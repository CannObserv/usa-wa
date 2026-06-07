"""Sidecar settings via pydantic-settings.

Env (`/etc/usa-wa/.env`, repo `.env`) is loaded by systemd or the developer
before launch — never by this module. PM credentials live there.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SidecarSettings(BaseSettings):
    """Power Map sidecar runtime configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    powermap_base_url: str = "https://power-map.exe.xyz"
    powermap_api_key: str | None = None
    #: Seconds between sync cycles (feed poll + due reconcile + outbox drain).
    feed_poll_seconds: float = 60.0


@lru_cache
def get_sidecar_settings() -> SidecarSettings:
    return SidecarSettings()
