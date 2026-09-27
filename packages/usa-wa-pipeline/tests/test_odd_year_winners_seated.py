"""Odd-year winner corroboration, ported from the House and Senate units (#412 PR B).

Same harness split as ``test_seat_occupancy``: the predicate is read from the
shipped SQL rather than restated.
"""

import re
from pathlib import Path

import duckdb

TEST_SQL = (
    Path(__file__).resolve().parents[1]
    / "dbt"
    / "tests"
    / "assignments_odd_year_winners_seated.sql"
)

WINNER_COLUMNS = "election_year bigint, role_key varchar, resource_id varchar"
SPAN_COLUMNS = "entity_id varchar, role_key varchar, valid_from date, valid_to date"

LD5 = "seat:senate:ld-5"
LD30_2 = "seat:house:ld-30:position-2"


def _predicate() -> str:
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('assignments'\)\s*\}\}", "assignments", sql)
    sql = re.sub(r"\{\{\s*ref\('seat_winners'\)\s*\}\}", "seat_winners", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _unseated(winners: list[tuple], spans: list[tuple]) -> list[tuple]:
    con = duckdb.connect(":memory:")
    con.execute(f"create table seat_winners ({WINNER_COLUMNS})")
    con.execute(f"create table assignments ({SPAN_COLUMNS})")
    if winners:
        con.executemany("insert into seat_winners values (?, ?, ?)", winners)
    if spans:
        con.executemany("insert into assignments values (?, ?, ?, ?)", spans)
    return sorted((row[0], row[1]) for row in con.execute(_predicate()).fetchall())


def _won(year: int, role_key: str) -> tuple:
    return (year, role_key, f"sos-legresults:{year}1104")


def test_the_hunt_shape_passes():
    """LD5, 2025: appointed in June, elected in November, still serving."""
    assert _unseated([_won(2025, LD5)], [("01A", LD5, "2025-06-02", None)]) == []


def test_a_winner_whose_seat_nobody_holds_fails():
    """The missing operator ``seated``: the ballot names a seat the record leaves empty."""
    assert _unseated([_won(2025, LD5)], [("01A", LD5, "2021-01-11", "2025-05-31")]) == [(2025, LD5)]


def test_the_hickel_shape_is_caught_in_history():
    """LD30 Pos 2, 2015: won the special, rostered, never seated — found by a
    manual audit, because the Postgres gate probed only the current biennium and
    its history sweep was report-only. This gate covers every archived odd year."""
    assert _unseated([_won(2015, LD30_2)], [("01B", LD30_2, "2013-01-14", "2015-06-30")]) == [
        (2015, LD30_2)
    ]


def test_a_seat_held_by_someone_else_passes():
    """Keyed on seat existence, not occupant identity, as the ported gates were:
    a surname mismatch is more often a name change than a missing succession."""
    assert _unseated([_won(2019, LD5)], [("01Z", LD5, "2019-01-14", "2021-01-11")]) == []


def test_a_winner_who_has_since_left_passes():
    """The probe is the end of the election year, not today: a 2025 winner who
    resigned in 2026 is a vacancy now, and the Postgres gate, probing the open
    cohort, would have paged on it."""
    assert _unseated([_won(2025, LD5)], [("01A", LD5, "2025-06-02", "2026-03-01")]) == []


def test_a_seat_that_emptied_before_year_end_fails():
    assert _unseated([_won(2017, LD5)], [("01A", LD5, "2017-11-30", "2017-12-15")]) == [(2017, LD5)]


def test_even_year_winners_are_out_of_scope():
    """An even November seats the NEXT biennium in January; the ported gates
    corroborated the odd-year specials only."""
    assert _unseated([_won(2024, LD5)], []) == []


def test_an_empty_build_passes():
    """The hermetic build materializes both models empty (#361)."""
    assert _unseated([], []) == []


def test_the_gate_is_a_plain_error():
    """0 across every archived odd year (2009-2025) on the production build 2026-09-27."""
    sql = TEST_SQL.read_text()
    assert "severity='error'" in sql
    assert "error_if" not in sql
