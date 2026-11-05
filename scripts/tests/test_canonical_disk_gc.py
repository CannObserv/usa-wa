"""The host runs the cohort's canonical disk GC, and keeps the old one's failure net (#508).

``scripts/disk-gc.sh`` and ``usa-wa-disk-gc.timer`` (#394) retired in favour of
Provisioner's GC (CannObserv/provisioner#61), adopted through the vendored
``maintaining-host-disk`` skill. The GC's files are the image layer's, installed
under ``/usr/local/lib`` and never edited here; what this repo owns is pinned:

- **One GC, never two.** retiring.md: the old tool goes in the change that turns
  the canonical timers on, so neither its script nor its units may ship.
- **The failure net stays.** ``usa-wa-disk-gc.service`` mailed the operator
  through ``OnFailure=usa-wa-notify-failure@%n.service`` (#49). The canonical
  ``cannobserv-disk-gc.service`` has no ``OnFailure=``; turning-on.md § 5's
  close (a) restores it as a drop-in under ``/etc``, which leaves the installed
  unit, and so its MANIFEST, untouched.
- **The skill is linked** the way every other vendored skill is.

Pure file parse: the live host's copy is verified at install time
(``docs/DEPLOYMENT-HOST.md`` § Host maintenance carries the commands).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from systemd_units import DEPLOY, unit_values

REPO = DEPLOY.parent
HOST_DOC = REPO / "docs" / "DEPLOYMENT-HOST.md"

#: The canonical GC's service, as the VERSION 3 layer names it.
CANONICAL_GC = "cannobserv-disk-gc.service"
DROP_IN = DEPLOY / f"{CANONICAL_GC}.d" / "on-failure.conf"
INSTALLED = f"/etc/systemd/system/{CANONICAL_GC}.d/on-failure.conf"
NOTIFY = "usa-wa-notify-failure@%n.service"

#: Where the layer installs its units; the verify below needs the real one.
INSTALLED_UNIT = Path("/usr/local/lib/systemd/system") / CANONICAL_GC
VERIFY_UNITS = REPO / "scripts" / "verify-units.sh"

SKILL = "maintaining-host-disk"
VENDORED = f"../skills-vendor/gregoryfoster-skills/skills/{SKILL}"


def _settings() -> list[tuple[str, str]]:
    """Every ``(section, key=value)`` line of the drop-in, skipping blanks and comments."""
    section = ""
    settings = []
    for raw in DROP_IN.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        settings.append((section, line))
    return settings


def test_the_old_gc_ships_nowhere() -> None:
    """Two GCs on one host is what retiring.md exists to prevent."""
    assert not (REPO / "scripts" / "disk-gc.sh").exists()
    assert not list(DEPLOY.glob("usa-wa-disk-gc.*"))


def test_the_failure_net_is_a_drop_in_on_the_canonical_gc() -> None:
    """Only ``OnFailure=``, to the #49 handler: the unit's own lines stay the layer's."""
    assert _settings() == [("Unit", f"OnFailure={NOTIFY}")]
    assert unit_values(DROP_IN, "Unit", "OnFailure") == [NOTIFY]


def test_the_notify_handler_it_names_ships() -> None:
    assert (DEPLOY / "usa-wa-notify-failure@.service").is_file()


def test_it_is_the_only_drop_in_on_the_canonical_gc() -> None:
    """A second file there would change the layer's unit with no test pinning it."""
    assert sorted(p.name for p in DROP_IN.parent.iterdir()) == [DROP_IN.name]


def test_the_documented_install_lands_it_and_reloads() -> None:
    """The runbook installs this file at the path systemd reads, then reloads:
    without the reload systemd keeps the unit it loaded, net and all missing."""
    text = HOST_DOC.read_text()
    install = f"deploy/{CANONICAL_GC}.d/on-failure.conf {INSTALLED}"
    assert install in text
    assert "sudo systemctl daemon-reload" in text[text.index(install) :].split("```", 1)[0]
    assert INSTALLED in DROP_IN.read_text()


def test_the_skill_is_linked_like_the_others() -> None:
    link = REPO / "skills" / SKILL
    assert link.is_symlink()
    assert str(link.readlink()) == VENDORED
    claude = REPO / ".claude" / "skills" / SKILL
    assert claude.is_symlink()
    assert str(claude.readlink()) == f"../../skills/{SKILL}"


def _verify_merged(tmp_path: Path, drop_in_text: str) -> subprocess.CompletedProcess:
    """``verify-units.sh`` on the installed canonical unit with ``drop_in_text`` merged in.

    The pre-commit gate matches ``deploy/*.{service,timer}`` only, so the drop-in
    is checked here, against the unit it extends: systemd reads drop-ins from the
    unit search path, which ``SYSTEMD_UNIT_PATH`` points at a scratch copy.
    """
    if not INSTALLED_UNIT.is_file():
        pytest.skip("the canonical disk GC is not installed on this host")
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze not installed")
    unit = tmp_path / CANONICAL_GC
    shutil.copy(INSTALLED_UNIT, unit)
    shutil.copy(DEPLOY / "usa-wa-notify-failure@.service", tmp_path)
    (tmp_path / f"{CANONICAL_GC}.d").mkdir()
    (tmp_path / f"{CANONICAL_GC}.d" / DROP_IN.name).write_text(drop_in_text)
    env = {**os.environ, "SYSTEMD_UNIT_PATH": f"{tmp_path}:"}
    return subprocess.run(  # noqa: S603
        ["bash", str(VERIFY_UNITS), str(unit)], capture_output=True, text=True, env=env, check=False
    )


def test_the_drop_in_verifies_on_the_unit_it_extends(tmp_path: Path) -> None:
    result = _verify_merged(tmp_path, DROP_IN.read_text())
    assert result.returncode == 0, result.stderr


def test_the_verify_catches_a_misspelt_drop_in(tmp_path: Path) -> None:
    """Teeth: a typo that would silently drop the net fails the same check."""
    broken = DROP_IN.read_text().replace("OnFailure=", "OnFailur=")
    assert _verify_merged(tmp_path, broken).returncode != 0
