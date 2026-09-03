"""Roster-PDF staging row-builder (#306): the newest archived revision only."""

from datetime import UTC, datetime

from clearinghouse_core.rawstore import RawStore
from usa_wa_pipeline.staging import roster


def test_roster_rows_parse_newest_revision_only(tmp_path) -> None:
    store = RawStore(tmp_path, "usa_wa_legislature_roster")
    run = store.open_run()
    run.record(
        "legroster:2024-01", b"old-pdf", url="u", fetched_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    run.record(
        "legroster:2025-08", b"new-pdf", url="u", fetched_at=datetime(2025, 8, 1, tzinfo=UTC)
    )
    run.close()

    parsed: list[bytes] = []

    def parse(wire: bytes):
        parsed.append(wire)
        return [
            {
                "district": 14,
                "chamber": "House",
                "year": 2025,
                "order": 1,
                "name": "Dana Whitfield",
                "party_token": "D",
                "annotation": None,
            }
        ]

    rows = roster.roster_rows(store, parse=parse)
    assert parsed == [b"new-pdf"]
    [row] = rows
    assert row["revision"] == "2025-08"
    assert row["district"] == 14
    assert row["chamber"] == "House"
    assert row["year"] == 2025
    assert row["order"] == 1
    assert row["name"] == "Dana Whitfield"
    assert row["party_token"] == "D"


def test_roster_rows_empty_store(tmp_path) -> None:
    store = RawStore(tmp_path, "usa_wa_legislature_roster")
    assert roster.roster_rows(store, parse=lambda wire: []) == []
