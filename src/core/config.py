"""Application settings via pydantic-settings.

Env files (/etc/usa-wa/.env, repo .env) are loaded by systemd or the
developer before launch — never by this module.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: str | None = None
    log_level: str = "INFO"
    build_id: str = "dev"


@lru_cache
def get_settings() -> Settings:
    """Return the shared Settings instance."""
    return Settings()


def get_database_url() -> str:
    """Return DATABASE_URL or raise with a helpful error."""
    url = get_settings().database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Load env: export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)"
        )
    return url
