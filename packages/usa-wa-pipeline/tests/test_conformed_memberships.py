"""The curated substitute tenures (#501 D5)."""

from datetime import date

import pytest

from clearinghouse_domain_legislative.span_kinds import KIND_PARTY, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_pipeline.conformed.memberships import (
    SUBSTITUTE_TENURES,
    SubstituteTenure,
    apply_memberships,
    unmatched_memberships,
)
from usa_wa_pipeline.conformed.spans import ROSTER_SOURCE, SOURCE


def _span(member, kind=KIND_SENATE, disc="22", start="1991-92"):
    return TenureSpan(
        member_id=member,
        kind=kind,
        discriminator=disc,
        start_biennium=start,
        end_biennium=start,
        valid_from=date(1991, 1, 14),
        valid_to=date(1991, 4, 18),
        is_active=False,
        start_basis="dated",
        end_basis="dated",
    )


ENTRY = SubstituteTenure(
    source=SOURCE,
    member_id="232",
    span_kind=KIND_SENATE,
    span_discriminator="22",
    span_start_biennium="1991-92",
    substitute="Lela Kreidler",
    principal="Myron “Mike” Kreidler",
    substitute_clause="Appointed January 14, 1991 to serve until April 18, 1991",
    principal_clause="(On leave of absence for military duty Jan. 8, 1991 to April 18, 1991)",
    edition="legroster:2025-06-05",
)


def test_the_table_holds_the_three_military_substitutes() -> None:
    assert sorted((t.substitute, t.span_start_biennium) for t in SUBSTITUTE_TENURES) == [
        ("Jon Wyss", "2005-06"),
        ("Lela Kreidler", "1991-92"),
        ("Marlo Braun", "2017-18"),
    ]


def test_every_entry_carries_both_halves_of_its_evidence() -> None:
    for entry in SUBSTITUTE_TENURES:
        assert entry.substitute_clause and entry.principal_clause and entry.edition
        assert entry.span_kind == KIND_SENATE


def test_entries_are_distinct_spans() -> None:
    keys = [t.key for t in SUBSTITUTE_TENURES]
    assert len(keys) == len(set(keys))


def test_the_named_span_becomes_a_substitute() -> None:
    families = {SOURCE: [_span("232"), _span("232", kind=KIND_PARTY, disc="democratic")]}
    out = apply_memberships(families, (ENTRY,))
    seat, party = out[SOURCE]
    assert seat.membership_type == "substitute"
    assert party.membership_type == "principal"  # the seat only, never the party


def test_the_source_is_part_of_the_key() -> None:
    """The families live in disjoint identity spaces (CR 58)."""
    out = apply_memberships({ROSTER_SOURCE: [_span("232")]}, (ENTRY,))
    assert out[ROSTER_SOURCE][0].membership_type == "principal"


def test_another_tenure_of_the_member_is_untouched() -> None:
    out = apply_memberships({SOURCE: [_span("232", start="1993-94")]}, (ENTRY,))
    assert out[SOURCE][0].membership_type == "principal"


def test_an_entry_matching_no_span_is_reported() -> None:
    """An inert entry is a stale adjudication; the model refuses to build on one."""
    families = apply_memberships({SOURCE: [_span("999")]}, (ENTRY,))
    assert unmatched_memberships(families, (ENTRY,)) == [ENTRY]
    assert (
        unmatched_memberships(apply_memberships({SOURCE: [_span("232")]}, (ENTRY,)), (ENTRY,)) == []
    )


@pytest.mark.parametrize("field", ["substitute_clause", "principal_clause", "edition"])
def test_an_entry_without_evidence_is_refused(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        SubstituteTenure(**{**ENTRY.__dict__, field: ""})
