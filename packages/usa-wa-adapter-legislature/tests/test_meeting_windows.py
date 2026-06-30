"""Tests for meeting_windows.py — biennium → window + resource-id keying (#39)."""

from __future__ import annotations

from datetime import datetime

import pytest

from usa_wa_adapter_legislature.meeting_windows import (
    biennium_window,
    meetings_resource_id,
    parse_meetings_resource_id,
)


def test_biennium_window_spans_two_calendar_years():
    begin, end = biennium_window("2023-24")
    assert begin == datetime(2023, 1, 1, 0, 0, 0)
    assert end == datetime(2024, 12, 31, 23, 59, 59)


def test_resource_id_is_date_keyed():
    begin, end = biennium_window("2025-26")
    assert meetings_resource_id(begin, end) == "committee-meetings:2025-01-01:2026-12-31"


def test_resource_id_roundtrips_through_parse():
    begin, end = biennium_window("2025-26")
    rid = meetings_resource_id(begin, end)
    assert parse_meetings_resource_id(rid) == (begin, end)


def test_parse_rejects_a_foreign_resource_id():
    with pytest.raises(ValueError, match="not a committee-meetings"):
        parse_meetings_resource_id("committees:2025-26")
