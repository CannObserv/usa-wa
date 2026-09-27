"""The post-registrar registry-coverage probe (#412 PR B).

Three counters split out of ``parity_spans``, which PR E retires with the
canonical oracle it compares against. These three never needed that oracle:
each asks whether the REGISTRY, as the registrar has just left it, binds every
identity the build derives. They must not become in-build dbt tests — a new
legislator or committee is unregistered in the first build that sees it by
design, and a failed build never reaches the registrar that would register it.
"""

from argparse import Namespace
from datetime import date

import duckdb
import pandas as pd
import pytest

import usa_wa_pipeline
from clearinghouse_core.job import OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK, JobContext
from clearinghouse_domain_legislative.span_kinds import KIND_PARTY
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_pipeline.conformed.spans import ROSTER_SOURCE, SOURCE
from usa_wa_pipeline.registry_coverage import (
    GATED_COUNTERS,
    STAGING_INPUTS,
    _coverage_job,
    coverage_counters,
    load_staging,
    run_coverage,
)
from usa_wa_pipeline.staging.roster import ROSTER_SCHEMA
from usa_wa_pipeline.staging.sos import RESULT_SCHEMA
from usa_wa_pipeline.staging.wsl import COMMITTEE_MEMBER_SCHEMA, SPONSOR_SCHEMA

CURRENT = "2025-26"


def _span(member_id: str, kind: str = KIND_PARTY, discriminator: str = "democratic") -> TenureSpan:
    return TenureSpan(
        member_id=member_id,
        kind=kind,
        discriminator=discriminator,
        start_biennium="2023-24",
        end_biennium="2025-26",
        valid_from=date(2023, 1, 9),
        valid_to=None,
        is_active=True,
    )


#: A registry that binds everything `_span("100")` derives.
PERSONS = {f"{SOURCE}:100": "01PERSON"}
ORGS = {f"{SOURCE}:party-democratic": "01ORG"}
ROLES = {f"{SOURCE}:party-role:democratic": "01ROLE"}


def _counters(families, *, persons=PERSONS, orgs=ORGS, roles=ROLES) -> dict:
    return coverage_counters(families, persons=persons, orgs=orgs, roles=roles)


def test_a_fully_registered_build_counts_zero():
    counters = _counters({SOURCE: [_span("100")], ROSTER_SOURCE: []})
    assert {name: counters[name] for name in GATED_COUNTERS} == dict.fromkeys(GATED_COUNTERS, 0)
    assert (counters["spans"], counters["registered_spans"], counters["roles"]) == (1, 1, 1)


def test_an_unregistered_person_is_counted_not_dropped():
    """The `assignments` model's inner join drops this span silently (#403 class);
    here it is counted."""
    counters = _counters({SOURCE: [_span("100"), _span("200")], ROSTER_SOURCE: []})
    assert counters["unregistered_spans"] == 1
    assert counters["registered_spans"] == 1
    assert counters["unregistered_sample"]["persons"] == [f"{SOURCE}:200"]


def test_roles_are_derived_from_every_span_not_only_registered_ones():
    """A seat exists whether or not its holder is registered (CR 86): with no
    person key at all, the role is still derived, and still bound."""
    counters = _counters({SOURCE: [_span("100")], ROSTER_SOURCE: []}, persons={})
    assert counters["roles"] == 1
    assert counters["unregistered_roles"] == counters["unregistered_orgs"] == 0


def test_the_clip_residue_is_reported_beside_the_gate():
    """`seat_overlaps_unclipped` (#360) moved here from `parity_spans` too:
    reported, never gated."""
    counters = _counters({SOURCE: [_span("100")], ROSTER_SOURCE: []})
    assert counters["seat_overlaps_unclipped"] == 0
    assert "seat_overlaps_unclipped" not in GATED_COUNTERS


def test_the_two_families_resolve_in_their_own_identity_spaces():
    """A roster member id is a key in the roster source's space, never the WSL one."""
    counters = _counters({SOURCE: [], ROSTER_SOURCE: [_span("100")]})
    assert counters["unregistered_spans"] == 1


def test_an_unregistered_org_is_counted():
    assert _counters({SOURCE: [_span("100")], ROSTER_SOURCE: []}, orgs={})["unregistered_orgs"] == 1


def test_an_unregistered_role_is_counted():
    counters = _counters({SOURCE: [_span("100")], ROSTER_SOURCE: []}, roles={})
    assert counters["unregistered_roles"] == 1


def test_the_gated_counters_are_the_three_split_out_of_parity_spans():
    assert GATED_COUNTERS == ("unregistered_spans", "unregistered_orgs", "unregistered_roles")


# --- the build's own inputs, read back from the duckdb ------------------------


def _sponsor(member_id: str = "100") -> dict:
    return {
        "biennium": CURRENT,
        "member_id": member_id,
        "agency": "Senate",
        "name": "Dana Whitfield",
        "long_name": "Senator Whitfield",
        "first_name": "Dana",
        "last_name": "Whitfield",
        "party": "D",
        "district": "14",
    }


def _roster_row() -> dict:
    """A pre-1991 stranger to the sponsor corpus: keeps the roster tier present
    (the builder refuses an empty one) and mints one roster identity."""
    return {
        "district": 30,
        "chamber": "house",
        "year": 1925,
        "order": 1,
        "name": "Wilbur Cranston",
        "party_token": "R",
        "annotation": None,
    }


def _ballot_row() -> dict:
    return {
        "election_date": "20081104",
        "race": "Legislative District 41 - State Representative Pos. 1",
        "candidate": "Chris Vance",
        "party": "(Prefers Republican Party)",
        "votes": "30000",
    }


def _pipeline_db(tmp_path, *, sponsors=(), roster=(), sos=()) -> str:
    """A built-looking duckdb holding only the four staging tables the span build reads."""
    db_path = str(tmp_path / "pipeline.duckdb")
    con = duckdb.connect(db_path)
    for table, schema, rows in (
        ("stg_wsl_sponsors", SPONSOR_SCHEMA, sponsors),
        ("stg_wsl_committee_members", COMMITTEE_MEMBER_SCHEMA, ()),
        ("stg_roster_members", ROSTER_SCHEMA, roster),
        ("stg_sos_results", RESULT_SCHEMA, sos),
    ):
        columns = ", ".join(f'"{name}" {type_}' for name, type_ in schema.items())
        con.execute(f"create table {table} ({columns})")
        if rows:
            con.register(
                "frame", pd.DataFrame([{name: row.get(name) for name in schema} for row in rows])
            )
            con.execute(f"insert into {table} select * from frame")
            con.unregister("frame")
    con.close()
    return db_path


def test_load_staging_reads_the_tables_the_assignments_model_reads(tmp_path):
    db_path = _pipeline_db(tmp_path, sponsors=[_sponsor()], roster=[_roster_row()])
    staging = load_staging(db_path)
    assert set(staging) == set(STAGING_INPUTS)
    assert [row["member_id"] for row in staging["sponsors"]] == ["100"]
    assert [row["name"] for row in staging["roster"]] == ["Wilbur Cranston"]
    assert staging["committee_members"] == []


def test_the_staging_inputs_are_the_models_the_assignments_model_refs():
    """Same tables as the model, so the probe rebuilds what the build built."""
    model = (usa_wa_pipeline.PROJECT_DIR / "models" / "conformed" / "assignments.py").read_text()
    for table in STAGING_INPUTS.values():
        assert f'dbt.ref("{table}")' in model


# --- the gate ------------------------------------------------------------------


def _staging(**over) -> dict:
    staging = {
        "sponsors": [_sponsor()],
        "committee_members": [],
        "roster": [_roster_row()],
        "sos_results": [_ballot_row()],
    }
    staging.update(over)
    return staging


def _run(staging, *, persons=None, orgs=None, roles=None):
    return run_coverage(
        staging,
        events=[],
        persons=persons if persons is not None else {},
        orgs=orgs if orgs is not None else {},
        roles=roles if roles is not None else {},
        current_biennium=CURRENT,
    )


def test_an_empty_registry_fails_and_names_what_tripped():
    result = _run(_staging())
    assert result.outcome == OUTCOME_FAILED
    assert result.exit_code == 1
    assert set(result.counters["integrity_failures"]) == set(GATED_COUNTERS)


def test_a_registry_binding_everything_passes():
    """Bind every key the fixture derives — read off a failing run, so the test
    states no key shape of its own."""
    staging = _staging()
    first = _run(staging)
    assert first.outcome == OUTCOME_FAILED
    keys = first.counters["unregistered_sample"]
    result = _run(
        staging,
        persons=dict.fromkeys(keys["persons"], "01P"),
        orgs=dict.fromkeys(keys["orgs"], "01O"),
        roles=dict.fromkeys(keys["roles"], "01R"),
    )
    assert result.outcome == OUTCOME_OK, result.counters
    assert result.counters["integrity_failures"] == []


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"sponsors": []}, "empty_sponsors"),
        ({"roster": []}, "empty_roster_rows"),
        ({"sos_results": []}, "empty_sos_rows"),
    ],
)
def test_a_build_missing_an_input_degrades_rather_than_passing(over, reason):
    """A probe that compared nothing must say so: an empty input would read as
    zero unregistered identities."""
    result = _run(_staging(**over))
    assert result.outcome == OUTCOME_DEGRADED
    assert result.counters[reason] is True


# --- the job, against the test database's (empty) registry --------------------


@pytest.mark.db
async def test_the_job_reads_the_build_and_the_registry(db_session, tmp_path):
    db_path = _pipeline_db(
        tmp_path, sponsors=[_sponsor()], roster=[_roster_row()], sos=[_ballot_row()]
    )
    ctx = JobContext(
        name="registry-coverage",
        args=Namespace(db=db_path),
        session=db_session,
        session_factory=None,
        dry_run=False,
    )
    result = await _coverage_job(ctx)
    assert result.outcome == OUTCOME_FAILED
    assert result.counters["unregistered_spans"] == result.counters["spans"] > 0
