"""Tests for clearinghouse_core.testing helpers."""

from __future__ import annotations

import pytest

from clearinghouse_core.testing import assert_test_url_safety

_PROD_URL = "postgresql+asyncpg://app@localhost/usa_wa"
_TEST_URL = "postgresql+asyncpg://test_user@localhost/usa_wa_test"


def test_assert_test_url_safety_no_database_url_is_a_noop(monkeypatch):
    """With DATABASE_URL unset, any test URL is allowed (nothing to collide with)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Must not raise.
    assert_test_url_safety(_TEST_URL)


def test_assert_test_url_safety_distinct_urls_pass(monkeypatch):
    """When URLs differ, the guard is a no-op."""
    monkeypatch.setenv("DATABASE_URL", _PROD_URL)
    assert_test_url_safety(_TEST_URL)


def test_assert_test_url_safety_matching_urls_raise(monkeypatch):
    """When TEST_DATABASE_URL == DATABASE_URL, the guard raises with an actionable message."""
    monkeypatch.setenv("DATABASE_URL", _PROD_URL)
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL must not equal DATABASE_URL"):
        assert_test_url_safety(_PROD_URL)


def test_assert_test_url_safety_non_test_database_name_raises(monkeypatch):
    """A test URL whose database name does not end in '_test' is rejected.

    Catches a typo pointing TEST_DATABASE_URL at the prod database even when
    DATABASE_URL itself is unset (e.g. CI that only defines the test DSN).
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="must end in '_test'"):
        assert_test_url_safety("postgresql+asyncpg://test_user@localhost/usa_wa")


def test_assert_test_url_safety_app_role_raises(monkeypatch):
    """A test URL connecting as the prod ops role 'usa_wa_app' is rejected."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="usa_wa_app"):
        assert_test_url_safety("postgresql+asyncpg://usa_wa_app@localhost/usa_wa_test")
