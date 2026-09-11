"""Party membership survives a gap in elected service (#289)."""

from datetime import date

from clearinghouse_domain_legislative.span_kinds import (
    KIND_COMMITTEE,
    KIND_HOUSE,
    KIND_PARTY,
    KIND_SENATE,
)
from clearinghouse_domain_legislative.tenure_spans import (
    TenureSpan,
    merge_party_continuity,
)


def _span(
    kind: str,
    discriminator: str,
    start: str,
    end: str,
    valid_from: date,
    valid_to: date | None,
    *,
    member_id: str = "17158",
    is_active: bool = False,
) -> TenureSpan:
    return TenureSpan(
        member_id=member_id,
        kind=kind,
        discriminator=discriminator,
        start_biennium=start,
        end_biennium=end,
        valid_from=valid_from,
        valid_to=valid_to,
        is_active=is_active,
    )


def test_a_gap_in_service_does_not_break_party_membership() -> None:
    """Liz Pike: a `departed` at the end of an appointed term split her party
    span, and she was a Republican across the 25-day interregnum.

    The merged span keys on the EARLIER start, so the surviving `source_id` is
    the one already shipped and the tail is what retracts."""
    first = _span(
        KIND_PARTY, "republican", "2011-12", "2011-12", date(2011, 1, 1), date(2012, 12, 7)
    )
    second = _span(
        KIND_PARTY, "republican", "2013-14", "2017-18", date(2013, 1, 1), date(2018, 12, 31)
    )

    [merged] = merge_party_continuity([first, second])

    assert merged.start_biennium == "2011-12"
    assert merged.end_biennium == "2017-18"
    assert merged.valid_from == date(2011, 1, 1)
    assert merged.valid_to == date(2018, 12, 31)
    assert merged.source_id == first.source_id


def test_no_upper_bound_on_the_gap() -> None:
    """Margaret Hurley sat out 400 days; Elmer Huntley 759. Neither stopped
    being what they were. Party continuity is not a function of how long the
    absence was — only of whether the affiliation itself changed (#289)."""
    first = _span(
        KIND_PARTY, "republican", "1957-58", "1963-64", date(1957, 1, 1), date(1965, 3, 26)
    )
    second = _span(
        KIND_PARTY, "republican", "1967-68", "1971-72", date(1967, 4, 24), date(1972, 12, 31)
    )

    [merged] = merge_party_continuity([first, second])

    assert merged.valid_from == date(1957, 1, 1)
    assert merged.valid_to == date(1972, 12, 31)


def test_an_intervening_party_is_a_documented_change_and_blocks_the_merge() -> None:
    """The one thing that DOES break continuity: the member is attested under
    another party in between. Merging the two Republican spans across it would
    assert two affiliations at once and erase the switch."""
    r1 = _span(KIND_PARTY, "republican", "1991-92", "1993-94", date(1991, 1, 1), date(1994, 12, 31))
    d = _span(KIND_PARTY, "democratic", "1995-96", "1997-98", date(1995, 1, 1), date(1998, 12, 31))
    r2 = _span(KIND_PARTY, "republican", "1999-00", "2001-02", date(1999, 1, 1), date(2002, 12, 31))

    merged = merge_party_continuity([r1, d, r2])

    assert len(merged) == 3
    assert {s.start_biennium for s in merged} == {"1991-92", "1995-96", "1999-00"}


def test_an_open_tail_keeps_the_merged_span_open() -> None:
    first = _span(
        KIND_PARTY, "democratic", "2017-18", "2023-24", date(2017, 1, 1), date(2024, 12, 5)
    )
    second = _span(
        KIND_PARTY, "democratic", "2025-26", "2025-26", date(2025, 1, 1), None, is_active=True
    )

    [merged] = merge_party_continuity([first, second])

    assert merged.valid_to is None
    assert merged.is_active is True


def test_seats_and_committees_are_untouched() -> None:
    """Only party membership survives a gap. A seat tenure that ends IS over —
    a returning member holds the seat twice, and a committee seat likewise."""
    spans = [
        _span(KIND_SENATE, "9", "1957-58", "1963-64", date(1957, 1, 1), date(1965, 3, 26)),
        _span(KIND_SENATE, "9", "1967-68", "1971-72", date(1967, 4, 24), date(1972, 12, 31)),
        _span(
            KIND_HOUSE,
            "ld-18-position-1",
            "2011-12",
            "2011-12",
            date(2012, 8, 23),
            date(2012, 12, 7),
        ),
        _span(KIND_COMMITTEE, "3532", "2017-18", "2017-18", date(2017, 1, 1), date(2018, 12, 31)),
        _span(KIND_COMMITTEE, "3532", "2021-22", "2021-22", date(2021, 1, 1), date(2022, 12, 31)),
    ]

    # the output is re-sorted the way `build_tenure_spans` sorts its own, so
    # compare as a collection rather than pinning an incidental order
    assert sorted(merge_party_continuity(spans), key=str) == sorted(spans, key=str)


def test_two_members_do_not_merge_into_each_other() -> None:
    a = _span(KIND_PARTY, "democratic", "2011-12", "2011-12", date(2011, 1, 1), date(2012, 12, 31))
    b = _span(
        KIND_PARTY,
        "democratic",
        "2015-16",
        "2015-16",
        date(2015, 1, 1),
        date(2016, 12, 31),
        member_id="99999",
    )

    assert len(merge_party_continuity([a, b])) == 2


def test_overlapping_same_party_spans_still_collapse_to_one() -> None:
    """Defensive: whatever produced an overlap, two spans of one affiliation are
    one fact, and the merged window is the union."""
    first = _span(
        KIND_PARTY, "democratic", "2011-12", "2013-14", date(2011, 1, 1), date(2014, 12, 31)
    )
    second = _span(
        KIND_PARTY, "democratic", "2013-14", "2015-16", date(2013, 1, 1), date(2016, 12, 31)
    )

    [merged] = merge_party_continuity([first, second])

    assert merged.valid_from == date(2011, 1, 1)
    assert merged.valid_to == date(2016, 12, 31)
