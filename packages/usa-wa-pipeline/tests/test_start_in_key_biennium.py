"""The #272 misdating gate, ported from ``succession-invariants`` (#412 PR B).

Same harness split as ``test_seat_occupancy``: the predicate is read from the
shipped SQL rather than restated, so a future edit to the gate cannot leave
these green by accident.

A span's key names its tenure-start biennium, so its ``valid_from`` belongs in
it. Two live rows once began a decade past their key — Hans Dunshee's LD-44
House tenure (``seated 2016-02-29`` resolved onto a tenure keyed 2003-04) and
Mike Kreidler's LD-22 Senate tenure — and the duplicate checks cannot see
either: one occupant with wrong dates is not a duplicate.
"""

import re
from pathlib import Path

import duckdb

TEST_SQL = (
    Path(__file__).resolve().parents[1] / "dbt" / "tests" / "assignments_start_in_key_biennium.sql"
)

COLUMNS = "entity_id varchar, span_key varchar, span_start_biennium varchar, valid_from date"


def _predicate() -> str:
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('assignments'\)\s*\}\}", "assignments", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _failures(rows: list[tuple]) -> int:
    con = duckdb.connect(":memory:")
    con.execute(f"create table assignments ({COLUMNS})")
    if rows:
        con.executemany("insert into assignments values (?, ?, ?, ?)", rows)
    return len(con.execute(_predicate()).fetchall())


def test_the_dunshee_shape_fails():
    """A tenure keyed 2003-04 that starts in 2016 records thirteen years as six weeks."""
    assert _failures([("01A", "k", "2003-04", "2016-02-29")]) == 1


def test_a_start_the_year_after_the_biennium_fails():
    assert _failures([("01A", "k", "2003-04", "2005-01-01")]) == 1


def test_a_mid_biennium_appointee_passes():
    """A start inside the key biennium is the ordinary appointee, not drift."""
    assert _failures([("01A", "k", "2003-04", "2004-06-15")]) == 0


def test_a_start_on_the_biennium_floor_passes():
    assert _failures([("01A", "k", "2003-04", "2003-01-01")]) == 0


def test_a_start_before_the_key_biennium_passes():
    """#272's predicate is one-sided, as ported: counterpart clipping (#360) can
    move a quantized ``valid_from`` earlier onto a predecessor's dated exit, so
    an early start is a derived edge, never this defect."""
    assert _failures([("01A", "k", "2003-04", "2002-12-20")]) == 0


def test_a_century_boundary_biennium_is_read_by_its_first_year():
    """``1999-00`` ends in 2000; a naive read of the two-digit tail would say 0."""
    assert _failures([("01A", "k", "1999-00", "2000-12-31")]) == 0
    assert _failures([("01A", "k", "1999-00", "2001-01-01")]) == 1


def test_an_empty_table_passes():
    """The hermetic build materializes conformed models empty (#361)."""
    assert _failures([]) == 0


def test_the_gate_is_a_plain_error():
    """No baseline: the corpus is clean (0 on 2026-09-27), so a threshold would
    only be somewhere for a regression to hide."""
    assert "severity='error'" in TEST_SQL.read_text()
    assert "error_if" not in TEST_SQL.read_text()
