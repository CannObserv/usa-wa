"""PID1's own lines stay in the journal after a ``daemon-reload`` (#520).

exeuntu's ``/etc/systemd/system.conf.d/container-overrides.conf`` sets
``LogTarget=console``, and a ``daemon-reload`` applies it: from the first reload
until the next boot, systemd's ``Started`` / ``Failed with result`` /
``Deactivated successfully`` lines go to ``hvc0`` and never reach
``journalctl -u``. The drop-in is CannObserv/provisioner#62's, copied byte for
byte from its ``image/rootfs`` at ``d2bdd36`` — the ``#62`` in its comment is
provisioner's issue, not this repo's.

These tests read the deploy artifact, not the live host: the gate must hold in a
worktree and in CI. What the live host carries is verified at install time —
``docs/DEPLOYMENT-HOST.md`` § Installing the host artifacts carries the commands.
"""

from __future__ import annotations

import hashlib

from systemd_units import DEPLOY

DROP_IN = DEPLOY / "system.conf.d" / "zz-cannobserv-log-target.conf"

# provisioner's image/rootfs/etc/systemd/system.conf.d/zz-cannobserv-log-target.conf
# at d2bdd36. A local edit makes this a fork of the cohort file; re-copy instead.
PROVISIONER_SHA256 = "0f16a1e6986d15ebb8d40999e05980ce0ce386d0cb8c1b8efa55881b478cb54d"

# The image's drop-in this one must override (measured on this host 2026-10-19).
EXEUNTU_DROP_IN = "container-overrides.conf"


def _settings() -> list[tuple[str, str]]:
    """Return every ``(section, key=value)`` line, skipping blanks and comments."""
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


def test_the_drop_in_is_byte_identical_to_provisioners() -> None:
    """The cohort file, not a local variant of it."""
    assert hashlib.sha256(DROP_IN.read_bytes()).hexdigest() == PROVISIONER_SHA256


def test_the_drop_in_sets_only_the_log_target() -> None:
    """One setting, so exeuntu's ``LogLevel``, ``DefaultOOMPolicy`` and the rest stay its own."""
    assert _settings() == [("Manager", "LogTarget=journal-or-kmsg")]


def test_the_drop_in_sorts_after_exeuntus() -> None:
    """Drop-ins apply in filename order across every ``system.conf.d``; the later one wins.

    systemd reads only ``*.conf``, so a misnamed copy would be skipped outright.
    """
    assert DROP_IN.suffix == ".conf"
    assert sorted([EXEUNTU_DROP_IN, DROP_IN.name]) == [EXEUNTU_DROP_IN, DROP_IN.name]
