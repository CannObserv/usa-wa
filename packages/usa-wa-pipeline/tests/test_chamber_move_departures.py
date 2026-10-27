"""The chamber-move departures probe (#518).

``departed`` is person-scoped: it closes every open span. Dated at a move to the
other chamber, it ends the new tenure where it began. A move is ``vacated`` on the
old seat (plus ``seated`` on the new one), per ONTOLOGY.md § 3. #514's Hurley event
was this shape and only a manual sweep caught it; the probe is that sweep, nightly.
"""

from argparse import Namespace
from dataclasses import replace
from datetime import date

import duckdb
import pandas as pd
import pytest

from clearinghouse_core.job import OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK, JobContext
from clearinghouse_domain_legislative.operator_events import OperatorEvent
from usa_wa_pipeline.chamber_move_departures import (
    ACCEPTED,
    Acceptance,
    _probe_job,
    evidence_windows,
    find_hits,
    load_staging,
    roster_keys,
    run_probe,
)
from usa_wa_pipeline.conformed.spans import ROSTER_SOURCE, SOURCE
from usa_wa_pipeline.operator_read import EventRow
from usa_wa_pipeline.staging.roster import ROSTER_SCHEMA
from usa_wa_pipeline.staging.wsl import SPONSOR_SCHEMA

ROSTER_NS = ROSTER_SOURCE
WSL_NS = SOURCE


def _row(name: str, chamber: str, year: int, district: int = 3) -> dict:
    return {
        "district": district,
        "chamber": chamber,
        "year": year,
        "order": 1,
        "name": name,
        "party_token": "D",
        "annotation": None,
    }


def _sponsor(member_id: str, biennium: str, agency: str, district: str = "1") -> dict:
    return {
        "biennium": biennium,
        "member_id": member_id,
        "agency": agency,
        "name": "Derek Stanford",
        "long_name": f"{agency} Stanford",
        "first_name": "Derek",
        "last_name": "Stanford",
        "party": "D",
        "district": district,
    }


def _departed(member_id: str, when: date) -> EventRow:
    return EventRow(member_id, "departed", when, None, None)


#: Margaret Hurley (#514): House 1953-79, resigned Nov. 28, 1979 INTO the Senate,
#: whose 1977 row (a mid-term successor, keyed to its group year) covers the date.
HURLEY = [
    _row("Margaret Hurley", "house", 1953),
    _row("Margaret Hurley", "house", 1979),
    _row("Margaret Hurley", "senate", 1977),
]


def _hits(roster=(), sponsors=(), events=(), entity_by_key=None):
    windows = evidence_windows(list(roster), list(sponsors))
    return find_hits(list(events), windows, entity_by_key or {})


# --- roster keys -----------------------------------------------------------


def test_a_roster_row_keys_by_its_fold_and_the_groups_first_year():
    """The registry's roster key (`<fold>:<first-session-year>`, identity.py `mint`)."""
    assert roster_keys(HURLEY) == [f"{ROSTER_NS}:margarethurley:1953"] * 3


def test_a_spelling_variant_keys_under_its_canonical_fold():
    """IDENTITY_ALIASES: one LD27 tenure printed two ways is one person."""
    rows = [_row("Frank G. Marzano", "house", 1965), _row("Frank Marzano", "house", 1969)]
    assert roster_keys(rows) == [f"{ROSTER_NS}:frankmarzano:1965"] * 2


def test_an_adjudicated_split_keys_each_part_by_its_own_first_year():
    """IDENTITY_SPLITS: the 1899 and the 1947 Elmer E. Johnston are two people."""
    rows = [_row("Elmer E. Johnston", "house", 1899), _row("Elmer E. Johnston", "house", 1951)]
    assert roster_keys(rows) == [
        f"{ROSTER_NS}:elmerejohnston:1899",
        f"{ROSTER_NS}:elmerejohnston:1951",
    ]


def test_a_widow_split_keys_her_rows_apart_from_his_in_their_shared_year():
    """IDENTITY_WIDOW_SPLITS (#525): his death-dated 1943 row stays his; her `Mrs.` rows
    key under her own first year. The probe reads the resolver's rule, not a copy."""
    rows = [
        _row("Jurie B. Smith", "house", 1941, 32),
        _row("Jurie B. Smith", "house", 1943, 32),
        _row("Mrs. Jurie B. Smith", "house", 1943, 32),
        _row("Mrs. Jurie B.(Nettie Luella) Smith", "house", 1945, 32),
    ]
    assert roster_keys(rows) == [
        f"{ROSTER_NS}:juriebsmith:1941",
        f"{ROSTER_NS}:juriebsmith:1941",
        f"{ROSTER_NS}:juriebsmith:1943",
        f"{ROSTER_NS}:juriebsmith:1943",
    ]


def test_a_nameless_row_has_no_key():
    assert roster_keys([{**_row("x", "house", 1953), "name": None}]) == [None]


# --- the check -------------------------------------------------------------


def test_a_departure_into_the_other_chamber_is_a_hit():
    """The Hurley shape: House and Senate rows both cover the date."""
    hits, unmapped = _hits(
        roster=HURLEY, events=[_departed("margarethurley:1953", date(1979, 11, 28))]
    )
    assert unmapped == []
    assert [(h.member_id, h.effective_date, h.chambers) for h in hits] == [
        ("margarethurley:1953", date(1979, 11, 28), ("house", "senate"))
    ]
    assert "roster senate 1977" in hits[0].evidence


def test_a_departure_from_one_chamber_is_not_a_hit():
    roster = [_row("Ada Lund", "senate", 1957), _row("Ada Lund", "house", 1951)]
    hits, unmapped = _hits(roster=roster, events=[_departed("adalund:1951", date(1958, 6, 1))])
    assert (hits, unmapped) == ([], [])


def test_a_return_after_the_term_is_not_a_hit():
    """The other chamber's window opens after the date: a departure, then a return."""
    roster = [_row("Ada Lund", "house", 1951), _row("Ada Lund", "senate", 1955)]
    hits, _ = _hits(roster=roster, events=[_departed("adalund:1951", date(1952, 3, 1))])
    assert hits == []


def test_only_departed_events_are_checked():
    """`vacated` is the RIGHT shape for a move; `seated` opens rather than closes."""
    events = [
        EventRow("margarethurley:1953", "vacated", date(1979, 11, 28), "chamber-house", "3"),
        EventRow("margarethurley:1953", "seated", date(1979, 11, 28), "chamber-senate", "3"),
    ]
    assert _hits(roster=HURLEY, events=events) == ([], [])


def test_sponsor_rows_are_evidence_for_a_wsl_member():
    """Derek Stanford (#363): both chambers' sponsor rows in 2019-20, departed July 1."""
    sponsors = [_sponsor("15809", "2019-20", "House"), _sponsor("15809", "2019-20", "Senate")]
    hits, _ = _hits(sponsors=sponsors, events=[_departed("15809", date(2019, 7, 1))])
    assert [h.chambers for h in hits] == [("house", "senate")]
    assert "sponsors senate 2019-20" in hits[0].evidence


def test_a_move_at_a_term_boundary_is_a_hit():
    """The legislature is sworn in mid-January, so the outgoing term's window runs
    through January 31: a House member moving to the Senate at the boundary, with
    the move recorded as `departed` on Jan 14, closes the new Senate span two
    weeks after its Jan 1 floor."""
    sponsors = [_sponsor("7", "2017-18", "House"), _sponsor("7", "2019-20", "Senate")]
    hits, _ = _hits(sponsors=sponsors, events=[_departed("7", date(2019, 1, 14))])
    assert [h.chambers for h in hits] == [("house", "senate")]


def test_the_grace_ends_with_january():
    sponsors = [_sponsor("7", "2017-18", "House"), _sponsor("7", "2019-20", "Senate")]
    assert _hits(sponsors=sponsors, events=[_departed("7", date(2019, 2, 1))])[0] == []


def test_roster_rows_reach_a_wsl_member_through_the_registry():
    """A crossing member's pre-1991 roster rows and WSL sponsor rows are one person
    only by the registry, which binds both keys to one entity."""
    roster = [_row("Linda A. Smith", "house", 1983, district=18)]
    sponsors = [_sponsor("403", "1983-84", "Senate", district="18")]
    entity = {f"{ROSTER_NS}:lindaasmith:1983": "01E", f"{WSL_NS}:403": "01E"}
    hits, _ = _hits(
        roster=roster,
        sponsors=sponsors,
        events=[_departed("403", date(1984, 2, 1))],
        entity_by_key=entity,
    )
    assert [h.chambers for h in hits] == [("house", "senate")]


def test_an_unregistered_roster_key_stands_as_its_own_identity():
    """The registry binds no key for a roster-only person until the registrar or
    an adjudication does; the event and the rows still share the key."""
    hits, unmapped = _hits(
        roster=HURLEY,
        events=[_departed("margarethurley:1953", date(1979, 11, 28))],
        entity_by_key={},
    )
    assert (len(hits), unmapped) == (1, [])


def test_a_departure_with_no_evidence_is_unmapped_not_passed():
    hits, unmapped = _hits(roster=HURLEY, events=[_departed("99999", date(2001, 1, 1))])
    assert (hits, unmapped) == ([], ["99999"])


# --- the gate --------------------------------------------------------------

STAGING = {"roster": HURLEY, "sponsors": [_sponsor("15809", "2019-20", "House")]}
HURLEY_EVENT = _departed("margarethurley:1953", date(1979, 11, 28))


def _probe(events, accepted=(), staging=STAGING):
    return run_probe(staging, events=events, entity_by_key={}, accepted=accepted)


def test_an_unlisted_hit_fails_and_names_the_member():
    result = _probe([HURLEY_EVENT])
    assert result.outcome == OUTCOME_FAILED
    assert result.exit_code == 1
    assert result.counters["failures"] == ["unaccepted_hits"]
    assert [h["member_id"] for h in result.counters["unaccepted_hits"]] == ["margarethurley:1953"]


def test_an_accepted_hit_passes_and_is_reported():
    accepted = (Acceptance("margarethurley:1953", date(1979, 11, 28), "test", "p.26"),)
    result = _probe([HURLEY_EVENT], accepted=accepted)
    assert result.outcome == OUTCOME_OK
    assert result.counters["accepted"] == ["margarethurley:1953 1979-11-28"]


def test_a_stale_acceptance_fails():
    """An entry no live hit matches — its event retracted, or the rows changed — is
    a claim nobody re-checks; it must be removed (the #452/#360 pattern)."""
    accepted = (Acceptance("margarethurley:1953", date(1979, 11, 28), "test", "p.26"),)
    house_only = replace(HURLEY_EVENT, effective_date=date(1954, 6, 1))
    result = _probe([house_only], accepted=accepted)
    assert result.outcome == OUTCOME_FAILED
    assert result.counters["failures"] == ["stale_acceptances"]
    assert result.counters["stale_acceptances"] == ["margarethurley:1953 1979-11-28"]


def test_a_clean_run_counts_what_it_compared():
    other = _departed("15809", date(2019, 7, 1))
    result = _probe([replace(HURLEY_EVENT, kind="seated"), other])
    assert result.outcome == OUTCOME_OK
    counters = result.counters
    assert (counters["departures"], counters["unmapped"], counters["hits"]) == (1, 0, 0)


@pytest.mark.parametrize(
    ("staging", "events", "reason"),
    [
        ({**STAGING, "roster": []}, [HURLEY_EVENT], "empty_roster_rows"),
        ({**STAGING, "sponsors": []}, [HURLEY_EVENT], "empty_sponsors"),
        (STAGING, [], "no_departures"),
    ],
)
def test_an_empty_input_degrades_rather_than_passing(staging, events, reason):
    """Each would read as "no hits" having compared nothing."""
    result = run_probe(staging, events=events, entity_by_key={}, accepted=())
    assert result.outcome == OUTCOME_DEGRADED
    assert result.counters == {reason: True}


def test_the_shipped_acceptances_are_514s_two_false_positives():
    """Measured 2026-10-19 over the 304 current departures: exactly these two."""
    assert {(a.member_id, a.effective_date) for a in ACCEPTED} == {
        ("charleslklinefelter:1941", date(1943, 11, 22)),
        ("elmerchuntley:1957", date(1965, 3, 26)),
    }
    assert all(a.reason and a.evidence for a in ACCEPTED)


# --- the job ---------------------------------------------------------------


def _pipeline_db(tmp_path, *, roster=(), sponsors=()) -> str:
    db_path = str(tmp_path / "pipeline.duckdb")
    con = duckdb.connect(db_path)
    for table, schema, rows in (
        ("stg_roster_members", ROSTER_SCHEMA, roster),
        ("stg_wsl_sponsors", SPONSOR_SCHEMA, sponsors),
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


def test_load_staging_reads_the_two_staging_tables(tmp_path):
    db_path = _pipeline_db(tmp_path, roster=HURLEY, sponsors=STAGING["sponsors"])
    staging = load_staging(db_path)
    assert (len(staging["roster"]), len(staging["sponsors"])) == (3, 1)
    assert staging["roster"][0]["name"] == "Margaret Hurley"


@pytest.mark.db
async def test_the_job_reads_the_build_and_the_live_events(db_session, tmp_path):
    db_session.add(
        OperatorEvent(
            source_id="518-hurley",
            member_id="margarethurley:1953",
            kind="departed",
            reason="resigned",
            evidence_url="https://example.test/p26",
            entered_by="test",
            effective_date=date(1979, 11, 28),
        )
    )
    await db_session.flush()
    db_path = _pipeline_db(tmp_path, roster=HURLEY, sponsors=STAGING["sponsors"])
    ctx = JobContext(
        name="chamber-move-departures",
        args=Namespace(db=db_path),
        session=db_session,
        session_factory=None,
        dry_run=False,
    )
    result = await _probe_job(ctx)
    assert result.outcome == OUTCOME_FAILED
    assert [h["member_id"] for h in result.counters["unaccepted_hits"]] == ["margarethurley:1953"]
    # The shipped acceptances name events this test database does not hold.
    assert len(result.counters["stale_acceptances"]) == len(ACCEPTED)
