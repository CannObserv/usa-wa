"""The span-duration gate (#363): a published tenure has duration.

Same harness split as ``test_seat_occupancy``: the predicate is read from the
shipped SQL rather than restated, so a future edit to the gate cannot leave
these green by accident.

Why it exists at all: the seat-occupancy gate needs TWO distinct holders to see
anything, so a single span collapsed to a point passes every check the conformed
tier had. One did — Derek Stanford's Senate seat, closed by a `departed` event at
the instant a `seated` opened it, taking 18 months of a sitting senator's tenure
out of the published record with nothing to report it.
"""

import re
from pathlib import Path

import duckdb

TEST_SQL = Path(__file__).resolve().parents[1] / "dbt" / "tests" / "assignments_span_duration.sql"

COLUMNS = "entity_id varchar, role_key varchar, valid_from date, valid_to date"


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
        con.executemany(f"insert into assignments values ({', '.join(['?'] * len(rows[0]))})", rows)
    return len(con.execute(_predicate()).fetchall())


def test_a_zero_length_span_fails():
    assert _failures([("01A", "seat:senate:ld-1", "2019-07-01", "2019-07-01")]) == 1


def test_an_inverted_span_fails():
    assert _failures([("01A", "seat:senate:ld-1", "2019-07-01", "2019-06-01")]) == 1


def test_an_ordinary_closed_span_passes():
    assert _failures([("01A", "seat:senate:ld-1", "2019-07-01", "2020-12-31")]) == 0


def test_a_one_day_span_passes():
    """A tenure can genuinely be short — Washington seats military substitutes
    for days at a time (usa-wa#362). The gate asserts duration, not a minimum."""
    assert _failures([("01A", "seat:senate:ld-6", "2005-04-14", "2005-04-15")]) == 0


def test_an_open_span_passes():
    assert _failures([("01A", "seat:senate:ld-1", "2019-07-01", None)]) == 0


def test_an_empty_table_passes():
    """The hermetic build materializes conformed models empty (#361)."""
    assert _failures([]) == 0
