"""The two WA legislative clocks (#597): the filing window and the term.

The filing window is the cohort's rule (CannObserv/cli ``co_cli/ext/wa_leg/calendar.py``,
cli#1268, operator rule 2026-11-10): a biennium opens at 00:00 Pacific on the first Monday in
December before its first year, when bill pre-filing opens, and runs to the day before the
next one. The term clock is when legislators serve; #598 moves it to RCW 44.04.021.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from usa_wa_common.legislative_calendar import (
    SITE_TZ,
    filing_biennium,
    filing_biennium_on,
    filing_window,
    harvest_biennium,
    term_biennium_on,
)


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 12, 6), "2025-26"),  # the day before pre-filing opens
        (date(2026, 12, 7), "2027-28"),  # first Monday in December 2026
        (date(2026, 12, 31), "2027-28"),
        (date(2027, 6, 1), "2027-28"),
        (date(2028, 12, 3), "2027-28"),  # the day before the next first Monday
        (date(2028, 12, 4), "2029-30"),
        (date(2024, 12, 2), "2025-26"),
        (date(2025, 1, 1), "2025-26"),  # the old Jan-1 window starts map to the same biennium
        (date(1998, 12, 7), "1999-00"),  # first Monday in December 1998; the century wraps
    ],
)
def test_the_filing_biennium_turns_on_the_first_monday_in_december(day, expected):
    assert filing_biennium_on(day) == expected


def test_a_moment_is_judged_on_the_pacific_calendar():
    """The nightly fires at 08:00 UTC, which on 2026-12-07 is exactly 00:00 PST."""
    assert filing_biennium(datetime(2026, 12, 7, 7, 59, 59, tzinfo=UTC)) == "2025-26"
    assert filing_biennium(datetime(2026, 12, 7, 8, 0, 0, tzinfo=UTC)) == "2027-28"
    assert filing_biennium(datetime(2026, 12, 7, 0, 0, tzinfo=SITE_TZ)) == "2027-28"


def test_a_naive_moment_is_refused():
    """All UTC (AGENTS.md): a naive datetime has no calendar to be judged on."""
    with pytest.raises(ValueError, match="aware"):
        filing_biennium(datetime(2026, 12, 7, 12, 0))


@pytest.mark.parametrize(
    "label,first,last",
    [
        ("2025-26", date(2024, 12, 2), date(2026, 12, 6)),
        ("2027-28", date(2026, 12, 7), date(2028, 12, 3)),
        ("1999-00", date(1998, 12, 7), date(2000, 12, 3)),
    ],
)
def test_the_filing_window_is_first_and_last_day_inclusive(label, first, last):
    assert filing_window(label) == (first, last)


def test_consecutive_filing_windows_meet_with_no_gap():
    _, last = filing_window("2025-26")
    first, _ = filing_window("2027-28")
    assert (first - last).days == 1


@pytest.mark.parametrize("label", ["2026-27", "2025-27", "25-26"])
def test_a_label_that_is_not_a_biennium_is_refused(label):
    with pytest.raises(ValueError):
        filing_window(label)


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 12, 7), "2025-26"),  # filing has turned; the term has not
        (date(2026, 12, 31), "2025-26"),
        (date(2027, 1, 1), "2027-28"),  # calendar-year terms until #598
    ],
)
def test_the_term_clock_lags_the_filing_clock(day, expected):
    assert term_biennium_on(day) == expected


def test_the_harvest_target_is_the_filing_biennium(monkeypatch):
    """The three raw harvests target the filing biennium (#597): from 2026-12-07, 2027-28.
    ``USA_WA_BIENNIUM`` still pins it, and with no ``today`` it reads the clock."""
    monkeypatch.delenv("USA_WA_BIENNIUM", raising=False)
    assert harvest_biennium(date(2026, 12, 6)) == "2025-26"
    assert harvest_biennium(date(2026, 12, 7)) == "2027-28"
    assert harvest_biennium(None) == filing_biennium(datetime.now(UTC))
    monkeypatch.setenv("USA_WA_BIENNIUM", "2019-20")
    assert harvest_biennium(date(2026, 12, 7)) == "2019-20"
