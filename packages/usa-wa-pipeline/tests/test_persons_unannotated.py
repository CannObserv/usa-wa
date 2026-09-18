"""The annotation gate (#378): a published name is a name, not a note about one.

Same split as `test_persons_named`: the predicate is read out of
`dbt/tests/persons_unannotated.sql` and run against synthetic rows, never
restated here — a test that retypes the SQL it checks is two literals with a
comment claiming they agree. Its logic, not its schema; the dbt run is what
couples the predicate to the real `persons` shape.

Every string below is a real published `name_full` from the 2026-09-10 snapshot,
so the two classes are the corpus's own, not ones invented to fit the rule.
"""

import re
from pathlib import Path

import duckdb
import pytest

TEST_SQL = Path(__file__).resolve().parents[1] / "dbt" / "tests" / "persons_unannotated.sql"

COLUMNS = "entity_id varchar, name_full varchar, name_source varchar"

ANNOTATIONS = [
    "Geraldine McCormick (Resgnd Dec. 31, 1982)",
    "D. N. Judson (Apntd Dec. 13 to serve ’33 Ex. S.)",
    "A. R. Heilig (Left Seattle July 2, 1900 Named Court Clerk, 3rd Judcl Dvn, AK Terr.)",
    "James Wickersham (Left Seattle July 2, 1900 Appointed Judge, 3rd Judcl Dvn, AK Terr.)",
    # No digit anywhere — caught on length, which is why the rule cannot be
    # "contains a number".
    "James M. Hogan (Select House Cmte upheld election challenge, Hogan declared duly elected)",
    # #378's reason for existing: survivorship would have made this the
    # published legal name of a live, power-map-resolved legislator.
    "Myron “Mike” Kreidler (On leave of absence for military duty Jan. 8, 1991 to April 18, 1991)",
]

NAMES = [
    # Marital print forms — name content, and an open editorial question the
    # nightly build must not settle by failing (#378 follow-on 2).
    "Agnes (Mrs. Thomas E.) Kehoe",
    "Belle (Mrs. Frank) Reeves",
    "Frances (Mrs. Thomas A.) Swayze",
    "Margaret (Mrs. Joseph E.) Hurley",
    "Mrs. Irwin LeCocq (Mary)",
    "Mrs. Jurie B.(Nettie Luella) Smith",
    "Mrs. Vincent (Matilda) F. Jones",
    # A legal-name gloss on a nickname.
    "Jack (John T.) Dootson",
    # A quoted nickname is how the person is known and carries no parenthesis.
    "A. L. “Slim” Rasmussen",
    # Nothing remarkable.
    "Patty Murray",
]


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


@pytest.mark.parametrize("name", ANNOTATIONS)
def test_catches_a_printed_annotation(name) -> None:
    assert _violations([("01A", name, "roster")]) == 1


@pytest.mark.parametrize("name", NAMES)
def test_passes_name_content(name) -> None:
    assert _violations([("01A", name, "roster")]) == 0


def test_a_nameless_person_passes() -> None:
    """`regexp_matches(NULL, …)` is NULL, which SQL drops — and a live entity no
    source attests is legitimate (`persons_named.sql` records why)."""
    assert _violations([("01A", None, None)]) == 0


def test_an_organization_chamber_marker_would_pass() -> None:
    """Organizations go through the same name screen, and `Law & Justice (H)` is
    a committee's chamber marker rather than prose. Pinned here because the
    tempting shortcut — flag every parenthesis — would take it."""
    assert _violations([("01A", "Law & Justice (H)", "roster")]) == 0
