"""The PM anchor export (#312): base32 crosswalk seed for power-map cutover."""

from pathlib import Path

import duckdb
import pytest
from ulid import ULID

from clearinghouse_domain_legislative.identity import Person
from usa_wa_pipeline import anchor_export, publish
from usa_wa_pipeline.anchor_export import (
    ANCHOR_COLUMNS,
    ANCHOR_TABLE,
    anchor_rows,
    kind_counts,
    materialize_anchors,
)


@pytest.mark.db
async def test_anchor_rows_are_base32_pairs(db_session) -> None:
    pm_id = ULID()
    anchored = Person(source="usa_wa_legislature", source_id="1", name_full="A", pm_person_id=pm_id)
    unanchored = Person(source="usa_wa_legislature", source_id="2", name_full="B")
    db_session.add_all([anchored, unanchored])
    await db_session.flush()

    rows = await anchor_rows(db_session)

    assert kind_counts(rows) == {"person": 1, "organization": 0, "role": 0, "assignment": 0}
    [(kind, local_id, pm)] = rows
    assert kind == "person"
    assert local_id == str(anchored.id)
    assert pm == str(pm_id)
    # the PM-side hard requirement: 26-char Crockford base32, never UUID-hex
    assert len(pm) == 26
    assert "-" not in pm
    assert str(unanchored.id) not in {row[1] for row in rows}


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


def test_kind_counts_reports_every_kind_and_rejects_unknown_ones() -> None:
    """Per-kind totals are what PM verifies its cohort against (3,118 persons),
    and the catalog carries only a single `rows` total — so they live in the
    job's counters now, zeros included, rather than being inferred from absence."""
    assert kind_counts([("person", "01A", "01P"), ("person", "01B", "01Q")]) == {
        "person": 2,
        "organization": 0,
        "role": 0,
        "assignment": 0,
    }
    with pytest.raises(ValueError, match="unknown anchor kind"):
        kind_counts([("bogus", "01C", "01D")])
