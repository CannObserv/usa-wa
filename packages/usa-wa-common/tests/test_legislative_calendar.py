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
    WA_TERMS,
    filing_biennium,
    filing_biennium_on,
    filing_window,
    harvest_biennium,
    term_biennium_on,
    term_end,
    term_start,
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
    "label,start",
    [
        ("2025-26", date(2025, 1, 13)),
        ("2027-28", date(2027, 1, 11)),
        ("2029-30", date(2029, 1, 8)),
        ("1981-82", date(1981, 1, 12)),  # the statute's own year (1981 c 288): the floor
        ("1979-80", date(1979, 1, 1)),  # before the floor: the calendar year (#604)
        ("1889-90", date(1889, 1, 1)),
    ],
)
def test_a_term_starts_on_the_second_monday_in_january_from_1981(label, start):
    """#598: RCW 44.04.021, "The regular term of office of each senator and representative
    shall commence on the second Monday in January following the date of election." Owner
    decision Q1 (2026-11-24): from 1981, the statute's date; calendar years before, until
    #604 reads the older session laws."""
    assert term_start(label) == start


@pytest.mark.parametrize(
    "label,end",
    [
        ("2025-26", date(2027, 1, 10)),  # the day before 2027-28's term starts
        ("2027-28", date(2029, 1, 7)),
        ("1979-80", date(1981, 1, 11)),  # the seam: its successor's term began 1981-01-12
        ("1977-78", date(1978, 12, 31)),  # before the floor: the calendar year
    ],
)
def test_a_term_ends_the_day_before_the_next_one_starts(label, end):
    assert term_end(label) == end


def test_consecutive_terms_meet_with_no_gap():
    for label, after in (("2023-24", "2025-26"), ("1979-80", "1981-82"), ("1977-78", "1979-80")):
        assert (term_start(after) - term_end(label)).days == 1


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 12, 7), "2025-26"),  # filing has turned; the term has not
        (date(2027, 1, 1), "2025-26"),  # #598: the calendar year no longer flips the term
        (date(2027, 1, 10), "2025-26"),
        (date(2027, 1, 11), "2027-28"),  # the second Monday in January
        (date(1981, 1, 11), "1979-80"),
        (date(1981, 1, 12), "1981-82"),
        (date(1979, 1, 1), "1979-80"),  # before the floor: calendar years
        (date(1978, 12, 31), "1977-78"),
    ],
)
def test_the_term_clock_follows_the_statute(day, expected):
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


def test_wa_terms_is_the_engine_calendar_for_the_statute():
    """#598: the span engine takes a ``TermCalendar``; WA's is the statute's."""
    assert WA_TERMS.start("2027-28") == date(2027, 1, 11)
    assert WA_TERMS.end("2025-26") == date(2027, 1, 10)
    assert WA_TERMS.biennium_on(date(2027, 1, 10)) == "2025-26"
    assert WA_TERMS.start("1979-80") == date(1979, 1, 1)


def test_the_term_clock_takes_a_datetime_as_its_day():
    """dbt hands Python models pandas Timestamps (a datetime subclass): the term clock reads
    the day, never comparing a timestamp with a date (role_periods, 2026-11-24)."""
    assert term_biennium_on(datetime(2027, 1, 10, 23, 0)) == "2025-26"
    assert term_biennium_on(datetime(2027, 1, 11, 0, 0)) == "2027-28"
