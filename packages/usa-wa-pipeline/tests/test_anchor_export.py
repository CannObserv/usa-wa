"""The PM anchor export (#312): base32 crosswalk seed for power-map cutover."""

import csv
import hashlib
import io
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from ulid import ULID

from clearinghouse_domain_legislative.identity import Person
from usa_wa_pipeline import anchor_export, publish
from usa_wa_pipeline.anchor_export import (
    ANCHOR_COLUMNS,
    ANCHOR_TABLE,
    export_anchors,
    materialize_anchors,
    write_export,
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


ROWS = [("person", "01A", "01P"), ("role", "01C", "01D")]


def _table(db):
    con = duckdb.connect(str(db))
    try:
        columns = [row[0] for row in con.execute(f'describe "{ANCHOR_TABLE}"').fetchall()]
        types = [row[1] for row in con.execute(f'describe "{ANCHOR_TABLE}"').fetchall()]
        rows = con.execute(f'select * from "{ANCHOR_TABLE}" order by all').fetchall()
    finally:
        con.close()
    return columns, types, rows


def test_materialize_builds_the_table_from_rows(tmp_path) -> None:
    """#354 CR 112: the published table is built from the rows in hand, not by
    re-reading the local `--out` artifact. The two sinks stay independent, so
    retiring the `data/anchor-export/` tree is a deletion and not a rewrite."""
    db = tmp_path / "pipeline.duckdb"

    assert materialize_anchors(ROWS, db) == 2

    columns, _, rows = _table(db)
    assert columns == list(ANCHOR_COLUMNS)
    assert rows == [("person", "01A", "01P"), ("role", "01C", "01D")]


def test_materialize_replaces_rather_than_appends(tmp_path) -> None:
    """A re-export is a REPLACEMENT — the table is the whole live crosswalk, so a
    second run must not double it (retraction-as-absence, the #302 contract)."""
    db = tmp_path / "pipeline.duckdb"
    materialize_anchors(ROWS, db)

    assert materialize_anchors(ROWS, db) == 2
    assert len(_table(db)[2]) == 2


def test_materialize_types_every_column_as_text(tmp_path) -> None:
    """The PM encoding gotcha, one layer down: a ULID that happens to be all
    digits must stay a 26-char string, never a numeric column."""
    numeric = "01234567890123456789012345"
    db = tmp_path / "pipeline.duckdb"

    materialize_anchors([("person", numeric, numeric)], db)

    _, types, rows = _table(db)
    assert set(types) == {"VARCHAR"}
    assert rows == [("person", numeric, numeric)]


def test_both_sinks_take_their_column_order_from_one_constant(tmp_path) -> None:
    """#354 CR 109: the CSV and the published table must not be able to disagree
    about which id is which. duckdb maps an explicit `columns=` spec POSITIONALLY
    and ignores header names, so when the two sinks were wired independently a
    reordered writer would have silently swapped usa_wa_id and pm_id — both are
    26-char base32, so every shape check downstream still passes."""
    out = tmp_path / "export"
    db = tmp_path / "pipeline.duckdb"

    write_export(ROWS, out)
    materialize_anchors(ROWS, db)

    reader = csv.DictReader(io.StringIO((out / "anchors.csv").read_text()))
    assert reader.fieldnames == list(ANCHOR_COLUMNS)
    columns, _, table_rows = _table(db)
    assert columns == list(ANCHOR_COLUMNS)

    # the same logical row, read back through each sink, agrees field by field
    from_csv = [tuple(row[column] for column in ANCHOR_COLUMNS) for row in reader]
    assert sorted(from_csv) == sorted(table_rows) == sorted(ROWS)


def test_write_export_counts_per_kind(tmp_path) -> None:
    """`manifest.json` is the only place per-kind counts survive once the catalog
    carries a single `rows` total, so they are computed, not assumed."""
    counts = write_export([*ROWS, ("person", "01B", "01Q")], tmp_path / "export")

    assert counts == {"person": 2, "organization": 0, "role": 1, "assignment": 0}
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    assert manifest["counts"] == counts
    assert manifest["sha256"]


def test_export_and_publish_share_one_db_resolver() -> None:
    """#354 CR 110: export and publish must name the SAME duckdb. Asserting two
    copies of a literal agree is not that assertion — it passes while they drift.
    One resolver, referenced by both, is."""
    assert anchor_export.pipeline_db_path is publish.pipeline_db_path

    monkey = publish.pipeline_db_path
    assert monkey(None) == Path("data/pipeline.duckdb")
    assert monkey("/explicit.duckdb") == Path("/explicit.duckdb")


def test_materialize_of_an_empty_crosswalk_declares_the_columns(tmp_path) -> None:
    """The #314 shape: once the pm_* columns are dropped the export goes empty.
    That must land as an empty TABLE — which the publisher's shrink gate refuses
    as a 100% contraction — not as a missing one, which reads as a build failure
    and refuses the whole catalog for a different, misleading reason."""
    db = tmp_path / "pipeline.duckdb"

    assert materialize_anchors([], db) == 0

    columns, types, rows = _table(db)
    assert columns == list(ANCHOR_COLUMNS)
    assert set(types) == {"VARCHAR"}
    assert rows == []


def test_materialize_rejects_a_row_that_does_not_match_the_columns(tmp_path) -> None:
    """A short/long row is a caller bug, not something to pad or truncate."""
    with pytest.raises(ValueError, match="does not match columns"):
        materialize_anchors([("person", "01A")], tmp_path / "pipeline.duckdb")


def test_local_export_is_byte_identical_to_the_published_csv(tmp_path) -> None:
    """One dataset, one sha256. The local `--out` artifact and the published
    `data.csv` are two deliveries of the same rows, so a client cross-checking
    `manifest.json` against the catalog entry must not see two digests and be
    left unable to tell a serialisation difference from corruption.

    Both halves are load-bearing: `csv`'s excel dialect writes CRLF where
    duckdb's COPY writes LF (one byte per row — 12,462 on the real export), and
    the publisher orders `by all` where a kind-grouped read does not."""
    rows = [
        ("person", "01B", "01Q"),
        ("assignment", "01A", "01P"),
        ("role", "01C", "01D"),
        ("organization", "01E", "01F"),
    ]
    write_export(rows, tmp_path / "export")
    ours = (tmp_path / "export" / "anchors.csv").read_bytes()

    # exactly how publish.py materialises a dataset: order by all, header, comma
    con = duckdb.connect()
    try:
        con.register("frame", pd.DataFrame(rows, columns=list(ANCHOR_COLUMNS), dtype="string"))
        con.execute(f'create or replace table "{ANCHOR_TABLE}" as select * from frame')
        published = tmp_path / "published.csv"
        con.execute(
            f'copy (select * from "{ANCHOR_TABLE}" order by all) '
            f"to '{published}' (header, delimiter ',')"
        )
    finally:
        con.close()

    assert ours == published.read_bytes()
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    assert manifest["sha256"] == hashlib.sha256(published.read_bytes()).hexdigest()


def test_write_export_imposes_publication_order_on_any_input(tmp_path) -> None:
    """The byte-equality above rests on row order, so `write_export` sorts rather
    than trusting its caller — otherwise a future caller passing rows in query
    order silently reintroduces a second digest for the same dataset."""
    unsorted = [("person", "01B", "01Q"), ("assignment", "01A", "01P")]
    write_export(unsorted, tmp_path / "export")

    lines = (tmp_path / "export" / "anchors.csv").read_text().splitlines()
    assert lines[0] == ",".join(ANCHOR_COLUMNS)
    assert lines[1:] == sorted(lines[1:])
