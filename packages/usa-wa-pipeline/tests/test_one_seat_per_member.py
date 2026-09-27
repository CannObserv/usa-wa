"""One member, one seat at a time — ported from ``succession-invariants`` (#412 PR B),
and widened from the open cohort to all of history, and from one chamber to both (CR 5).

Same harness split as ``test_seat_occupancy``: the predicate is read from the
shipped SQL rather than restated.
"""

import re
from pathlib import Path

import duckdb

TEST_SQL = (
    Path(__file__).resolve().parents[1] / "dbt" / "tests" / "assignments_one_seat_per_member.sql"
)

COLUMNS = (
    "entity_id varchar, span_key varchar, role_key varchar, span_kind varchar,"
    " valid_from date, valid_to date"
)


def _predicate() -> str:
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('assignments'\)\s*\}\}", "assignments", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _span(entity, role_key, kind, valid_from, valid_to=None):
    return (entity, f"{entity}|{role_key}|{valid_from}", role_key, kind, valid_from, valid_to)


def _house(entity, ld, valid_from, valid_to=None):
    return _span(entity, f"seat:house:ld-{ld}:position-1", "chamber-house", valid_from, valid_to)


def _failures(rows: list[tuple]) -> int:
    con = duckdb.connect(":memory:")
    con.execute(f"create table assignments ({COLUMNS})")
    if rows:
        con.executemany("insert into assignments values (?, ?, ?, ?, ?, ?)", rows)
    return len(con.execute(_predicate()).fetchall())


def test_two_open_senate_seats_fail():
    """The "two open senators in LD5" shape, seen from the member's side."""
    assert (
        _failures(
            [
                _span("01A", "seat:senate:ld-5", "chamber-senate", "2025-01-13"),
                _span("01A", "seat:senate:ld-6", "chamber-senate", "2025-06-01"),
            ]
        )
        == 1
    )


def test_a_closed_historical_overlap_fails():
    """The Postgres gate saw only the open cohort, so a conflict that had since
    closed was invisible to it forever (#119). This one gates history."""
    assert (
        _failures(
            [
                _house("01A", 1, "2003-01-13", "2005-01-10"),
                _house("01A", 2, "2004-01-01", "2006-01-01"),
            ]
        )
        == 1
    )


def test_one_seat_held_twice_at_once_fails():
    """Two overlapping spans on ONE seat are a merged tenure (#267), not a handoff."""
    assert (
        _failures(
            [
                _span("01A", "seat:senate:ld-5", "chamber-senate", "2013-01-14", "2017-01-09"),
                _span("01A", "seat:senate:ld-5", "chamber-senate", "2015-01-12"),
            ]
        )
        == 1
    )


def test_a_same_day_move_between_seats_passes():
    """A shared boundary is a handoff (a redistricting renumber, a chamber-internal move)."""
    assert (
        _failures(
            [
                _span("01A", "seat:senate:ld-5", "chamber-senate", "2013-01-14", "2023-01-09"),
                _span("01A", "seat:senate:ld-6", "chamber-senate", "2023-01-09"),
            ]
        )
        == 0
    )


def test_a_house_and_a_senate_seat_at_once_fail():
    """CR 5: the chamber-mover shape (#145). A representative appointed to the
    Senate whose House ``vacated`` was never recorded holds both seats; once the
    House seat has a successor the chamber count is back to 98 and nothing else
    sees it. The ported gate was per chamber; this one is not."""
    assert (
        _failures(
            [
                _house("01A", 5, "2019-01-14", "2020-01-10"),
                _span("01A", "seat:senate:ld-5", "chamber-senate", "2019-12-01"),
            ]
        )
        == 1
    )


def test_a_same_day_move_from_the_house_to_the_senate_passes():
    assert (
        _failures(
            [
                _house("01A", 5, "2019-01-14", "2019-12-01"),
                _span("01A", "seat:senate:ld-5", "chamber-senate", "2019-12-01"),
            ]
        )
        == 0
    )


def test_two_members_on_different_seats_pass():
    assert (
        _failures(
            [
                _span("01A", "seat:senate:ld-5", "chamber-senate", "2025-01-13"),
                _span("01B", "seat:senate:ld-6", "chamber-senate", "2025-01-13"),
            ]
        )
        == 0
    )


def test_party_and_committee_spans_are_out_of_scope():
    """Party and committee roles are legitimately many-at-once."""
    assert (
        _failures(
            [
                _span("01A", "committee:1", "committee", "2025-01-13"),
                _span("01A", "committee:2", "committee", "2025-01-13"),
            ]
        )
        == 0
    )


def test_an_empty_table_passes():
    """The hermetic build materializes conformed models empty (#361)."""
    assert _failures([]) == 0


def test_the_gate_is_a_plain_error():
    """No baseline: 0 on the production build 2026-09-27, across all history."""
    sql = TEST_SQL.read_text()
    assert "severity='error'" in sql
    assert "error_if" not in sql
