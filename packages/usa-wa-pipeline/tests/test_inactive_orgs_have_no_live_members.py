"""An inactive organization has no live member — #124's INV1, ported from the
``committee-lineage-invariants`` unit, which reads canonical and retires (#428,
ahead of #412 PR E).

Same harness split as ``test_one_seat_per_member``: the predicate is read from
the shipped SQL rather than restated.
"""

import re
from pathlib import Path

import duckdb

TEST_SQL = (
    Path(__file__).resolve().parents[1]
    / "dbt"
    / "tests"
    / "organizations_inactive_have_no_live_members.sql"
)


def _predicate() -> str:
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('(\w+)'\)\s*\}\}", r"\1", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _failures(orgs: list[tuple], roles: list[tuple], assignments: list[tuple]) -> list[tuple]:
    con = duckdb.connect(":memory:")
    con.execute("create table organizations (entity_id varchar, org_type varchar, active boolean)")
    con.execute("create table roles (role_key varchar, org_entity_id varchar)")
    con.execute("create table assignments (entity_id varchar, role_key varchar, is_active boolean)")
    for table, rows in (("organizations", orgs), ("roles", roles), ("assignments", assignments)):
        if rows:
            marks = ", ".join("?" * len(rows[0]))
            con.executemany(f"insert into {table} values ({marks})", rows)
    return con.execute(_predicate()).fetchall()


ROLE = [("committee:1754:member", "02A")]


def test_a_retired_committee_with_a_live_member_fails():
    """The #124 shape: a committee WSL no longer returns still reads as having
    current members."""
    failures = _failures(
        [("02A", "committee", False)], ROLE, [("01A", "committee:1754:member", True)]
    )
    assert len(failures) == 1


def test_a_retired_committee_whose_members_left_passes():
    assert (
        _failures([("02A", "committee", False)], ROLE, [("01A", "committee:1754:member", False)])
        == []
    )


def test_an_active_committee_with_live_members_passes():
    assert (
        _failures([("02A", "committee", True)], ROLE, [("01A", "committee:1754:member", True)])
        == []
    )


def test_a_historical_party_with_a_seated_member_fails_too():
    """WIDER than the unit it replaces, which checked committees only: a live
    member of a party declared inactive is the same defect on another org type.
    0 on the production build, 2026-09-28."""
    failures = _failures(
        [("03B", "party", False)],
        [("party:populist", "03B")],
        [("01A", "party:populist", True)],
    )
    assert len(failures) == 1


def test_one_row_per_offending_org_not_per_member():
    """dbt counts rows, so a retired committee with ten stale members is one
    failure naming the count, not ten."""
    failures = _failures(
        [("02A", "committee", False)],
        ROLE,
        [("01A", "committee:1754:member", True), ("01B", "committee:1754:member", True)],
    )
    assert len(failures) == 1
