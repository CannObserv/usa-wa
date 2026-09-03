"""PDC + SOS staging row-builders (#307). Parsers injected, as in the WSL suite."""

from clearinghouse_core.rawstore import RawStore
from usa_wa_pipeline.staging import pdc, sos


def _store(tmp_path, slug: str, resources: dict[str, bytes]) -> RawStore:
    store = RawStore(tmp_path, slug)
    run = store.open_run()
    for rid, body in resources.items():
        run.record(rid, body, url="u")
    run.close()
    return store


def test_pdc_winner_rows_carry_chamber_and_year_from_resource(tmp_path) -> None:
    store = _store(
        tmp_path, "usa_wa_pdc", {"house-winners:2024": b"h", "senate-winners:2022": b"s"}
    )

    def parse(wire: bytes):
        return [
            {
                "person_id": "7710",
                "filer_id": "WHITD--123",
                "filer_name": "WHITFIELD DANA",
                "party": "DEMOCRAT",
                "legislative_district": "14",
                "office": "STATE REPRESENTATIVE",
                "general_election_status": "Won in general",
                "candidacy_id": "99",
            }
        ]

    rows = pdc.winner_rows(store, parse_house=parse, parse_senate=parse)
    assert {(r["chamber"], r["election_year"]) for r in rows} == {
        ("house", 2024),
        ("senate", 2022),
    }
    row = rows[0]
    assert row["person_id"] == "7710"
    assert row["filer_id"] == "WHITD--123"
    assert row["legislative_district"] == "14"


def test_sos_result_rows(tmp_path) -> None:
    store = _store(tmp_path, "usa_wa_sos_results", {"sos-legresults:20241105": b"w"})

    def parse(wire: bytes):
        return [
            {
                "Race": "Legislative District 14 - State Representative Pos. 1",
                "Candidate": "Dana Whitfield",
                "Party": "(Prefers Democratic Party)",
                "Votes": "26583",
                "PercentageOfTotalVotes": "58.05",
                "JurisdictionName": "Legislative",
            }
        ]

    [row] = sos.result_rows(store, parse=parse)
    assert row["election_date"] == "20241105"
    assert row["race"].endswith("Pos. 1")
    assert row["candidate"] == "Dana Whitfield"
    assert row["votes"] == "26583"


def test_sos_filing_rows(tmp_path) -> None:
    store = _store(tmp_path, "usa_wa_sos", {"sos-whofiled:20241105": b"w"})

    def parse(wire: bytes):
        return [
            {
                "BallotName": "Dana Whitfield",
                "PartyName": "Democratic",
                "RaceName": "State Representative Pos. 1",
                "RaceJurisdictionName": "Legislative District 14",
            }
        ]

    [row] = sos.filing_rows(store, parse=parse)
    assert row["election_date"] == "20241105"
    assert row["ballot_name"] == "Dana Whitfield"
    assert row["race_jurisdiction_name"] == "Legislative District 14"


def test_empty_stores_yield_no_rows(tmp_path) -> None:
    assert pdc.winner_rows(RawStore(tmp_path, "usa_wa_pdc")) == []
    assert sos.result_rows(RawStore(tmp_path, "usa_wa_sos_results")) == []
    assert sos.filing_rows(RawStore(tmp_path, "usa_wa_sos")) == []
