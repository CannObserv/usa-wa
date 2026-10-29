"""The curated never-seated rows (#546)."""

from datetime import date

import pytest

from clearinghouse_domain_legislative.span_kinds import KIND_PARTY, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_pipeline.conformed.never_seated import (
    NEVER_SEATED,
    NeverSeated,
    without_never_seated,
)
from usa_wa_pipeline.conformed.spans import ROSTER_SOURCE, SOURCE


def _span(member, kind=KIND_SENATE, disc="39", start="1941-42"):
    return TenureSpan(
        member_id=member,
        kind=kind,
        discriminator=disc,
        start_biennium=start,
        end_biennium=start,
        valid_from=date(1941, 1, 1),
        valid_to=date(1942, 12, 31),
        is_active=False,
        start_basis="observed",
        end_basis="observed",
    )


ENTRY = NeverSeated(
    member_id="lenuswestman:1941",
    name="Lenus Westman",
    district="39",
    party="democratic",
    span_start_biennium="1941-42",
    clause="Elected but not seated by vote of the Senate, January 24, 1941",
    edition="legroster:2025-06-05",
)


def _both(member="lenuswestman:1941", start="1941-42"):
    return [
        _span(member, start=start),
        _span(member, kind=KIND_PARTY, disc="democratic", start=start),
    ]


def test_the_table_holds_the_three_senators_who_never_sat() -> None:
    assert sorted((e.name, e.district, e.span_start_biennium) for e in NEVER_SEATED) == [
        ("C. A. Couplin", "9", "1921-22"),
        ("Lenus Westman", "39", "1941-42"),
        ("William P. Mulligan", "39", "1943-44"),
    ]


def test_every_entry_carries_its_evidence() -> None:
    for entry in NEVER_SEATED:
        assert entry.clause and entry.edition


def test_entries_are_distinct_rows() -> None:
    keys = [key for entry in NEVER_SEATED for key in entry.keys]
    assert len(keys) == len(set(keys))


def test_an_entry_names_its_seat_and_its_party_span() -> None:
    """None of them held a party seat either: both spans come from the one row."""
    assert ENTRY.keys == frozenset(
        {
            (ROSTER_SOURCE, "lenuswestman:1941", KIND_SENATE, "39", "1941-42"),
            (ROSTER_SOURCE, "lenuswestman:1941", KIND_PARTY, "democratic", "1941-42"),
        }
    )


def test_both_spans_of_the_named_row_are_removed() -> None:
    other = _span("patrickcrane:1941")
    families, stale = without_never_seated({ROSTER_SOURCE: [*_both(), other]}, (ENTRY,))
    assert families == {ROSTER_SOURCE: [other]}
    assert stale == []


def test_another_tenure_of_the_member_stays() -> None:
    later = _both(start="1945-46")
    families, _ = without_never_seated({ROSTER_SOURCE: [*_both(), *later]}, (ENTRY,))
    assert families[ROSTER_SOURCE] == later


def test_the_source_is_part_of_the_key() -> None:
    """The families live in disjoint identity spaces (CR 58)."""
    families, stale = without_never_seated({SOURCE: _both()}, (ENTRY,))
    assert families == {SOURCE: _both()}
    assert stale == [ENTRY]


def test_an_entry_missing_either_span_is_stale() -> None:
    """A re-keyed span would publish again under its new key: the model refuses to
    build on an entry that removed less than it names."""
    seat_only = [_span("lenuswestman:1941")]
    _, stale = without_never_seated({ROSTER_SOURCE: seat_only}, (ENTRY,))
    assert stale == [ENTRY]


@pytest.mark.parametrize("field", ["clause", "edition"])
def test_an_entry_without_evidence_is_refused(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        NeverSeated(**{**ENTRY.__dict__, field: ""})
