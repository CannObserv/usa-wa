"""No production unit loads an env file from the checkout (#561).

The repo ``.env`` holds the developer's and agents' credentials — GitHub tokens, the
Anthropic key, ``TEST_DATABASE_URL``. Production reads none of them, yet seven units
loaded the file after ``/etc/usa-wa/.env``, so every one of those processes (uvicorn on
``0.0.0.0:8000`` among them) carried the tokens in its environment, and any dev-only
value could silently override production config. Production reads ``/etc/usa-wa/.env``
only; this keeps the checkout's file from coming back on any unit, present or future.

Pure file parse over every ``deploy/*.service`` — no systemd.
"""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath

import pytest
from systemd_units import DEPLOY, unit_values

CHECKOUT = PurePosixPath("/home/exedev/usa-wa")
SYSTEM_ENV = "/etc/usa-wa/.env"
SERVICES = sorted(DEPLOY.glob("*.service"))

# The units that read production's config. Each lost only the checkout line in #561.
PRODUCTION_ENV_UNITS = (
    "usa-wa.service",
    "usa-wa-pipeline.service",
    "usa-wa-migrate.service",
    "usa-wa-integrity-sweep.service",
    "usa-wa-roster-pdf-recheck.service",
    "usa-wa-wsl-availability-probe.service",
    "usa-wa-notify-failure@.service",
)


def _env_files(unit: str) -> list[str]:
    return unit_values(DEPLOY / unit, "Service", "EnvironmentFile")


def test_the_scan_sees_every_unit() -> None:
    names = {path.name for path in SERVICES}
    assert set(PRODUCTION_ENV_UNITS) <= names


@pytest.mark.parametrize("service", SERVICES, ids=lambda path: path.name)
def test_no_env_file_lives_in_the_checkout(service) -> None:
    for value in unit_values(service, "Service", "EnvironmentFile"):
        path = PurePosixPath(posixpath.normpath(value.removeprefix("-")))
        assert not path.is_relative_to(CHECKOUT), (
            f"{service.name}: EnvironmentFile={value} loads a file from the checkout; "
            f"production config belongs in {SYSTEM_ENV}"
        )


@pytest.mark.parametrize("unit", PRODUCTION_ENV_UNITS)
def test_production_config_still_loads_and_is_required(unit: str) -> None:
    """Dropping the checkout line must not take the system file with it. No leading
    '-': a unit without its database URLs or alert address should fail loudly."""
    assert SYSTEM_ENV in _env_files(unit)
