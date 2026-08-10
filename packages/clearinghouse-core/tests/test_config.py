"""Settings + DSN resolution (#179b).

``get_database_url()`` was single-role until the #179b sweep reached the five
owner-role CLIs (the span/source migrations, which hard-delete citations the app role
is REVOKEd on). Those five each read ``DATABASE_URL_OWNER`` straight off
``os.environ`` with their own error text and their own exit code — exactly the split
brain #179 exists to close, one variable further along.
"""

import pytest

from clearinghouse_core.config import (
    DATABASE_ROLE_APP,
    DATABASE_ROLE_OWNER,
    Settings,
    get_database_url,
    get_settings,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """``get_settings`` is ``lru_cache``d; a test that edits the env must not leak."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_app_role_is_the_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app@h/db")
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    assert get_database_url() == "postgresql+asyncpg://app@h/db"
    assert get_database_url(DATABASE_ROLE_APP) == "postgresql+asyncpg://app@h/db"


def test_owner_role_resolves_the_owner_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app@h/db")
    monkeypatch.setenv("DATABASE_URL_OWNER", "postgresql+asyncpg://owner@h/db")
    assert get_database_url(DATABASE_ROLE_OWNER) == "postgresql+asyncpg://owner@h/db"


def test_owner_role_never_silently_falls_back_to_the_app_dsn(monkeypatch):
    """The whole point of the owner role is DDL/DELETE the app role cannot do. A
    fallback would turn a missing variable into a permission error mid-migration."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app@h/db")
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        get_database_url(DATABASE_ROLE_OWNER)
    assert "DATABASE_URL_OWNER" in str(excinfo.value)


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown database role"):
        get_database_url("superuser")


def test_settings_carry_both_dsns(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app@h/db")
    monkeypatch.setenv("DATABASE_URL_OWNER", "postgresql+asyncpg://owner@h/db")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://app@h/db"
    assert settings.database_url_owner == "postgresql+asyncpg://owner@h/db"
