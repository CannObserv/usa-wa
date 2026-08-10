"""Application settings via pydantic-settings.

Env files (/etc/usa-wa/.env, repo .env) are loaded by systemd or the
developer before launch — never by this module.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_ROLE_APP = "app"
"""The everyday least-privilege role (``DATABASE_URL``) — every job but the migrations."""

DATABASE_ROLE_OWNER = "owner"
"""The schema-owning role (``DATABASE_URL_OWNER``). Needed only where a job hard-deletes
provenance rows the app role is REVOKEd on (#54) or runs DDL — see docs/DEPLOYMENT.md."""

_ROLE_ENV_VARS = {
    DATABASE_ROLE_APP: "DATABASE_URL",
    DATABASE_ROLE_OWNER: "DATABASE_URL_OWNER",
}


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: str | None = None
    database_url_owner: str | None = None
    log_level: str = "INFO"
    build_id: str = "dev"


@lru_cache
def get_settings() -> Settings:
    """Return the shared Settings instance."""
    return Settings()


def get_database_url(role: str = DATABASE_ROLE_APP) -> str:
    """Return the DSN for ``role``, or raise with a helpful error.

    The owner DSN deliberately does **not** fall back to the app DSN: an owner job asks
    for the role precisely because it does something the app role cannot, so a fallback
    would trade one clear "variable is not set" for a permission error partway through a
    migration.
    """
    if role not in _ROLE_ENV_VARS:
        raise ValueError(
            f"unknown database role {role!r}; expected one of {sorted(_ROLE_ENV_VARS)}"
        )
    settings = get_settings()
    url = settings.database_url if role == DATABASE_ROLE_APP else settings.database_url_owner
    if not url:
        raise RuntimeError(
            f"{_ROLE_ENV_VARS[role]} is not set. "
            "Load env: export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)"
        )
    return url
