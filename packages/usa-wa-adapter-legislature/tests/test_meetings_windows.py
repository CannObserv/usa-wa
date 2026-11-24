"""Tests for meetings/windows.py — biennium → window + resource-id keying (#39)."""

from __future__ import annotations

from datetime import datetime

import pytest

from usa_wa_adapter_legislature.meetings.windows import (
    biennium_window,
    meetings_resource_id,
    parse_meetings_resource_id,
)


def test_biennium_window_is_the_filing_window():
    """#597: a biennium's meetings window is its filing window (first Monday in December
    before it, through the day before the next), so December's interim committee days
    belong to the upcoming biennium, as they do in CannObserv/cli."""
    begin, end = biennium_window("2023-24")
    assert begin == datetime(2022, 12, 5, 0, 0, 0)
    assert end == datetime(2024, 12, 1, 23, 59, 59)


def test_consecutive_windows_meet_with_no_gap():
    _, end = biennium_window("2025-26")
    begin, _ = biennium_window("2027-28")
    assert (begin - end).total_seconds() == 1


def test_resource_id_is_date_keyed():
    begin, end = biennium_window("2025-26")
    assert meetings_resource_id(begin, end) == "committee-meetings:2024-12-02:2026-12-06"
    begin, end = biennium_window("2027-28")
    assert meetings_resource_id(begin, end) == "committee-meetings:2026-12-07:2028-12-03"


def test_resource_id_roundtrips_through_parse():
    begin, end = biennium_window("2025-26")
    rid = meetings_resource_id(begin, end)
    assert parse_meetings_resource_id(rid) == (begin, end)


def test_parse_rejects_a_foreign_resource_id():
    with pytest.raises(ValueError, match="not a committee-meetings"):
        parse_meetings_resource_id("committees:2025-26")
