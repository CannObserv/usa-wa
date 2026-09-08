"""The PM anchor export (#312): base32 crosswalk seed for power-map cutover."""

import csv
import io
import json
from pathlib import Path

import duckdb
import pytest
from ulid import ULID

from clearinghouse_domain_legislative.identity import Person
from usa_wa_pipeline.anchor_export import (
    ANCHOR_COLUMNS,
    ANCHOR_TABLE,
    export_anchors,
    materialize_anchors,
    pipeline_db_path,
)


@pytest.mark.db
async def test_exports_base32_pairs_with_manifest(db_session, tmp_path) -> None:
    pm_id = ULID()
    anchored = Person(source="usa_wa_legislature", source_id="1", name_full="A", pm_person_id=pm_id)
    unanchored = Person(source="usa_wa_legislature", source_id="2", name_full="B")
    db_session.add_all([anchored, unanchored])
    await db_session.flush()

    summary = await export_anchors(db_session, tmp_path)
    assert summary["person"] == 1
    assert summary["organization"] == 0

    rows = list(csv.DictReader(io.StringIO((tmp_path / "anchors.csv").read_text())))
    [row] = rows
    assert row["kind"] == "person"
    assert row["usa_wa_id"] == str(anchored.id)
    assert row["pm_id"] == str(pm_id)
    # the PM-side hard requirement: 26-char Crockford base32, never UUID-hex
    assert len(row["pm_id"]) == 26
    assert "-" not in row["pm_id"]

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["counts"]["person"] == 1
    assert manifest["sha256"]


def _anchors_csv(tmp_path, *rows: tuple[str, str, str]):
    path = tmp_path / "anchors.csv"
    body = "".join(f"{kind},{local},{pm}\n" for kind, local, pm in rows)
    path.write_text("kind,usa_wa_id,pm_id\n" + body)
    return path


def test_materialize_loads_the_export_as_a_publishable_table(tmp_path) -> None:
    """#354: the publisher reads duckdb tables, so delivery starts by being one."""
    csv_path = _anchors_csv(tmp_path, ("person", "01A", "01B"), ("role", "01C", "01D"))
    db = tmp_path / "pipeline.duckdb"

    assert materialize_anchors(csv_path, db) == 2

    con = duckdb.connect(str(db))
    try:
        columns = [row[0] for row in con.execute(f'describe "{ANCHOR_TABLE}"').fetchall()]
        assert columns == list(ANCHOR_COLUMNS)
        assert con.execute(f'select * from "{ANCHOR_TABLE}" order by all').fetchall() == [
            ("person", "01A", "01B"),
            ("role", "01C", "01D"),
        ]
    finally:
        con.close()


def test_materialize_replaces_rather_than_appends(tmp_path) -> None:
    """A re-export is a REPLACEMENT — the table is the whole live crosswalk, so a
    second run must not double it (retraction-as-absence, the #302 contract)."""
    db = tmp_path / "pipeline.duckdb"
    materialize_anchors(_anchors_csv(tmp_path, ("person", "01A", "01B")), db)
    rows = materialize_anchors(_anchors_csv(tmp_path, ("person", "01A", "01B")), db)

    assert rows == 1
    con = duckdb.connect(str(db))
    try:
        assert con.execute(f'select count(*) from "{ANCHOR_TABLE}"').fetchone()[0] == 1
    finally:
        con.close()


def test_materialize_keeps_all_digit_ulids_as_text(tmp_path) -> None:
    """The PM encoding gotcha, one layer down: a ULID that happens to be all
    digits must stay a 26-char string. Left to duckdb's CSV sniffer it becomes a
    numeric column, and the id PM is handed no longer resolves."""
    numeric = "01234567890123456789012345"
    csv_path = _anchors_csv(tmp_path, ("person", numeric, numeric))
    db = tmp_path / "pipeline.duckdb"

    materialize_anchors(csv_path, db)

    con = duckdb.connect(str(db))
    try:
        types = {row[0]: row[1] for row in con.execute(f'describe "{ANCHOR_TABLE}"').fetchall()}
        assert set(types.values()) == {"VARCHAR"}
        [(local, pm)] = con.execute(f'select usa_wa_id, pm_id from "{ANCHOR_TABLE}"').fetchall()
        assert local == numeric and pm == numeric
    finally:
        con.close()


def test_pipeline_db_default_matches_the_publisher(monkeypatch) -> None:
    """Export and publish must name the SAME duckdb. If they drift, the export
    writes a table the publisher never sees and the catalog silently stops
    carrying the crosswalk."""
    monkeypatch.delenv("USA_WA_PIPELINE_DB", raising=False)
    assert pipeline_db_path(None) == Path("data/pipeline.duckdb")

    monkeypatch.setenv("USA_WA_PIPELINE_DB", "/srv/other.duckdb")
    assert pipeline_db_path(None) == Path("/srv/other.duckdb")
    assert pipeline_db_path("/explicit.duckdb") == Path("/explicit.duckdb")
