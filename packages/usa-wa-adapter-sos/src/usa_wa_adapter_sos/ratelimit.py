"""Shared courtesy rate-limit primitives for the WA SOS sources (the #77 pattern).

Package-internal infrastructure, not business logic: each SOS **source** (``filings``,
``results``) hits a *different* upstream host, so each owns its own limiter *instance* + env knob,
but they share this one implementation rather than duplicating it. A generic async min-interval
gate + a fault-tolerant env-float reader.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any


class AsyncRateLimiter:
    """Async min-interval gate. :meth:`acquire` reserves the next evenly-spaced slot under a
    lock, then sleeps (outside the lock) until it, so sequential callers are spaced by
    ``min_interval``. ``monotonic``/``sleep`` are injectable for deterministic tests."""

    def __init__(
        self,
        min_interval: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._min = max(0.0, min_interval)
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next = 0.0

    def set_interval(self, min_interval: float) -> None:
        self._min = max(0.0, min_interval)

    async def acquire(self) -> None:
        if self._min <= 0:
            return
        async with self._lock:
            slot = max(self._monotonic(), self._next)
            self._next = slot + self._min
            delay = slot - self._monotonic()
        if delay > 0:
            await self._sleep(delay)


def env_float(name: str, default: float) -> float:
    """Read a float from env ``name``, falling back to ``default`` on unset/malformed — a bad
    env var must not crash every caller with an import-time ``ValueError``."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
