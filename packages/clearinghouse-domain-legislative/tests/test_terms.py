"""Tests for terms.py — biennium label arithmetic (#189).

Characterisation: every case here moved verbatim from the three adapter test modules
that owned the calendar before it was promoted to Layer 2 —
``test_synthesis.py`` (``parse_biennium``), ``test_refresh.py`` (``biennium_for_date``,
``biennium_start_date``, ``previous_biennium``) and ``test_harvest_committee_meetings.py``
(``bienniums_in_range``). Pure functions, no DB.
"""

from __future__ import annotations

from datetime import date

import pytest

from clearinghouse_domain_legislative.terms import (
    biennium_for_date,
    biennium_start_date,
    biennium_start_year,
    bienniums_in_range,
    parse_biennium,
    previous_biennium,
)

# ----- parse_biennium -----


def test_parse_biennium_extracts_start_and_end_years():
    """``2025-26`` → (2025, 2026)."""
    assert parse_biennium("2025-26") == (2025, 2026)


def test_parse_biennium_handles_decade_rollover():
    """``2029-30`` → (2029, 2030)."""
    assert parse_biennium("2029-30") == (2029, 2030)


def test_parse_biennium_rejects_malformed_input():
    """Anything not ``YYYY-YY`` raises ``ValueError``."""
    for bad in ["2025", "2025-2026", "25-26", "abcd-ef", ""]:
        with pytest.raises(ValueError):
            parse_biennium(bad)


# ----- biennium_for_date / start / previous -----


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2025, 1, 13), "2025-26"),
        (date(2025, 12, 31), "2025-26"),
        (date(2026, 6, 18), "2025-26"),
        (date(2026, 12, 31), "2025-26"),
        (date(2027, 1, 1), "2027-28"),
        (date(2030, 7, 4), "2029-30"),
    ],
)
def test_biennium_for_date_rolls_on_odd_years(today, expected):
    """WA bienniums start on odd years; even-year dates roll back to the start."""
    assert biennium_for_date(today) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2025-26", date(2025, 1, 1)),
        ("2027-28", date(2027, 1, 1)),
        ("2099-00", date(2099, 1, 1)),
    ],
)
def test_biennium_start_date_is_jan1_of_the_odd_year(label, expected):
    """The window boundary for a rename = the biennium's start (Jan 1 of the odd year).

    WSL exposes no real name-change date, so the boundary is the documented
    biennium-start approximation."""
    assert biennium_start_date(label) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2025-26", "2023-24"),
        ("2027-28", "2025-26"),
        ("2001-02", "1999-00"),
    ],
)
def test_previous_biennium_steps_back_two_years(label, expected):
    """The prior biennium is the rename diff's "before" side."""
    assert previous_biennium(label) == expected


def test_biennium_start_year_is_public():
    """Promoted from ``synthesis._biennium_start_year``: it was already crossing a package
    boundary (``usa_wa_sync_powermap.committee_name_chain`` imported the underscore name),
    so the leading underscore was a lie about the seam."""
    assert biennium_start_year("2025-26") == 2025
    assert biennium_start_year("1999-00") == 1999


# ----- bienniums_in_range -----


def test_bienniums_in_range_walks_odd_years_inclusive():
    assert bienniums_in_range("2021-22", "2025-26") == ["2021-22", "2023-24", "2025-26"]
    assert bienniums_in_range("2025-26", "2025-26") == ["2025-26"]


def test_bienniums_in_range_rejects_reversed():
    with pytest.raises(ValueError, match="after"):
        bienniums_in_range("2025-26", "2023-24")
