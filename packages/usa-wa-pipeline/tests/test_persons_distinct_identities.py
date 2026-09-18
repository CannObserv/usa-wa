"""The collision gate's dbt half (#378 step 4).

`test_conformed_namesakes` owns the rule. What is left to prove is the wiring,
because this gate is unusually thin where the others are not: `persons_named`
and `persons_unannotated` carry their predicates in SQL, so reading the file
tests the rule. Here the SQL is a `select *` and every decision sits in a python
model, which means the only way the build can stop asserting the rule is for the
three links between them to come apart — the test refs the model, the model
calls `collision_rows`, and rows mean failure.

So that is what this tests: the seam, not the fold.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

_DBT = Path(__file__).resolve().parents[1] / "dbt"
TEST_SQL = _DBT / "tests" / "persons_distinct_identities.sql"
MODEL_PY = _DBT / "models" / "conformed" / "person_name_collisions.py"

COLUMNS = "name_fold varchar, entity_id varchar, name_full varchar, name_source varchar"


def _predicate() -> str:
    """The shipped test SQL with dbt's jinja resolved against a local table."""
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('person_name_collisions'\)\s*\}\}", "person_name_collisions", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _violations(rows: list[tuple]) -> int:
    con = duckdb.connect()
    try:
        con.execute(f"create table person_name_collisions ({COLUMNS})")
        # duckdb's executemany refuses an empty parameter list, and the empty
        # table is the case this gate lives in every other night.
        if rows:
            con.executemany("insert into person_name_collisions values (?, ?, ?, ?)", rows)
        return len(con.execute(_predicate()).fetchall())
    finally:
        con.close()


def test_an_empty_model_passes_the_gate() -> None:
    """The clean corpus builds. Gated at zero, so this is the everyday case."""
    assert _violations([]) == 0


def test_a_collision_fails_the_gate() -> None:
    """dbt's singular-test contract: rows returned are the failure."""
    rows = [
        ("pattymurray", "A", "Patty Murray", "wsl"),
        ("pattymurray", "B", "Patty Murray", "roster"),
    ]
    assert _violations(rows) == 2


def test_the_gate_reads_the_model_the_package_builds() -> None:
    """The one link a `select *` cannot make for itself.

    A gate whose predicate is trivial is only as good as what it points at. If
    this ref were retargeted — or the model quietly rebuilt from something other
    than the tested `collision_rows` — every test here and in
    `test_conformed_namesakes` would still pass while the build asserted
    nothing.
    """
    assert "ref('person_name_collisions')" in TEST_SQL.read_text()
    model = MODEL_PY.read_text()
    assert "from usa_wa_pipeline.conformed.namesakes import" in model
    assert "collision_rows(" in model
    assert 'dbt.ref("persons")' in model


def test_the_gate_selects_the_work_order_not_a_count() -> None:
    """A reviewer adjudicating a pair needs both ids and both names in the failure."""
    sql = TEST_SQL.read_text()
    for column in ("name_fold", "entity_id", "name_full", "name_source"):
        assert column in sql
    assert "count(" not in sql.lower()
