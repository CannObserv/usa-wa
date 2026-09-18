"""The seat-designator gate (#367).

Same split as `test_persons_named` and `test_persons_unannotated`: the predicate
is read out of `dbt/tests/persons_no_seat_suffix.sql` and run against synthetic
rows, never restated here — a test that retypes the SQL it checks is two
literals with a comment claiming they agree.

Every string in SUFFIXED is a real published `name_full` from the
v20260910T212002Z-51c59b snapshot, so the flagged class is the corpus's own.
"""

import re
from pathlib import Path

import duckdb
import pytest

TEST_SQL = Path(__file__).resolve().parents[1] / "dbt" / "tests" / "persons_no_seat_suffix.sql"

COLUMNS = "entity_id varchar, name_full varchar, name_source varchar"

#: All 8, verbatim from the snapshot power-map#499 read.
SUFFIXED = [
    "John Wynne – 39A",
    "Bob Williams – 19A",
    "Carol Monohon – 19B",
    "Charles Moon – 39B",
    "Dick van Dyke – 39B",
    "George L. Raiter – 19A",
    "James B. Mitchell – 39A",
    "Karla Wilson – 39A",
]

NAMES = [
    # The stripped forms — what publishes now.
    "John Wynne",
    "Dick van Dyke",
    # A digit that is part of a name, not a seat: generational suffixes and the
    # numbered forms the corpus really carries.
    "William Bishop, Jr.",
    "Leo K. Thorsness",
    # A hyphenated surname must not read as a designator.
    "Mary Skinner-Smith",
    # A quoted nickname and a marital form, both name content (#378, #384).
    'A. L. "Slim" Rasmussen',
    "Agnes (Mrs. Thomas E.) Kehoe",
    "Patty Murray",
]


def _predicate() -> str:
    """The shipped test SQL with dbt's jinja resolved against a local table."""
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('persons'\)\s*\}\}", "persons", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _flagged(name: str) -> bool:
    con = duckdb.connect()
    try:
        con.execute(f"create table persons ({COLUMNS})")
        con.execute("insert into persons values (?, ?, ?)", ["01X", name, "roster"])
        return bool(con.execute(_predicate()).fetchall())
    finally:
        con.close()


@pytest.mark.parametrize("name", SUFFIXED)
def test_a_seat_designator_fails_the_gate(name: str) -> None:
    assert _flagged(name), name


@pytest.mark.parametrize("name", NAMES)
def test_a_name_passes_the_gate(name: str) -> None:
    assert not _flagged(name), name


@pytest.mark.parametrize("dash", ["–", "—", "-"])
def test_every_dash_the_stripper_accepts_is_gated(dash: str) -> None:
    """The gate must cover the same separator set as `strip_position_suffix`.

    The corpus uses an en dash throughout. If the gate matched only that, a
    hyphenated or em-dashed designator would pass a check whose whole job is to
    prove the stripper ran — the gate would be narrower than the thing it guards.
    """
    assert _flagged(f"John Wynne {dash} 39A")
