"""The citations coverage probe (#313).

Written against the BUILT duckdb rather than by recomputing the join: the
artifact is what a consumer reads, so verifying the artifact is the only check
that can catch a binder that dropped an input the pure function handles fine.
"""

import duckdb
import pytest

from usa_wa_pipeline import parity_citations

WSL = "usa_wa_legislature"
ROSTER = "usa_wa_legislature_roster"


def _db(tmp_path, *, citations, fetches, persons=(), orgs=(), roles=(), assignments=(), keys=()):
    path = str(tmp_path / "pipeline.duckdb")
    con = duckdb.connect(path)
    con.execute(
        "create table citations(entity_type varchar, entity_id varchar, "
        "source varchar, resource_id varchar)"
    )
    con.execute("create table stg_raw_fetches(source varchar, resource_id varchar, sha256 varchar)")
    con.execute("create table persons(entity_id varchar)")
    con.execute("create table organizations(entity_id varchar)")
    con.execute("create table roles(entity_id varchar)")
    con.execute(
        "create table assignments(member_id varchar, span_kind varchar, "
        "span_discriminator varchar, span_start_biennium varchar)"
    )
    con.execute("create table org_crosswalk(entity_id varchar, key_value varchar)")
    for row in citations:
        con.execute("insert into citations values (?, ?, ?, ?)", list(row))
    for row in fetches:
        con.execute("insert into stg_raw_fetches values (?, ?, ?)", list(row))
    for table, rows in (("persons", persons), ("organizations", orgs), ("roles", roles)):
        for row in rows:
            con.execute(f"insert into {table} values (?)", [row])  # noqa: S608
    for row in assignments:
        con.execute("insert into assignments values (?, ?, ?, ?)", list(row))
    for row in keys:
        con.execute("insert into org_crosswalk values (?, ?)", list(row))
    con.close()
    return path


FETCH = (WSL, "sponsors:2019-20", "abc")
CITED_PERSON = ("person", "01A", WSL, "sponsors:2019-20")


def test_a_fully_cited_corpus_is_clean(tmp_path) -> None:
    path = _db(tmp_path, citations=[CITED_PERSON], fetches=[FETCH], persons=["01A"])
    counters, failures = parity_citations.audit(path)
    assert failures == []
    assert counters["citations"] == 1
    assert counters["uncited_persons"] == 0


def test_a_citation_with_no_attestation_fails(tmp_path) -> None:
    """The chain's whole point is that a citation resolves to bytes. One that
    names a resource `stg_raw_fetches` does not carry is a dangling pointer —
    and `/provenance` would answer it with nulls rather than an error."""
    path = _db(tmp_path, citations=[CITED_PERSON], fetches=[], persons=["01A"])
    counters, failures = parity_citations.audit(path)
    assert counters["orphan_citations"] == 1
    assert "orphan_citations" in failures


def test_an_uncited_assignment_fails(tmp_path) -> None:
    path = _db(
        tmp_path,
        citations=[CITED_PERSON],
        fetches=[FETCH],
        persons=["01A"],
        assignments=[("100", "party", "D", "2019-20")],
    )
    counters, failures = parity_citations.audit(path)
    assert counters["uncited_assignments"] == 1
    assert "uncited_assignments" in failures


def test_an_assignment_cited_by_its_span_key_is_clean(tmp_path) -> None:
    """The assignment's published identity is the 4-part span source_id, so the
    probe has to reassemble it exactly as the artifact spells it."""
    path = _db(
        tmp_path,
        citations=[("assignment", "100:party:D:2019-20", *FETCH[:2])],
        fetches=[FETCH],
        assignments=[("100", "party", "D", "2019-20")],
    )
    counters, failures = parity_citations.audit(path)
    assert counters["uncited_assignments"] == 0
    assert failures == []


def test_a_structural_org_is_not_a_coverage_gap(tmp_path) -> None:
    """The Legislature, the chambers and the parties are definitional — read
    off `usa_wa_common.orgs`, never off a wire. Counting them as uncited would
    put a permanent floor of 11 under a counter gated at zero."""
    path = _db(
        tmp_path,
        citations=[],
        fetches=[],
        orgs=["01ORG"],
        keys=[("01ORG", "party-republican")],
    )
    counters, failures = parity_citations.audit(path)
    assert counters["structural_organizations"] == 1
    assert counters["uncited_organizations"] == 0
    assert failures == []


def test_a_sourced_org_with_no_citation_fails(tmp_path) -> None:
    path = _db(
        tmp_path,
        citations=[],
        fetches=[],
        orgs=["01ORG"],
        keys=[("01ORG", "5")],
    )
    counters, failures = parity_citations.audit(path)
    assert counters["uncited_organizations"] == 1
    assert "uncited_organizations" in failures


def test_uncited_persons_are_ratcheted_not_gated(tmp_path) -> None:
    """Three today, each understood: one WSL member no staging row names, and
    the two Elmer E. Johnstons whose shared fold the citer refuses to guess at.
    A zero gate would be a lie; an unwatched counter would let the number grow."""
    path = _db(tmp_path, citations=[], fetches=[], persons=["01A", "01B", "01C"])
    counters, failures = parity_citations.audit(path)
    assert counters["uncited_persons"] == 3
    assert failures == []


def test_a_person_gap_past_the_baseline_fails(tmp_path) -> None:
    path = _db(tmp_path, citations=[], fetches=[], persons=["01A", "01B", "01C", "01D"])
    counters, failures = parity_citations.audit(path)
    assert counters["uncited_persons"] == 4
    assert "uncited_persons" in failures


def test_the_baseline_is_a_ceiling_not_an_expectation(tmp_path) -> None:
    """A run that cites MORE than the baseline expects is progress, not drift."""
    path = _db(tmp_path, citations=[], fetches=[], persons=["01A"])
    _, failures = parity_citations.audit(path)
    assert failures == []


def test_a_missing_artifact_is_a_failure_not_an_empty_pass(tmp_path) -> None:
    """An unbuilt table must never read as 'nothing uncited' — that is the one
    shape a coverage probe exists to refuse."""
    path = str(tmp_path / "empty.duckdb")
    duckdb.connect(path).close()
    with pytest.raises(parity_citations.ArtifactMissing):
        parity_citations.audit(path)


def test_an_unregistered_role_is_not_a_coverage_gap(tmp_path) -> None:
    """CR 98: `roles.entity_id` is null for exactly ONE build — the nightly runs
    `dbt build -> registrar -> publish`, so a brand-new seat is unregistered in
    the build that first sees it and bound by the next (conformed/schema.yml
    says so, which is why that column carries only a `unique` test).

    Counting it as uncited put a zero gate under a documented, self-healing
    state: creating a committee would fail the nightly and email the operator.
    """
    path = _db(tmp_path, citations=[], fetches=[], roles=[None])
    counters, failures = parity_citations.audit(path)
    assert counters["unregistered_roles"] == 1
    assert counters["uncited_roles"] == 0
    assert failures == []


def test_a_registered_role_with_no_citation_still_fails(tmp_path) -> None:
    """The exemption is for the null, not for the role: a role the registrar
    HAS bound and nothing attests is the integrity break the gate exists for."""
    path = _db(tmp_path, citations=[], fetches=[], roles=["01ROLE"])
    counters, failures = parity_citations.audit(path)
    assert counters["uncited_roles"] == 1
    assert "uncited_roles" in failures
