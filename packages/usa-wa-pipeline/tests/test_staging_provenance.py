"""Every staging row names the raw resource it came from (#313).

The citations artifact is a join from an entity back to the bytes that
attested it, and that join is only possible if the row remembers where it was
read. `source` + `resource_id` are the raw store's own coordinates — the pair
`stg_raw_fetches` keys on — so the chain is
``entity → staging row → resource → sha256`` with no guessing anywhere along
it.

One test per builder rather than a parametrized sweep: each builder derives
its resource id differently (a prefix strip, a parsed composite id, the
newest-revision pick), and a shared harness would hide exactly that.
"""

from clearinghouse_core.rawstore import RawStore
from usa_wa_pipeline.staging import pdc, roster, sos, wsl

BIENNIUM = "2025-26"


def _store(tmp_path, source: str, resources: dict[str, bytes]) -> RawStore:
    store = RawStore(tmp_path, source)
    run = store.open_run()
    for resource_id, body in resources.items():
        run.record(resource_id, body, url="u")
    run.close()
    return store


def _wsl_store(tmp_path, resources):
    return _store(tmp_path, "usa_wa_legislature", resources)


def test_committee_rows_carry_their_wire(tmp_path) -> None:
    store = _wsl_store(tmp_path, {f"committees-roster:{BIENNIUM}": b"w"})
    [row] = wsl.committee_rows(store, parse=lambda _: [{"Id": 1}])
    assert row["source"] == "usa_wa_legislature"
    assert row["resource_id"] == f"committees-roster:{BIENNIUM}"


def test_sponsor_rows_carry_their_wire(tmp_path) -> None:
    store = _wsl_store(tmp_path, {f"sponsors:{BIENNIUM}": b"w"})
    [row] = wsl.sponsor_rows(store, parse=lambda _: [{"Id": 27992}])
    assert (row["source"], row["resource_id"]) == ("usa_wa_legislature", f"sponsors:{BIENNIUM}")


def test_committee_member_rows_carry_their_wire(tmp_path) -> None:
    resource = f"committee-members-hist:{BIENNIUM}:1:House:Ag"
    store = _wsl_store(tmp_path, {resource: b"w"})
    [row] = wsl.committee_member_rows(store, parse=lambda _: [{"Id": 5}])
    assert (row["source"], row["resource_id"]) == ("usa_wa_legislature", resource)


def test_meeting_rows_carry_their_wire(tmp_path) -> None:
    resource = "committee-meetings:2025-01-01:2026-12-31"
    store = _wsl_store(tmp_path, {resource: b"w"})
    rows = wsl.meeting_rows(
        store, parse=lambda _: [{"Agency": "Joint", "Committees": {"Committee": {"Id": 7}}}]
    )
    assert [(r["source"], r["resource_id"]) for r in rows] == [("usa_wa_legislature", resource)]


def test_roster_rows_carry_the_revision_wire(tmp_path) -> None:
    store = _store(tmp_path, "usa_wa_legislature_roster", {"legroster:2025-06-05": b"pdf"})
    [row] = roster.roster_rows(store, parse=lambda _: [{"name": "A B", "year": 1937}])
    assert (row["source"], row["resource_id"]) == (
        "usa_wa_legislature_roster",
        "legroster:2025-06-05",
    )


def test_pdc_winner_rows_carry_their_cohort_wire(tmp_path) -> None:
    store = _store(tmp_path, "usa_wa_pdc", {"house-winners:2024": b"w"})
    rows = pdc.winner_rows(
        store, parse_house=lambda _: [{"person_id": "p1"}], parse_senate=lambda _: []
    )
    assert [(r["source"], r["resource_id"]) for r in rows] == [("usa_wa_pdc", "house-winners:2024")]


def test_sos_result_rows_carry_their_export(tmp_path) -> None:
    store = _store(tmp_path, "usa_wa_sos_results", {"sos-legresults:20241105": b"csv"})
    [row] = sos.result_rows(store, parse=lambda _: [{"Race": "LD 1 Rep"}])
    assert (row["source"], row["resource_id"]) == ("usa_wa_sos_results", "sos-legresults:20241105")


def test_sos_filing_rows_carry_their_export(tmp_path) -> None:
    store = _store(tmp_path, "usa_wa_sos", {"sos-whofiled:20240517": b"csv"})
    [row] = sos.filing_rows(store, parse=lambda _: [{"BallotName": "A B"}])
    assert (row["source"], row["resource_id"]) == ("usa_wa_sos", "sos-whofiled:20240517")


def test_provenance_columns_are_declared_last_by_every_builder() -> None:
    """Appended, never interleaved: the published staging schemas are additive
    (SCHEMA_VERSION minor), so a consumer reading by position is unmoved."""
    for columns in (
        wsl.COMMITTEE_COLUMNS,
        wsl.SPONSOR_COLUMNS,
        wsl.COMMITTEE_MEMBER_COLUMNS,
        wsl.MEETING_COLUMNS,
        roster.ROSTER_COLUMNS,
        pdc.WINNER_COLUMNS,
        sos.RESULT_COLUMNS,
        sos.FILING_COLUMNS,
    ):
        assert columns[-2:] == ["source", "resource_id"]
