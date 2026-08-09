"""Tests for clearinghouse_core.testing helpers."""

from __future__ import annotations

import pytest

from clearinghouse_core import testing
from clearinghouse_core.testing import (
    assert_test_url_safety,
    declared_schemas,
    production_database_url,
    remember_production_url,
)

_PROD_URL = "postgresql+asyncpg://app@localhost/usa_wa"
_TEST_URL = "postgresql+asyncpg://test_user@localhost/usa_wa_test"


@pytest.fixture
def forget_production_url(monkeypatch):
    """Restore the "nobody pinned a DSN" state so the environment fallback is exercised.

    The root ``conftest.py`` pins the real DSN for the whole session (CR #196 finding
    13), so a test that only monkeypatches ``DATABASE_URL`` would be overridden by the
    pin and prove nothing.
    """
    monkeypatch.setattr(testing, "_PRODUCTION_URL", testing._UNREMEMBERED)


def test_assert_test_url_safety_no_database_url_is_a_noop(monkeypatch, forget_production_url):
    """With no production DSN known at all, any *_test URL is allowed."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Must not raise.
    assert_test_url_safety(_TEST_URL)


def test_assert_test_url_safety_falls_back_to_the_environment(monkeypatch, forget_production_url):
    """Nothing remembered → read ``DATABASE_URL``.

    Keeps the helper usable outside the pytest session that installs the stash.
    """
    monkeypatch.setenv("DATABASE_URL", _PROD_URL)
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL must not equal DATABASE_URL"):
        assert_test_url_safety(_PROD_URL)


def test_assert_test_url_safety_distinct_urls_pass(monkeypatch):
    """When URLs differ, the guard is a no-op."""
    monkeypatch.setattr(testing, "_PRODUCTION_URL", _PROD_URL)
    assert_test_url_safety(_TEST_URL)


def test_assert_test_url_safety_matching_urls_raise(monkeypatch):
    """When TEST_DATABASE_URL == DATABASE_URL, the guard raises with an actionable message."""
    monkeypatch.setattr(testing, "_PRODUCTION_URL", _PROD_URL)
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL must not equal DATABASE_URL"):
        assert_test_url_safety(_PROD_URL)


def test_assert_test_url_safety_non_test_database_name_raises(monkeypatch, forget_production_url):
    """A test URL whose database name does not end in '_test' is rejected.

    Catches a typo pointing TEST_DATABASE_URL at the prod database even when
    DATABASE_URL itself is unset (e.g. CI that only defines the test DSN).
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="must end in '_test'"):
        assert_test_url_safety("postgresql+asyncpg://test_user@localhost/usa_wa")


def test_assert_test_url_safety_same_role_as_prod_raises(monkeypatch):
    """A test URL connecting as the *prod* role (whatever it is named) is rejected.

    The forbidden role is derived from the production DSN's username, so the guard is
    jurisdiction-agnostic — no hardcoded role name.
    """
    monkeypatch.setattr(
        testing, "_PRODUCTION_URL", "postgresql+asyncpg://usa_wa_app@localhost/usa_wa"
    )
    with pytest.raises(RuntimeError, match="same role as production"):
        assert_test_url_safety("postgresql+asyncpg://usa_wa_app@localhost/usa_wa_test")


def test_assert_test_url_safety_distinct_role_from_prod_passes(monkeypatch):
    """A dedicated test role against a *_test DB is fine even when the prod DSN is known."""
    monkeypatch.setattr(
        testing, "_PRODUCTION_URL", "postgresql+asyncpg://usa_wa_app@localhost/usa_wa"
    )
    assert_test_url_safety("postgresql+asyncpg://usa_wa_test_owner@localhost/usa_wa_test")


# --- CR #196 finding 13: the guard survives the production-DSN sentinel ------
#
# The root conftest rewrites ``DATABASE_URL`` to an unroutable sentinel for the whole
# session (CR #191 finding 1). Belts 1 and 3 derive the forbidden URL and role from the
# production DSN — read from the environment at *call* time, they compared against
# ``blocked`` and let a DSN connecting as the production role straight through.


def test_the_guard_prefers_the_remembered_dsn_over_a_neutered_environment(monkeypatch):
    """Belt 3 must survive the sentinel: same role as production is still rejected."""
    monkeypatch.setattr(
        testing, "_PRODUCTION_URL", "postgresql+asyncpg://usa_wa_app@localhost/usa_wa"
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://blocked@127.0.0.1:1/blocked")

    with pytest.raises(RuntimeError, match="same role as production"):
        assert_test_url_safety("postgresql+asyncpg://usa_wa_app@localhost/evil_test")


def test_belt_one_also_survives_a_neutered_environment(monkeypatch):
    """Belt 1 (test DSN == production DSN) is derived from the same source."""
    monkeypatch.setattr(testing, "_PRODUCTION_URL", _PROD_URL)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://blocked@127.0.0.1:1/blocked")

    with pytest.raises(RuntimeError, match="must not equal DATABASE_URL"):
        assert_test_url_safety(_PROD_URL)


def test_remember_production_url_round_trips(monkeypatch, forget_production_url):
    """The pin is readable, so a caller can assert what the guard will compare against."""
    remember_production_url(_PROD_URL)
    assert production_database_url() == _PROD_URL


def test_remembering_none_pins_absence_rather_than_falling_back(monkeypatch):
    """Pinning ``None`` must not re-enable the environment read.

    The conftest calls ``remember_production_url(os.environ.get("DATABASE_URL"))`` and
    *then* installs the sentinel. On a machine with no production DSN that argument is
    ``None`` — and a fallback would go straight back to reading the sentinel it just
    wrote, which is the exact failure this mechanism exists to prevent.
    """
    monkeypatch.setattr(testing, "_PRODUCTION_URL", "postgresql+asyncpg://stale@localhost/stale")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://blocked@127.0.0.1:1/blocked")
    remember_production_url(None)

    assert production_database_url() is None
    # …and with no production DSN known, belt 2 is the one still standing.
    assert_test_url_safety("postgresql+asyncpg://usa_wa_app@localhost/anything_test")
    with pytest.raises(RuntimeError, match="must end in '_test'"):
        assert_test_url_safety("postgresql+asyncpg://usa_wa_app@localhost/usa_wa")


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
