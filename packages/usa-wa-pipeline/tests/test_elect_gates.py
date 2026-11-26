"""The member-elect gates (#600 spec § 2.6), read from the shipped SQL."""

import re
from pathlib import Path

import duckdb

DBT_TESTS = Path(__file__).resolve().parents[1] / "dbt" / "tests"
ONE_PER_SEAT = DBT_TESTS / "assignments_elect_one_per_seat.sql"
NOT_HOLDOVER = DBT_TESTS / "assignments_elect_not_holdover.sql"

ASSIGNMENTS = "span_key varchar, role_key varchar, span_kind varchar, span_start_biennium varchar"
WINNERS = "election_year bigint, role_key varchar"


def _sql(path: Path) -> str:
    sql = path.read_text()
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('(\w+)'\)\s*\}\}", r"\1", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {path.name}"
    return sql


def _failures(path: Path, assignments: list[tuple], winners: list[tuple] = ()) -> list[tuple]:
    con = duckdb.connect(":memory:")
    con.execute(f"create table assignments ({ASSIGNMENTS})")
    con.execute(f"create table seat_winners ({WINNERS})")
    if assignments:
        con.executemany("insert into assignments values (?, ?, ?, ?)", assignments)
    if winners:
        con.executemany("insert into seat_winners values (?, ?)", list(winners))
    return con.execute(_sql(path)).fetchall()


PHAM = ("a", "seat-elect:house:ld-4:position-1", "chamber-house-elect", "2027-28")
YOUNG = ("b", "seat-elect:senate:ld-26", "chamber-senate-elect", "2027-28")
WON = [(2026, "seat:house:ld-4:position-1"), (2026, "seat:senate:ld-26")]


def test_one_member_elect_per_seat_elect_role_passes() -> None:
    assert _failures(ONE_PER_SEAT, [PHAM, YOUNG]) == []


def test_two_member_elects_on_one_seat_fail() -> None:
    assert _failures(ONE_PER_SEAT, [PHAM, ("c", *PHAM[1:])])


def test_the_same_seat_in_two_bienniums_is_two_elections() -> None:
    assert _failures(ONE_PER_SEAT, [PHAM, ("c", PHAM[1], PHAM[2], "2029-30")]) == []


def test_a_member_elect_whose_seat_was_on_the_ballot_passes() -> None:
    assert _failures(NOT_HOLDOVER, [PHAM, YOUNG], WON) == []


def test_a_holdover_s_seat_has_no_member_elect() -> None:
    """LD4's Senate seat wasn't on the 2026 ballot: a member-elect there is invented."""
    christian = ("d", "seat-elect:senate:ld-4", "chamber-senate-elect", "2027-28")
    assert _failures(NOT_HOLDOVER, [christian], WON) == [("d", "seat-elect:senate:ld-4")]


def test_the_seat_must_be_on_the_seating_general_not_an_earlier_one() -> None:
    assert _failures(NOT_HOLDOVER, [YOUNG], [(2022, "seat:senate:ld-26")])


def test_the_gates_ignore_every_other_kind() -> None:
    seat = ("e", "seat:house:ld-4:position-1", "chamber-house", "2025-26")
    assert _failures(ONE_PER_SEAT, [seat, ("f", *seat[1:])]) == []
    assert _failures(NOT_HOLDOVER, [seat]) == []
