"""Tests for clearinghouse_core.testing helpers."""

from __future__ import annotations

import pytest

from clearinghouse_core.testing import assert_test_url_safety, declared_schemas

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


def test_assert_test_url_safety_same_role_as_prod_raises(monkeypatch):
    """A test URL connecting as the *prod* role (whatever it is named) is rejected.

    The forbidden role is derived from DATABASE_URL's username, so the guard is
    jurisdiction-agnostic — no hardcoded role name.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://usa_wa_app@localhost/usa_wa")
    with pytest.raises(RuntimeError, match="same role as production"):
        assert_test_url_safety("postgresql+asyncpg://usa_wa_app@localhost/usa_wa_test")


def test_assert_test_url_safety_distinct_role_from_prod_passes(monkeypatch):
    """A dedicated test role against a *_test DB is fine even when DATABASE_URL is set."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://usa_wa_app@localhost/usa_wa")
    assert_test_url_safety("postgresql+asyncpg://usa_wa_test_owner@localhost/usa_wa_test")


def test_declared_schemas_includes_every_workspace_schema():
    """``declared_schemas`` is the single source of truth for full-DB resets.

    Regression guard for issue #26: the ``sync`` schema was added to the
    migration chain (#22) but integration-test wipes still listed only the two
    original schemas, so a from-base re-migration collided on
    ``sync.powermap_outbox``. The helper must surface *every* schema the
    migration chain creates — derived from ``Base.metadata`` so it can't drift
    out of date as new schemas are added — regardless of the caller's import
    context (it forces sibling registration imports itself).
    """
    assert declared_schemas() >= {"clearinghouse_core", "canonical", "sync"}
