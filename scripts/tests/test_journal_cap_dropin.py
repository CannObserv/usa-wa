"""The journal stays capped at 200M (#394).

``/etc/systemd/journald.conf`` sets no ``SystemMaxUse=``, so without the drop-in
the journal grows to journald's default of 10% of the filesystem (~2.5 G on this
volume). The cap is also why ``usa-wa-disk-gc.service`` never vacuums the
journal, and so needs no privilege: lose the cap and the GC has nothing standing
in for it. It was the one host copy under ``deploy/`` with no test (#526).

These tests read the deploy artifact, not the live host: the gate must hold in a
worktree and in CI. What the live host carries is verified at install time —
``docs/DEPLOYMENT-HOST.md`` § Installing the host artifacts carries the commands.
"""

from __future__ import annotations

from systemd_units import DEPLOY

DROP_IN = DEPLOY / "journald.conf.d" / "10-usa-wa-journal-cap.conf"
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


def test_journald_reads_the_drop_in() -> None:
    """journald reads only ``*.conf`` under ``journald.conf.d``; a misnamed copy is skipped."""
    assert DROP_IN.is_file()
    assert DROP_IN.suffix == ".conf"
    assert DROP_IN.parent.name == "journald.conf.d"


def test_the_documented_install_copies_this_file_into_journald_conf_d() -> None:
    """The runbook's ``cp`` and the file's own install comment name the same file and target."""
    command = f"sudo cp deploy/journald.conf.d/{DROP_IN.name} {INSTALLED_DIR}"
    assert command in HOST_DOC.read_text()
    assert INSTALLED_DIR in DROP_IN.read_text()
