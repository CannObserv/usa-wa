"""The committee-lineage gates (#447): INV2 on `organizations`, and the edge key
and distinct ends on `org_lineage`.

Same harness split as ``test_inactive_orgs_have_no_live_members``: each predicate
is read from the shipped SQL rather than restated.
"""

import re
from pathlib import Path

import duckdb

TESTS_DIR = Path(__file__).resolve().parents[1] / "dbt" / "tests"

LINEAGE_DDL = (
    "create table org_lineage (subject_entity_id varchar, slug varchar, "
    "linked_entity_id varchar, subject_source_id varchar, linked_source_id varchar, "
    "effective_year bigint, evidence_url varchar, notes varchar)"
)


def _predicate(name: str) -> str:
    sql = (TESTS_DIR / name).read_text()
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\{\{\s*ref\('(\w+)'\)\s*\}\}", r"\1", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {name}"
    return sql


def _failures(name: str, lineage: list[tuple], orgs: list[tuple] = ()) -> list[tuple]:
    con = duckdb.connect(":memory:")
    con.execute(LINEAGE_DDL)
    con.execute("create table organizations (entity_id varchar, org_type varchar, active boolean)")
    if lineage:
        con.executemany("insert into org_lineage values (?, ?, ?, ?, ?, ?, ?, ?)", lineage)
    if orgs:
        con.executemany("insert into organizations values (?, ?, ?)", orgs)
    return con.execute(_predicate(name)).fetchall()


def _edge(subject: str, slug: str, linked: str, year: int = 2026) -> tuple:
    return (f"E{subject}", slug, f"E{linked}", subject, linked, year, "https://x.test/", None)


INV2 = "organizations_succeeded_are_inactive.sql"


def test_an_active_succeeded_predecessor_fails_inv2() -> None:
    """The Civic Health shape (35341 → 36500), had the derivation not run."""
    failures = _failures(
        INV2,
        [_edge("35341", "succeeded_by", "36500")],
        [("E35341", "other", True), ("E36500", "other", True)],
    )
    assert [f[0] for f in failures] == ["E35341"]


def test_an_active_merged_predecessor_fails_inv2() -> None:
    failures = _failures(
        INV2,
        [_edge("1", "merged_with", "2")],
        [("E1", "committee", True), ("E2", "committee", True)],
    )
    assert len(failures) == 1


def test_a_live_split_child_passes_inv2() -> None:
    """`split_from` is exempt: a split's two heads both stay live (#124 OQ3)."""
    assert (
        _failures(
            INV2,
            [_edge("2", "split_from", "1")],
            [("E1", "committee", True), ("E2", "committee", True)],
        )
        == []
    )


def test_a_retired_predecessor_passes_inv2() -> None:
    assert (
        _failures(
            INV2,
            [_edge("35341", "succeeded_by", "36500")],
            [("E35341", "other", False), ("E36500", "other", True)],
        )
        == []
    )


def test_one_row_per_predecessor_not_per_link() -> None:
    """A predecessor that split three ways is one failure, not three."""
    failures = _failures(
        INV2,
        [_edge("1", "succeeded_by", "2"), _edge("1", "merged_with", "3")],
        [("E1", "committee", True)],
    )
    assert len(failures) == 1


KEY = "org_lineage_key.sql"


def test_one_edge_attested_twice_fails_the_key() -> None:
    """The published identity is the (subject, slug, linked) edge — a year-only
    correction refines it in place (#127). Two current rows on one edge are two
    years asserted at once; supersede one."""
    failures = _failures(
        KEY, [_edge("1", "succeeded_by", "2", 2001), _edge("1", "succeeded_by", "2", 2003)]
    )
    assert len(failures) == 1


def test_one_pair_under_two_slugs_is_two_edges() -> None:
    """Production: 8265 split_from 438 AND 8265 succeeded_by 438 (#124 #15)."""
    assert (
        _failures(KEY, [_edge("8265", "split_from", "438"), _edge("8265", "succeeded_by", "438")])
        == []
    )


ENDS = "org_lineage_distinct_ends.sql"


def test_a_link_whose_ends_merged_into_one_entity_fails() -> None:
    """The DB bars `A → A` on WSL ids; a registry merge of A and B can still make
    `A → B` a self-link once resolved, which no consumer can apply."""
    failures = _failures(
        ENDS, [("E1", "succeeded_by", "E1", "1", "2", 2001, "https://x.test/", None)]
    )
    assert len(failures) == 1


def test_distinct_ends_pass() -> None:
    assert _failures(ENDS, [_edge("1", "succeeded_by", "2")]) == []
