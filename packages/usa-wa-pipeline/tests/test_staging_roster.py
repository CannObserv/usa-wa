"""Roster-PDF staging row-builder (#306): the newest archived revision only."""

from datetime import UTC, datetime

import yaml

from clearinghouse_core.rawstore import RawStore
from usa_wa_pipeline import PROJECT_DIR
from usa_wa_pipeline.conformed.spans import roster_records
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


def _schema_not_null(model: str) -> set[str]:
    """The columns ``models/staging/schema.yml`` gates ``not_null`` on ``model``."""
    schema = yaml.safe_load((PROJECT_DIR / "models" / "staging" / "schema.yml").read_text())
    [entry] = [m for m in schema["models"] if m["name"] == model]
    return {
        column["name"]
        for column in entry.get("columns", [])
        if "not_null" in (column.get("data_tests") or [])
    }


def test_every_column_the_resolve_needs_is_gated_not_null():
    """``malformed_roster_rows`` as an in-build dbt test (#412 PR B).

    ``conformed.spans.roster_records`` drops a row it cannot convert, and
    ``parity_spans`` gated that count at zero from outside the build. In-build,
    the same guard is a ``not_null`` on every column whose null the resolve
    drops. Derived by nulling each column in turn (``None`` and the ``NaN`` a
    BIGINT null becomes in a frame), never by listing them here, so a column
    the resolve starts to need fails this until the schema gates it.
    """
    row = {
        "revision": "2025-08",
        "district": 14,
        "chamber": "House",
        "year": 2025,
        "order": 1,
        "name": "Dana Whitfield",
        "party_token": "D",
        "annotation": None,
        "source": "usa_wa_legislature_roster",
        "resource_id": "legroster:2025-08",
    }
    assert set(row) == set(roster.ROSTER_COLUMNS)
    assert len(roster_records([row])) == 1
    needed = {
        column
        for column in roster.ROSTER_COLUMNS
        for null in (None, float("nan"))
        if not roster_records([{**row, column: null}])
    }
    assert needed, "nulling nothing drops a row — the probe above proves nothing"
    assert needed <= _schema_not_null("stg_roster_members")
