"""The registrar (#308): proposed link pairs → clusters → registry writes."""

from argparse import Namespace

import duckdb
import pytest

from clearinghouse_core.job import JobContext
from clearinghouse_core.registry import (
    KIND_ORG,
    KIND_PERSON,
    KIND_ROLE,
    apply_decision,
    decide,
    registered_view,
)
from usa_wa_common.orgs import STRUCTURAL_ORGS
from usa_wa_pipeline.registrar import (
    _registrar_job,
    cluster_pairs,
    load_org_keys,
    load_pairs,
    load_role_keys,
    run_registrar,
    singleton_pairs,
    unprocessed_kinds,
)


def test_cluster_pairs_connected_components() -> None:
    clusters = cluster_pairs(
        [("a", "b"), ("b", "c"), ("x", "y"), ("solo", "solo")],
    )
    assert sorted(sorted(c) for c in clusters) == [["a", "b", "c"], ["solo"], ["x", "y"]]


@pytest.mark.db
async def test_registrar_mints_appends_and_reports_conflicts(db_session) -> None:
    # pre-register two distinct entities the conflict pair will collide across
    a = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:1"}), {}), registered_by="test"
    )
    b = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:2"}), {}), registered_by="test"
    )

    summary = await run_registrar(
        db_session,
        KIND_PERSON,
        pairs=[
            ("wsl:1", "wa_pdc:100"),  # append onto a
            ("new:1", "new:2"),  # mint
            ("wsl:1", "wsl:2"),  # conflict: a vs b
        ],
    )
    assert summary["appended_clusters"] == 0  # the conflict swallowed wsl:1's component
    assert summary["conflicts"] == 1
    view = await registered_view(db_session, KIND_PERSON)
    # conflict wrote nothing: the pdc key rode the conflicted component
    assert "wa_pdc:100" not in view
    assert view["wsl:1"] == a
    assert view["wsl:2"] == b
    assert view["new:1"] == view["new:2"]
    assert summary["minted"] == 1


@pytest.mark.db
async def test_registrar_clean_append(db_session) -> None:
    a = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:1"}), {}), registered_by="test"
    )
    summary = await run_registrar(db_session, KIND_PERSON, pairs=[("wsl:1", "wa_pdc:100")])
    assert summary["appended_clusters"] == 1
    assert (await registered_view(db_session, KIND_PERSON))["wa_pdc:100"] == a


@pytest.mark.db
async def test_registrar_idempotent(db_session) -> None:
    await run_registrar(db_session, KIND_PERSON, pairs=[("a:1", "b:2")])
    summary = await run_registrar(db_session, KIND_PERSON, pairs=[("a:1", "b:2")])
    assert summary["minted"] == 0
    assert summary["noops"] == 1


def test_load_pairs_filters_on_kind(tmp_path) -> None:
    """CR 20: an org rule unioned into proposed_links must never reach the
    person registration path — load_pairs reads one kind only."""
    db_path = str(tmp_path / "m.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        "create table proposed_links as select * from (values "
        "('person', 'a', 'b', 'r', 1.0), ('org', 'x', 'y', 'r', 1.0)"
        ") t(kind, left_key, right_key, rule, score)"
    )
    con.close()
    assert load_pairs(db_path) == [("a", "b")]
    assert load_pairs(db_path, "org") == [("x", "y")]


def test_unprocessed_kinds_names_what_the_job_skips(tmp_path) -> None:
    """CR 40: pairs of a kind the job does not register must degrade, not
    vanish silently."""
    db_path = str(tmp_path / "m.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        "create table proposed_links as select * from (values "
        "('person', 'a', 'b', 'r', 1.0), ('org', 'x', 'y', 'r', 1.0)"
        ") t(kind, left_key, right_key, rule, score)"
    )
    con.close()
    assert unprocessed_kinds(db_path) == ["org"]

    con = duckdb.connect(str(tmp_path / "p.duckdb"))
    con.execute(
        "create table proposed_links as select * from (values "
        "('person', 'a', 'b', 'r', 1.0)) t(kind, left_key, right_key, rule, score)"
    )
    con.close()
    assert unprocessed_kinds(str(tmp_path / "p.duckdb")) == []


def test_unprocessed_kinds_tolerates_a_null_kind(tmp_path) -> None:
    """CR 53: a NULL kind must be reported, not crash the sort (the dbt
    not_null test guards the nightly by ordering, but this is callable alone)."""
    db_path = str(tmp_path / "n.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        "create table proposed_links as select * from (values "
        "('person', 'a', 'b', 'r', 1.0), (NULL, 'x', 'y', 'r', 1.0), ('org', 'q', 'z', 'r', 1.0)"
        ") t(kind, left_key, right_key, rule, score)"
    )
    con.close()
    assert unprocessed_kinds(db_path) == ["<null>", "org"]


def test_role_natural_keys_are_read_from_the_conformed_dimension(tmp_path) -> None:
    """#313: roles register from the conformed `roles` model, not from
    `proposed_links` — there is no matching to propose. The natural key is
    `<source>:<role_key>`, the same shape persons and orgs use, so the seed's
    ULID-preserving pass and this ongoing pass address the same rows."""
    db_path = str(tmp_path / "r.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        "create table roles as select * from (values "
        "('seat:senate:ld-14'), ('party-role:democratic')) t(role_key)"
    )
    con.close()
    assert load_role_keys(db_path) == [
        "usa_wa_legislature:party-role:democratic",
        "usa_wa_legislature:seat:senate:ld-14",
    ]


async def test_the_registrar_mints_one_entity_per_role_key(db_session) -> None:
    """A singleton cluster per role: nothing to match, nothing to merge, so the
    decision table only ever mints or no-ops. Re-running must not double-mint —
    the role dimension is rebuilt from scratch on every pipeline run."""
    keys = ["usa_wa_legislature:seat:senate:ld-14", "usa_wa_legislature:party-role:democratic"]
    summary = await run_registrar(db_session, KIND_ROLE, pairs=singleton_pairs(keys))
    assert summary["minted"] == 2
    assert summary["conflicts"] == 0

    again = await run_registrar(db_session, KIND_ROLE, pairs=singleton_pairs(keys))
    assert again["minted"] == 0
    assert again["noops"] == 2

    view = await registered_view(db_session, KIND_ROLE)
    assert sorted(view) == sorted(keys)


def _pipeline_db(tmp_path, *, committees: list[str], meetings: list[str]) -> str:
    """A built-pipeline stand-in: the tables the registrar job reads."""
    db_path = str(tmp_path / "pipeline.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        "create table proposed_links (kind varchar, left_key varchar, right_key varchar, "
        "rule varchar, score double)"
    )
    con.execute("create table roles (role_key varchar)")
    con.execute("create table stg_wsl_committees (biennium varchar, committee_id varchar)")
    con.executemany(
        "insert into stg_wsl_committees values ('2025-26', ?)", [[c] for c in committees]
    )
    con.execute("create table stg_wsl_meetings (meeting_window varchar, committee_id varchar)")
    con.executemany(
        "insert into stg_wsl_meetings values ('2025-01-01:2026-12-31', ?)",
        [[m] for m in meetings],
    )
    con.close()
    return db_path


def test_org_natural_keys_are_every_staged_committee_plus_the_structural_orgs(tmp_path) -> None:
    """The org universe the canonical tier holds, derived in the pipeline:
    CommitteeService committees, the meeting-ref-only Joint/Other bodies no
    CommitteeService op carries (committee 36500 on 2026-09-22), and the
    synthesized structural orgs no wire carries at all."""
    db_path = _pipeline_db(tmp_path, committees=["28240", "31640"], meetings=["31640", "36500"])
    keys = load_org_keys(db_path)
    assert keys == sorted(
        {"usa_wa_legislature:28240", "usa_wa_legislature:31640", "usa_wa_legislature:36500"}
        | {f"usa_wa_legislature:{source_id}" for source_id in STRUCTURAL_ORGS}
    )


@pytest.mark.db
async def test_the_nightly_job_registers_a_new_org(db_session, tmp_path) -> None:
    """2026-09-22: a Joint committee first seen after the #308 seed stayed
    unregistered — the job had person and role passes and no org pass, so
    `parity-registry` failed the nightly on `org_missing=1`. Orgs register like
    roles: singleton clusters, mint once, no-op thereafter."""
    db_path = _pipeline_db(tmp_path, committees=["28240"], meetings=["36500"])
    ctx = JobContext(
        name="registrar",
        args=Namespace(db=db_path),
        session=db_session,
        session_factory=None,
        dry_run=False,
    )

    result = await _registrar_job(ctx)
    assert result.counters["org_minted"] == 2 + len(STRUCTURAL_ORGS)
    assert result.counters["conflicts"] == 0
    view = await registered_view(db_session, KIND_ORG)
    assert "usa_wa_legislature:36500" in view
    assert "usa_wa_legislature:28240" in view

    again = await _registrar_job(ctx)
    assert again.counters["org_minted"] == 0
    assert again.counters["org_noops"] == 2 + len(STRUCTURAL_ORGS)
