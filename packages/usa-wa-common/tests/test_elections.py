"""The WA general-election calendar (#189).

Cases moved verbatim from `usa-wa-adapter-pdc/tests/test_adapter.py`, where they were
testing an archive-only SODA adapter for arithmetic that has nothing to do with PDC.
"""

from __future__ import annotations

from datetime import date

from usa_wa_common.elections import (
    election_year_for_biennium,
    election_years_for_biennium,
    general_election_day,
    lookahead_election_year,
    seating_biennium_for_election_year,
    senate_election_years_for_biennium,
)


def test_election_year_for_biennium() -> None:
    assert election_year_for_biennium("2025-26") == 2024
    assert election_year_for_biennium("2013-14") == 2012


def test_seating_biennium_for_election_year_is_inverse() -> None:
    assert seating_biennium_for_election_year(2024) == "2025-26"
    assert seating_biennium_for_election_year(2012) == "2013-14"
    for biennium in ("2025-26", "2013-14", "1999-00"):
        assert seating_biennium_for_election_year(election_year_for_biennium(biennium)) == biennium


def test_seating_biennium_for_odd_year_special_is_mid_biennium() -> None:
    """#121: an odd-year November special seats the biennium *starting* that year, mid-term
    (Nov 2025 seated Hunt/Krishnadasan/Zahn into 2025-26) — not the next biennium."""
    assert seating_biennium_for_election_year(2025) == "2025-26"
    assert seating_biennium_for_election_year(2013) == "2013-14"
    # every year in a biennium's decisive set seats THAT biennium
    for biennium in ("2025-26", "2013-14"):
        for year in election_years_for_biennium(biennium):
            assert seating_biennium_for_election_year(year) == biennium


def test_senate_election_years_for_biennium() -> None:
    """#75 staggered evens + the #121 odd mid-biennium special (Nov 2025: Hunt LD5 et al.)."""
    assert senate_election_years_for_biennium("2025-26") == (2024, 2022, 2025)


def test_election_years_for_biennium_spans_the_seating_and_special_generals() -> None:
    """Every general-election year a biennium's membership can be decided by (#106): the even
    ``start-1`` that seated it, plus the odd ``start`` whose November general fills mid-biennium
    vacancies by special (Hunt, LD5 Senate, Nov 2025). November of ``start+1`` is *excluded* — it
    seats the NEXT biennium, not this one."""
    assert election_years_for_biennium("2025-26") == [2024, 2025]
    assert election_years_for_biennium("2013-14") == [2012, 2013]
    # the seating year always leads, so a consumer archiving in order writes the even cohort first
    assert election_years_for_biennium("2025-26")[0] == election_year_for_biennium("2025-26")


def test_general_election_day_is_the_first_tuesday_after_the_first_monday() -> None:
    """A Tuesday Nov 1 is NOT election day (2022: Nov 8); a Monday Nov 1 makes it Nov 2 (2021)."""
    assert general_election_day(2026) == date(2026, 11, 3)
    assert general_election_day(2024) == date(2024, 11, 5)
    assert general_election_day(2022) == date(2022, 11, 8)
    assert general_election_day(2021) == date(2021, 11, 2)
    assert general_election_day(2012) == date(2012, 11, 6)


def test_lookahead_is_the_next_bienniums_seating_election_once_held() -> None:
    """#135 early capture: the November of ``start+1`` seats the NEXT biennium, so
    :func:`election_years_for_biennium` excludes it and a date-current harvest would not
    fetch its winners until Jan 1. From the day after it is held, it is the lookahead."""
    assert lookahead_election_year("2025-26", today=date(2026, 11, 4)) == 2026
    assert lookahead_election_year("2025-26", today=date(2026, 12, 31)) == 2026
    assert lookahead_election_year("2025-26", today=date(2026, 11, 4)) not in (
        election_years_for_biennium("2025-26")
    )


def test_no_lookahead_on_or_before_election_day() -> None:
    """The nightly runs 08:00 UTC — midnight in WA, before the polls close — so election day
    itself has no returns to fetch."""
    assert lookahead_election_year("2025-26", today=date(2026, 11, 3)) is None
    assert lookahead_election_year("2025-26", today=date(2026, 9, 30)) is None
    assert lookahead_election_year("2025-26", today=date(2025, 12, 1)) is None


def test_the_lookahead_is_the_year_the_next_biennium_harvests_natively() -> None:
    """The hand-off at the rollover is seamless: from Jan 1 the next biennium's own
    decisive set leads with the year the lookahead was fetching."""
    lookahead = lookahead_election_year("2025-26", today=date(2026, 12, 31))
    assert election_years_for_biennium("2027-28")[0] == lookahead
    assert senate_election_years_for_biennium("2027-28")[0] == lookahead
    assert lookahead_election_year("2027-28", today=date(2027, 1, 1)) is None
