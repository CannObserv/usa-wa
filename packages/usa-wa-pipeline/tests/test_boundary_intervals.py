"""The D6 basis and interval invariants on `assignments` (#501), from the shipped SQL."""

import re
from pathlib import Path

import duckdb
import pytest

TEST_SQL = (
    Path(__file__).resolve().parents[1] / "dbt" / "tests" / "assignments_boundary_intervals.sql"
)
SUBSTITUTES_SQL = TEST_SQL.with_name("assignments_substitutes_nested.sql")

COLUMNS = (
    "span_key varchar, role_key varchar, entity_id varchar, span_start_biennium varchar,"
    " span_end_biennium varchar, valid_from date, valid_to date, is_active boolean,"
    " start_basis varchar, end_basis varchar, valid_from_latest date, valid_to_earliest date,"
    " membership_type varchar"
)


def _sql(path: Path) -> str:
    sql = path.read_text()
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('(\w+)'\)\s*\}\}", r"\1", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {path.name}"
    return sql


def _failures(rows: list[dict], path: Path = TEST_SQL) -> list[tuple]:
    con = duckdb.connect(":memory:")
    con.execute(f"create table assignments ({COLUMNS})")
    names = [c.strip().split()[0] for c in COLUMNS.split(",")]
    for row in rows:
        con.execute(
            f"insert into assignments values ({', '.join('?' * len(names))})",
            [row.get(n) for n in names],
        )
    return con.execute(_sql(path)).fetchall()


def _row(**over) -> dict:
    """An observed 1971-74 tenure, its intervals as D6 defines them."""
    row = {
        "span_key": "k",
        "role_key": "seat:senate:ld-29",
        "entity_id": "01A",
        "span_start_biennium": "1971-72",
        "span_end_biennium": "1973-74",
        "valid_from": "1971-01-01",
        "valid_to": "1974-12-31",
        "is_active": False,
        "start_basis": "observed",
        "end_basis": "observed",
        "valid_from_latest": "1972-12-31",
        "valid_to_earliest": "1973-01-01",
        "membership_type": "principal",
    }
    row.update(over)
    return row


DATED = _row(
    valid_from="1971-10-04",
    valid_to="1973-03-19",
    start_basis="dated",
    end_basis="dated",
    valid_from_latest="1971-10-04",
    valid_to_earliest="1973-03-19",
)
DERIVED = _row(
    valid_from="1971-08-09",
    valid_to="1973-05-01",
    start_basis="derived",
    end_basis="derived",
    valid_from_latest="1972-12-31",
    valid_to_earliest="1973-01-01",
)
OPEN = _row(
    span_end_biennium="2025-26",
    valid_to=None,
    is_active=True,
    end_basis="open",
    valid_to_earliest=None,
)


@pytest.mark.parametrize(
    "row", [_row(), DATED, DERIVED, OPEN], ids=["observed", "dated", "derived", "open"]
)
def test_each_basis_as_defined_passes(row: dict) -> None:
    assert _failures([row]) == []


@pytest.mark.parametrize(
    "over",
    [
        # invariant 1: open ⇔ valid_to null ⇔ valid_to_earliest null ⇔ is_active
        {"end_basis": "open"},
        {"is_active": True},
        {"valid_to_earliest": None},
        # invariant 3: the bounds are ordered
        {"valid_from_latest": "1970-12-31"},
        {"valid_to_earliest": "1975-01-01"},
        # invariant 4: observed sits on its own biennium edge, the interval is the table's
        {"valid_from": "1971-06-01"},
        {"valid_to": "1974-06-01"},
        {"valid_from_latest": "1972-06-01"},
        # invariant 5: dated is exact (a window is #501 D6a's, in the follow-up)
        {"start_basis": "dated"},
        {"end_basis": "dated"},
    ],
)
def test_a_broken_invariant_fails(over: dict) -> None:
    assert _failures([_row(**over)])


def test_a_derived_edge_is_never_posed_as_exact() -> None:
    """Invariant 6: an inference keeps its biennium's width unless rule 3 clamps it."""
    assert _failures([{**DERIVED, "valid_from_latest": "1971-08-09"}])
    assert _failures([{**DERIVED, "valid_to_earliest": "1973-05-01"}])


def test_an_open_span_has_no_end_interval() -> None:
    assert _failures([{**OPEN, "valid_to_earliest": "2025-01-01"}])


# The substitutes (#501 D5): nested in a principal's tenure on the same seat, both
# edges dated.

BENSON = _row(
    span_key="benson",
    role_key="seat:senate:ld-6",
    entity_id="01B",
    span_start_biennium="2005-06",
    span_end_biennium="2005-06",
    valid_from="2005-01-01",
    valid_to="2006-12-31",
)
WYSS = _row(
    span_key="wyss",
    role_key="seat:senate:ld-6",
    entity_id="01W",
    span_start_biennium="2005-06",
    span_end_biennium="2005-06",
    valid_from="2005-04-14",
    valid_to="2005-04-16",
    start_basis="dated",
    end_basis="dated",
    membership_type="substitute",
)


def test_a_nested_dated_substitute_passes() -> None:
    assert _failures([BENSON, WYSS], SUBSTITUTES_SQL) == []


def test_a_substitute_with_no_principal_fails() -> None:
    assert len(_failures([WYSS], SUBSTITUTES_SQL)) == 1


def test_a_substitute_outliving_its_principal_fails() -> None:
    assert _failures([BENSON, {**WYSS, "valid_to": "2007-01-01"}], SUBSTITUTES_SQL)


def test_an_undated_substitute_fails() -> None:
    assert _failures([BENSON, {**WYSS, "start_basis": "observed"}], SUBSTITUTES_SQL)
