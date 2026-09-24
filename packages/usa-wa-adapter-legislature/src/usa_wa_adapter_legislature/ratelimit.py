"""Shared courtesy rate-limit primitives for the legislature package's sources (#77, #236).

Package-internal infrastructure, not business logic: each source hits a *different* host (WSL
SOAP at ``wslwebservices.leg.wa.gov``, the roster PDF at ``leg.wa.gov``), so each owns its own
limiter *instance* + env knob, but they share this one implementation rather than duplicating
it. The SOS package keeps an async sibling in :mod:`usa_wa_adapter_sos.ratelimit`; a shared
Layer-1 home for both is deferred (#236), not rejected.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Thread-safe min-interval gate. `acquire()` reserves the next evenly-spaced slot under a
    lock, then sleeps (outside the lock) until it — so concurrent callers from different
    `asyncio.to_thread` threads are spaced by `min_interval` without one holding the lock while
    sleeping. `monotonic`/`sleep` are injectable for deterministic tests.

    `acquire()` blocks. An async caller dispatches it via `asyncio.to_thread` rather than
    calling it on the event loop."""

    def __init__(
        self,
        min_interval: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min = max(0.0, min_interval)
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0

    def set_interval(self, min_interval: float) -> None:
        self._min = max(0.0, min_interval)

    def acquire(self) -> None:
        if self._min <= 0:
            return
        with self._lock:
            slot = max(self._monotonic(), self._next)
            self._next = slot + self._min
        delay = slot - self._monotonic()
        if delay > 0:
            self._sleep(delay)


def env_float(name: str, default: float) -> float:
    """Read a float from env `name`, falling back to `default` on unset/malformed — a bad env
    var must not crash every caller with an import-time `ValueError`."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
