"""`role_periods`' key and tiling gates (#501 D1), read from the shipped SQL."""

import re
from pathlib import Path

import duckdb

DBT_TESTS = Path(__file__).resolve().parents[1] / "dbt" / "tests"
KEY_SQL = DBT_TESTS / "role_periods_key.sql"
TILED_SQL = DBT_TESTS / "role_periods_tiled.sql"

PERIODS = (
    "role_key varchar, valid_from date, valid_to date, max_holders bigint,"
    " max_holders_basis varchar"
)
ASSIGNMENTS = "role_key varchar, valid_from date"


def _sql(path: Path) -> str:
    sql = path.read_text()
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('(\w+)'\)\s*\}\}", r"\1", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {path.name}"
    return sql


def _failures(path: Path, periods: list[tuple], assignments: list[tuple] = ()) -> list[tuple]:
    con = duckdb.connect(":memory:")
    con.execute(f"create table role_periods ({PERIODS})")
    con.execute(f"create table assignments ({ASSIGNMENTS})")
    if periods:
        con.executemany(
            "insert into role_periods values (?, ?, ?, ?, ?)",
            [(*p, "kind") for p in periods],
        )
    if assignments:
        con.executemany("insert into assignments values (?, ?)", list(assignments))
    return con.execute(_sql(path)).fetchall()


SEAT = [
    ("seat:senate:ld-19", "1889-01-01", "1890-12-31", 5),
    ("seat:senate:ld-19", "1891-01-01", None, 1),
]
HELD = [("seat:senate:ld-19", "1889-01-01"), ("seat:senate:ld-19", "1993-01-01")]


def test_a_clean_tiling_passes() -> None:
    assert _failures(KEY_SQL, SEAT) == []
    assert _failures(TILED_SQL, SEAT, HELD) == []


def test_a_duplicate_period_start_fails_the_key() -> None:
    assert len(_failures(KEY_SQL, [*SEAT, ("seat:senate:ld-19", "1891-01-01", None, 1)])) == 1


def test_a_gap_fails() -> None:
    gap = [SEAT[0], ("seat:senate:ld-19", "1893-01-01", None, 1)]
    assert _failures(TILED_SQL, gap, HELD)


def test_an_overlap_fails() -> None:
    overlap = [SEAT[0], ("seat:senate:ld-19", "1890-06-01", None, 1)]
    assert _failures(TILED_SQL, overlap, HELD)


def test_a_closed_last_period_fails() -> None:
    assert _failures(TILED_SQL, [SEAT[0]], [HELD[0]])


def test_two_open_periods_fail() -> None:
    two = [("r", "1889-01-01", None, 1), ("r", "1891-01-01", None, 1)]
    assert _failures(TILED_SQL, two, [("r", "1889-01-01")])


def test_a_capacity_below_one_fails() -> None:
    assert _failures(TILED_SQL, [("r", "1889-01-01", None, 0)], [("r", "1889-01-01")])


def test_no_limit_is_allowed() -> None:
    assert _failures(TILED_SQL, [("r", "1889-01-01", None, None)], [("r", "1889-01-01")]) == []


def test_a_tenure_before_the_first_period_fails() -> None:
    """Every published instant needs a covering period (#501 correction 8)."""
    assert _failures(TILED_SQL, SEAT, [("seat:senate:ld-19", "1888-12-12")])


def test_a_held_role_with_no_period_fails() -> None:
    assert _failures(TILED_SQL, [], [("seat:senate:ld-19", "1889-01-01")])
