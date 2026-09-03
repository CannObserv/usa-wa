"""The PM anchor export (#312): base32 crosswalk seed for power-map cutover."""

import csv
import io
import json

import pytest
from ulid import ULID

from clearinghouse_domain_legislative.identity import Person
from usa_wa_pipeline.anchor_export import export_anchors

pytestmark = pytest.mark.db


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
