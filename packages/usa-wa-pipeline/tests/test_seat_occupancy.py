"""The seat-occupancy gate (#359): one seat, one holder at a time.

The predicate is exercised by reading ``dbt/tests/assignments_seat_occupancy.sql``
and running it against synthetic rows, never by restating it here. A test that
retypes the SQL it checks is two literals with a comment claiming they agree —
the defect CR 117 caught one level up, and the reason #358's phantom could have
returned unnoticed.
"""

import re
from pathlib import Path

import duckdb
import pytest

TEST_SQL = Path(__file__).resolve().parents[1] / "dbt" / "tests" / "assignments_seat_occupancy.sql"

COLUMNS = "entity_id varchar, role_key varchar, span_kind varchar, valid_from date, valid_to date"


def _predicate() -> str:
    """The shipped test SQL with dbt's jinja resolved against a local table."""
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{\{\s*config\([^}]*\)\s*\}\}", "", sql)
    sql = re.sub(r"\{\{\s*ref\('assignments'\)\s*\}\}", "assignments", sql)
    assert "{{" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _conflicts(rows: list[tuple]) -> int:
    con = duckdb.connect()
    try:
        con.execute(f"create table assignments ({COLUMNS})")
        con.executemany("insert into assignments values (?, ?, ?, ?, ?)", rows)
        return len(con.execute(_predicate()).fetchall())
    finally:
        con.close()


def test_the_shipped_sql_is_the_thing_under_test() -> None:
    """If the file moves or stops being a singular test, fail here rather than
    silently exercising nothing."""
    assert TEST_SQL.is_file()
    assert "error_if" in TEST_SQL.read_text(), "the ratchet config is the gate's teeth"


def test_catches_the_wynne_shape() -> None:
    """#358: WSL asserted both Stevens and Wynne as Senate LD-39 for 2001-02.
    The roster observes Stevens at term-start 2001 and has no Wynne senate row
    at all, so the phantom is bad upstream data — and nothing in the pipeline
    would have said so. This is that guard."""
    conflicts = _conflicts(
        [
            ("stevens", "seat:senate:ld-39", "chamber-senate", "1997-01-01", "2012-12-31"),
            ("wynne", "seat:senate:ld-39", "chamber-senate", "2001-01-01", "2002-12-31"),
        ]
    )
    assert conflicts == 1


def test_a_shared_boundary_is_a_handoff_not_an_overlap() -> None:
    """A span ending the day its successor begins is how mid-term succession is
    spelled. Counting it would bury real conflicts under clean transitions."""
    assert (
        _conflicts(
            [
                ("pearson", "seat:senate:ld-39", "chamber-senate", "2013-01-01", "2017-11-12"),
                ("wagoner", "seat:senate:ld-39", "chamber-senate", "2017-11-12", None),
            ]
        )
        == 0
    )


def test_an_open_span_still_conflicts() -> None:
    """`valid_to IS NULL` means "still serving", not "no end to compare", so an
    open span overlaps anything starting after it. Without the explicit null
    branch a live conflict hides behind the incumbent's open end."""
    assert (
        _conflicts(
            [
                ("incumbent", "seat:senate:ld-1", "chamber-senate", "2019-01-01", None),
                ("claimant", "seat:senate:ld-1", "chamber-senate", "2021-01-01", "2022-12-31"),
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "kind,role", [("party", "party-role:republican"), ("committee", "committee-member-role:7")]
)
def test_multi_holder_kinds_are_not_seats(kind: str, role: str) -> None:
    """Party membership and committee seats are legitimately multi-holder. The
    constraint is stated by KIND, so no exception list has to be maintained."""
    assert (
        _conflicts(
            [
                ("a", role, kind, "2001-01-01", "2002-12-31"),
                ("b", role, kind, "2001-01-01", "2002-12-31"),
            ]
        )
        == 0
    )


def test_one_person_two_spans_in_one_seat_is_not_a_conflict() -> None:
    """A returning member holds the same seat twice. Disjoint spans for ONE
    entity are how an interruption is represented (#358 q3), not a conflict."""
    assert (
        _conflicts(
            [
                ("comfort", "seat:senate:ld-2", "chamber-senate", "1943-01-01", "1952-12-31"),
                ("comfort", "seat:senate:ld-2", "chamber-senate", "1955-01-01", "1956-12-31"),
            ]
        )
        == 0
    )
