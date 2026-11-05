"""The journal stays capped at 200M (#394).

``/etc/systemd/journald.conf`` sets no ``SystemMaxUse=``, so without the drop-in
the journal grows to journald's default of 10% of the filesystem (~2.5 G on this
volume). No disk GC vacuums the journal: the cap stands in for it. It was the
one host copy under ``deploy/`` with no test (#526).

**It carries the canonical disk GC's file name (#508).** That layer ships
``/usr/local/lib/systemd/journald.conf.d/zz-cannobserv-journal.conf`` at 512M,
and journald applies drop-ins in file-name order across every directory, so
``zz-`` outranked this repo's ``10-usa-wa-journal-cap.conf`` and raised the cap
the day the GC was installed. A file of the same name under ``/etc`` masks the
layer's, as the layer's own comment says; the owner kept 200M (2026-11-05).

These tests read the deploy artifact, not the live host: the gate must hold in a
worktree and in CI. What the live host carries is verified at install time —
``docs/DEPLOYMENT-HOST.md`` § Installing the host artifacts carries the commands.
"""

from __future__ import annotations

from systemd_units import DEPLOY

#: The canonical disk GC layer's journald drop-in (VERSION 3), which this file masks.
CANONICAL_NAME = "zz-cannobserv-journal.conf"
DROP_IN = DEPLOY / "journald.conf.d" / CANONICAL_NAME
HOST_DOC = DEPLOY.parent / "docs" / "DEPLOYMENT-HOST.md"
INSTALLED_DIR = "/etc/systemd/journald.conf.d/"


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


def test_the_drop_in_sets_only_the_cap() -> None:
    """One setting, so the rest of journald's configuration stays the image's."""
    assert _settings() == [("Journal", "SystemMaxUse=200M")]


def test_it_masks_the_canonical_gc_layers_cap_by_name() -> None:
    """Same name under ``/etc`` replaces the layer's file; any other name sorts
    before ``zz-`` and loses to its 512M."""
    assert DROP_IN.name == CANONICAL_NAME
    assert CANONICAL_NAME in HOST_DOC.read_text()


def test_it_is_the_only_journald_drop_in_shipped() -> None:
    """A second drop-in under ``deploy/journald.conf.d`` would ship with no test pinning it."""
    assert sorted(p.name for p in DROP_IN.parent.iterdir()) == [DROP_IN.name]


def test_the_documented_install_copies_this_file_into_journald_conf_d() -> None:
    """The runbook's ``cp`` and the file's own install comment name the same file and target."""
    command = f"sudo cp deploy/journald.conf.d/{DROP_IN.name} {INSTALLED_DIR}"
    assert command in HOST_DOC.read_text()
    assert f"deploy/journald.conf.d/{DROP_IN.name}" in DROP_IN.read_text()
    assert INSTALLED_DIR in DROP_IN.read_text()
