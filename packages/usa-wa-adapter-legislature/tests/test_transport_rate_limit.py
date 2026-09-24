"""Unit tests for the central WSL request rate limiter's wiring (#77).

Every SOAP POST passes through one global instance so no caller can burst against the single
WSL host. The limiter itself is tested in ``test_ratelimit.py``; this file pins the WSL
instance, its override and its env knob.
"""

from __future__ import annotations

from usa_wa_adapter_legislature.transport import (
    _WSL_LIMITER,
    DEFAULT_WSL_MIN_REQUEST_INTERVAL,
    _env_min_interval,
    configure_wsl_rate_limit,
)


def test_configure_wsl_rate_limit_sets_the_global() -> None:
    configure_wsl_rate_limit(1.5)
    assert _WSL_LIMITER._min == 1.5
    configure_wsl_rate_limit(0.0)  # restore (the autouse fixture also zeroes it)
    assert _WSL_LIMITER._min == 0.0


def test_env_min_interval_reads_valid_value(monkeypatch) -> None:
    monkeypatch.setenv("USA_WA_WSL_MIN_REQUEST_INTERVAL", "1.25")
    assert _env_min_interval() == 1.25


def test_env_min_interval_falls_back_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("USA_WA_WSL_MIN_REQUEST_INTERVAL", raising=False)
    assert _env_min_interval() == DEFAULT_WSL_MIN_REQUEST_INTERVAL


def test_env_min_interval_falls_back_on_malformed(monkeypatch) -> None:
    # A bad env value must not crash every WSL caller with an import-time ValueError.
    monkeypatch.setenv("USA_WA_WSL_MIN_REQUEST_INTERVAL", "fast")
    assert _env_min_interval() == DEFAULT_WSL_MIN_REQUEST_INTERVAL
