"""Backoff schedule tests (engine step 3)."""

from datetime import datetime, timedelta

from clearinghouse_sync_powermap.retry import MAX_DELAY, backoff, next_attempt_at


def test_backoff_doubles():
    assert backoff(1) == timedelta(seconds=60)
    assert backoff(2) == timedelta(seconds=120)
    assert backoff(3) == timedelta(seconds=240)


def test_backoff_floor():
    assert backoff(0) == timedelta(seconds=60)
    assert backoff(-5) == timedelta(seconds=60)


def test_backoff_caps():
    assert backoff(100) == MAX_DELAY


def test_next_attempt_at_adds_delay():
    now = datetime(2026, 6, 4, 12, 0, 0)
    assert next_attempt_at(now, 1) == now + timedelta(seconds=60)
