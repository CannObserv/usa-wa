"""Unit tests for the central WSL request rate limiter (#77).

The limiter is a global min-interval gate every SOAP POST passes through so no caller can
burst against the single WSL host. Tested deterministically with an injected clock — no
real sleeping.
"""

from __future__ import annotations

from usa_wa_adapter_legislature.transport import (
    _WSL_LIMITER,
    _RateLimiter,
    configure_wsl_rate_limit,
)


class _FakeClock:
    """Monotonic clock where `sleep(d)` advances time by `d` (models a real sleep)."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


def test_first_call_is_immediate_then_spaced() -> None:
    clock = _FakeClock()
    lim = _RateLimiter(1.0, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(3):
        lim.acquire()
    # First acquire reserves slot 0 (no wait); each subsequent one waits one interval.
    assert clock.sleeps == [1.0, 1.0]


def test_zero_interval_never_sleeps() -> None:
    clock = _FakeClock()
    lim = _RateLimiter(0.0, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(5):
        lim.acquire()
    assert clock.sleeps == []


def test_negative_interval_clamped_to_zero() -> None:
    clock = _FakeClock()
    lim = _RateLimiter(-3.0, monotonic=clock.monotonic, sleep=clock.sleep)
    lim.acquire()
    assert clock.sleeps == []


def test_elapsed_time_reduces_the_wait() -> None:
    clock = _FakeClock()
    lim = _RateLimiter(1.0, monotonic=clock.monotonic, sleep=clock.sleep)
    lim.acquire()  # slot 0, next=1.0
    clock.t = 0.75  # 0.75s of real work elapsed before the next call
    lim.acquire()  # slot 1.0, wait only the remaining 0.25s
    assert clock.sleeps == [0.25]


def test_set_interval_updates_pacing() -> None:
    clock = _FakeClock()
    lim = _RateLimiter(0.0, monotonic=clock.monotonic, sleep=clock.sleep)
    lim.acquire()  # off → no sleep
    lim.set_interval(2.0)
    lim.acquire()  # first spaced call reserves the current slot (no wait yet)
    lim.acquire()  # now waits 2.0
    assert clock.sleeps == [2.0]


def test_configure_wsl_rate_limit_sets_the_global() -> None:
    configure_wsl_rate_limit(1.5)
    assert _WSL_LIMITER._min == 1.5
    configure_wsl_rate_limit(0.0)  # restore (the autouse fixture also zeroes it)
    assert _WSL_LIMITER._min == 0.0
