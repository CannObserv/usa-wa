"""The chamber-count gates, ported from ``succession-invariants`` (#412 PR B).

Two files, because dbt thresholds count result ROWS and cannot tell an excess
from a vacancy inside one test:

- ``assignments_chamber_excess.sql`` — **error**: more open seats than the
  chamber has is a ghost-open predecessor, a missing ``departed``/``vacated``.
- ``assignments_chamber_vacancy.sql`` — **warn**: fewer is a real state (a
  death, a resignation awaiting appointment, the 2027-01-01 rollover before the
  new wire lands). The Postgres gate's strict equality would have aborted the
  nightly before registrar and publish on every one of them.

Same harness split as ``test_seat_occupancy``: the predicates are read from the
shipped SQL rather than restated.
"""

import re
from pathlib import Path

import duckdb
import pytest

from usa_wa_common.seats import HOUSE_SEATS, SENATE_SEATS

_TESTS = Path(__file__).resolve().parents[1] / "dbt" / "tests"
EXCESS_SQL = _TESTS / "assignments_chamber_excess.sql"
VACANCY_SQL = _TESTS / "assignments_chamber_vacancy.sql"

COLUMNS = "entity_id varchar, span_kind varchar, is_active boolean"


def _predicate(path: Path) -> str:
    sql = path.read_text()
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('assignments'\)\s*\}\}", "assignments", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {path.name}"
    return sql


def _chamber(senate_open: int, house_open: int, *, closed: int = 1) -> list[tuple]:
    """Open spans per chamber, plus some closed history in each."""
    rows = [(f"S{i}", "chamber-senate", True) for i in range(senate_open)]
    rows += [(f"H{i}", "chamber-house", True) for i in range(house_open)]
    rows += [
        (f"X{i}", kind, False)
        for i in range(closed)
        for kind in ("chamber-senate", "chamber-house")
    ]
    return rows


def _failing(path: Path, rows: list[tuple]) -> list[str]:
    con = duckdb.connect(":memory:")
    con.execute(f"create table assignments ({COLUMNS})")
    if rows:
        con.executemany("insert into assignments values (?, ?, ?)", rows)
    return sorted(row[0] for row in con.execute(_predicate(path)).fetchall())


def test_a_full_legislature_passes_both():
    rows = _chamber(SENATE_SEATS, HOUSE_SEATS)
    assert _failing(EXCESS_SQL, rows) == []
    assert _failing(VACANCY_SQL, rows) == []


def test_a_ghost_open_senator_is_an_excess():
    """50 open senators: a predecessor nobody closed."""
    rows = _chamber(SENATE_SEATS + 1, HOUSE_SEATS)
    assert _failing(EXCESS_SQL, rows) == ["chamber-senate"]
    assert _failing(VACANCY_SQL, rows) == []


def test_a_ghost_open_representative_is_an_excess():
    rows = _chamber(SENATE_SEATS, HOUSE_SEATS + 1)
    assert _failing(EXCESS_SQL, rows) == ["chamber-house"]


def test_a_vacancy_is_not_an_excess():
    """48 open senators is a vacancy, a real state — it must never abort the build."""
    rows = _chamber(SENATE_SEATS - 1, HOUSE_SEATS - 2)
    assert _failing(EXCESS_SQL, rows) == []
    assert _failing(VACANCY_SQL, rows) == ["chamber-house", "chamber-senate"]


def test_closed_spans_do_not_count():
    rows = _chamber(SENATE_SEATS, HOUSE_SEATS, closed=500)
    assert _failing(EXCESS_SQL, rows) == []


def test_non_chamber_kinds_do_not_count():
    rows = _chamber(SENATE_SEATS, HOUSE_SEATS) + [(f"P{i}", "party", True) for i in range(200)]
    assert _failing(EXCESS_SQL, rows) == []


def test_an_empty_table_passes_both():
    """The hermetic build materializes conformed models empty (#361); a vacancy
    warning on every pre-commit would teach everyone to ignore the real one."""
    assert _failing(EXCESS_SQL, []) == []
    assert _failing(VACANCY_SQL, []) == []


def test_a_chamber_with_every_seat_vacant_still_warns():
    """Vacuous only when a chamber has no spans at all, never when all are closed."""
    rows = _chamber(SENATE_SEATS, 0)
    assert _failing(VACANCY_SQL, rows) == ["chamber-house"]


@pytest.mark.parametrize("path", [EXCESS_SQL, VACANCY_SQL], ids=lambda p: p.stem)
def test_the_seat_literals_are_the_chamber_sizes(path):
    """The SQL cannot import a constant, so the literals are pinned to the one
    that defines them — a redistricting edits ``usa_wa_common.seats`` and this fails
    until both gates follow."""
    sql = path.read_text()
    assert re.search(rf"\('chamber-senate',\s*{SENATE_SEATS}\)", sql)
    assert re.search(rf"\('chamber-house',\s*{HOUSE_SEATS}\)", sql)


def test_the_severities_are_split():
    """An excess aborts the build; a vacancy only warns."""
    assert "severity='error'" in EXCESS_SQL.read_text()
    assert "severity='warn'" in VACANCY_SQL.read_text()
