"""The blank-name gate (#364): a person's name is a name, or it is absent.

Same split as `test_seat_occupancy`: the predicate is read out of
``dbt/tests/persons_named.sql`` and run against synthetic rows, never restated
here — a test that retypes the SQL it checks is two literals with a comment
claiming they agree. Its logic, not its schema; the dbt run is what couples the
predicate to the real `persons` shape.

The gate is the half of #364 that outlives the fix. `conformed.entities._name`
is why no blank is built today; this is why a build that starts producing one
again fails HERE, in the nightly, rather than in a consumer's staging models
three weeks later (which is how power-map#497 found the first four).
"""

import re
from pathlib import Path

import duckdb

TEST_SQL = Path(__file__).resolve().parents[1] / "dbt" / "tests" / "persons_named.sql"

COLUMNS = "entity_id varchar, name_full varchar, name_source varchar"


def _predicate() -> str:
    """The shipped test SQL with dbt's jinja resolved against a local table."""
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('persons'\)\s*\}\}", "persons", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _violations(rows: list[tuple]) -> int:
    con = duckdb.connect()
    try:
        con.execute(f"create table persons ({COLUMNS})")
        con.executemany("insert into persons values (?, ?, ?)", rows)
        return len(con.execute(_predicate()).fetchall())
    finally:
        con.close()


def test_a_real_name_passes() -> None:
    assert _violations([("01A", "Tina Orwall", "wsl")]) == 0


def test_catches_the_whitespace_name() -> None:
    """The published shape #364 is named for: four legislators, `name_full` a
    single space, `name_source` claiming WSL attested it."""
    assert _violations([("01A", " ", "wsl")]) == 1


def test_catches_an_empty_string_name() -> None:
    """`''` and `' '` are the same defect; only `''` survives a naive trim."""
    assert _violations([("01A", "", "wsl")]) == 1


def test_catches_an_untrimmed_name() -> None:
    """WSL ships `'Marlo Braun '` and PDC `'MICHAEL JAMES BAUMGARTNER '`. A
    name with edges is a name a consumer has to clean, i.e. ours to clean."""
    assert _violations([("01A", "Marlo Braun ", "wsl")]) == 1


def test_a_nameless_person_passes() -> None:
    """The Heck acceptance: WSL member 31656 (Lt. Governor, an ex-officio Senate
    Rules seat minted from the retired `committee-members:` archive) has NO
    staging attestation at all, so no source can name him. Null on both columns
    is the honest reading and the one the gate must allow — the alternative is a
    `required` constraint that would wedge the nightly on a documented gap."""
    assert _violations([("01A", None, None)]) == 0


def test_catches_a_name_with_no_source() -> None:
    """The pair travels together — a name whose provenance is unstated is a
    survivorship bug, not a name."""
    assert _violations([("01A", "Tina Orwall", None)]) == 1


def test_catches_a_source_with_no_name() -> None:
    """The inverse, and the shape a future blank would take if a caller nulled
    the name without also dropping the source that claimed to supply it."""
    assert _violations([("01A", None, "wsl")]) == 1


def test_the_shipped_sql_gates_at_zero() -> None:
    """No baseline, no ratchet: unlike the occupancy gate this one starts clean
    on the real corpus (measured 2026-09-10, 3,135 persons), so any violation is
    new. If a future edit adds a threshold, that is a finding to argue in review
    rather than something to slip past this file."""
    assert TEST_SQL.is_file()
    sql = TEST_SQL.read_text()
    assert "error_if" not in sql and "warn_if" not in sql
