"""The seat-occupancy gate (#359): one seat, one holder at a time.

The predicate is exercised by reading ``dbt/tests/assignments_seat_occupancy.sql``
and running it against synthetic rows, never by restating it here. A test that
retypes the SQL it checks is two literals with a comment claiming they agree —
the defect CR 117 caught one level up, and the reason #358's phantom could have
returned unnoticed.

**Its logic, not its schema** (CR 124). The synthetic table declares its own
columns, so a rename in the `assignments` model leaves these green; the dbt run
is what couples the predicate to the real shape. The split is deliberate — these
cases would otherwise need a built duckdb to run — but it means green here and
a passing `dbt build` say different things, and both are needed.
"""

import re
from pathlib import Path

import duckdb
import pytest

TEST_SQL = Path(__file__).resolve().parents[1] / "dbt" / "tests" / "assignments_seat_occupancy.sql"

COLUMNS = "entity_id varchar, role_key varchar, span_kind varchar, valid_from date, valid_to date"
ROSTER_COLUMNS = "district integer, chamber varchar, year integer, name varchar, annotation varchar"


def _predicate() -> str:
    """The shipped test SQL with dbt's jinja resolved against a local table.

    Statement tags (`{% set %}`) and expression tags (`{{ config }}`) both have
    to go: they configure the gate rather than select rows, and duckdb would
    choke on either. The assertion at the end is the point — if a future edit
    introduces jinja this does not understand, these tests must fail loudly
    rather than quietly run a mangled query.
    """
    sql = TEST_SQL.read_text()
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('assignments'\)\s*\}\}", "assignments", sql)
    sql = re.sub(r"\{\{\s*ref\('stg_roster_members'\)\s*\}\}", "stg_roster_members", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {TEST_SQL.name}"
    return sql


def _conflicts(rows: list[tuple], roster: list[tuple] | None = None) -> int:
    """Run the shipped predicate over synthetic spans, with an optional roster.

    The roster drives the multi-member exclusion, so most cases pass none and
    get the default single-member reading — which is the conservative one.
    """
    con = duckdb.connect()
    try:
        con.execute(f"create table assignments ({COLUMNS})")
        con.executemany("insert into assignments values (?, ?, ?, ?, ?)", rows)
        con.execute(f"create table stg_roster_members ({ROSTER_COLUMNS})")
        if roster:
            con.executemany("insert into stg_roster_members values (?, ?, ?, ?, ?)", roster)
        return len(con.execute(_predicate()).fetchall())
    finally:
        con.close()


def test_the_shipped_sql_carries_a_real_ratchet() -> None:
    """If the file moves, or the ratchet decays into a threshold that can never
    fire, fail here rather than silently exercising nothing.

    CR 123: asserting `"error_if" in text` passed for `error_if='>99999'` too —
    it read as a guard on the teeth while only proving the word was present.
    """
    assert TEST_SQL.is_file()
    sql = TEST_SQL.read_text()

    error_if = re.search(r"error_if\s*=\s*'>'\s*~\s*(\w+)", sql)
    warn_if = re.search(r"warn_if\s*=\s*'!='\s*~\s*(\w+)", sql)
    assert error_if and warn_if, "both thresholds must be present and interpolated"
    assert error_if.group(1) == warn_if.group(1), "the two thresholds must track one baseline"

    # the baseline is a plain integer per mode — not an expression that could
    # quietly become unreachable
    baselines = re.findall(r"set\s+" + error_if.group(1) + r"\s*=\s*(\d+) if .* else (\d+)", sql)
    assert baselines, "the baseline must resolve to literal integers"
    assert all(part.isdigit() for pair in baselines for part in pair)


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


def test_a_bare_multi_member_district_year_is_not_a_conflict() -> None:
    """#360: WA's 1889 legislature seated multi-member senate districts — LD-19
    carried five, all unannotated. `seat:senate:ld-N` collapses them into one
    role_key, so without this the gate reads five lawful senators as a fight."""
    spans = [
        ("a", "seat:senate:ld-19", "chamber-senate", "1889-01-01", "1890-12-31"),
        ("b", "seat:senate:ld-19", "chamber-senate", "1889-01-01", "1890-12-31"),
    ]
    roster = [(19, "senate", 1889, f"Senator {n}", None) for n in "ABCDE"]

    assert _conflicts(spans, roster) == 0
    # the same spans with no roster evidence stay a conflict: absent proof of a
    # multi-member seat, the conservative reading polices
    assert _conflicts(spans) == 1


def test_an_annotated_extra_row_is_succession_not_capacity() -> None:
    """An annotation means a SUCCESSOR within one seat, so the district is still
    single-member and a genuine overlap there must still be caught. Reading it
    as capacity would license exactly what this gate exists to find."""
    spans = [
        ("pelz", "seat:senate:ld-37", "chamber-senate", "1995-01-01", "1996-12-31"),
        ("kline", "seat:senate:ld-37", "chamber-senate", "1995-06-01", "1996-12-31"),
    ]
    roster = [
        (37, "senate", 1995, "Dwight Pelz", "Resigned January 13, 1997"),
        (37, "senate", 1995, "Adam Kline", "Appointed January 20, 1997"),
    ]
    assert _conflicts(spans, roster) == 1


def test_a_partly_annotated_district_year_is_not_multi_member() -> None:
    """One annotation explains the extra row. Ambiguity resolves toward
    policing, never toward excusing."""
    spans = [
        ("mccutcheon", "seat:senate:ld-29", "chamber-senate", "1971-01-01", "1972-12-31"),
        ("rasmussen", "seat:senate:ld-29", "chamber-senate", "1971-06-01", "1972-12-31"),
    ]
    roster = [
        (29, "senate", 1971, "John T. McCutcheon", "Deceased Aug. 9, 1971"),
        (29, "senate", 1971, "A. L. Rasmussen", None),
    ]
    assert _conflicts(spans, roster) == 1


def test_capacity_must_cover_the_overlap_not_merely_exist() -> None:
    """A district that was multi-member in 1889 is not licensed forever. The
    exclusion applies only where the bare biennium actually covers the overlap."""
    roster = [(19, "senate", 1889, f"Senator {n}", None) for n in "ABCDE"]
    later = [
        ("a", "seat:senate:ld-19", "chamber-senate", "1975-01-01", "1978-12-31"),
        ("b", "seat:senate:ld-19", "chamber-senate", "1976-01-01", "1978-12-31"),
    ]
    assert _conflicts(later, roster) == 1
