"""Assert the intended After=/Before= ordering graph across deploy/ units.

This is the guard `systemd-analyze verify` **cannot** provide (issue #52, the
#50-class defect). A misspelled ordering dep — `After=usa-wa-migrat.service` —
exits 0 with no warning under `verify`, because systemd legitimately allows
ordering against not-yet-installed units. So the `verify` pre-commit gate
(scripts/verify-units.sh, #51) catches directive/section typos and bad
ExecStart binaries, but a typo'd `After=`/`Before=` is a silent no-op it can't
see. Here the expected edges carry the *correct* spelling, so a typo fails on a
set mismatch.

Pure file parse — no DB, no systemd-analyze; runs everywhere.

The load-bearing assertion is ``test_every_unit_has_an_expected_entry``: it
cross-checks the on-disk unit set against EXPECTED's keys, so adding a unit
without an ordering decision fails the suite.
"""

from pathlib import Path

import pytest

DEPLOY = Path(__file__).parent.parent.parent / "deploy"  # scripts/tests/ → repo → deploy/

# Intended ordering graph, encoded as data. After=/Before= are space-separated,
# additive across repeated lines, and order-insensitive — so compared as sets.
#
# Note the deliberate asymmetry: usa-wa-migrate.service declares Before= only
# the two long-running serving units (usa-wa + sync-powermap), while the
# oneshot/timer-driven units (reconcile, wsl-refresh) assert After=migrate from
# their own side. Ordering is symmetric in effect (one side suffices), so this
# is correct — captured faithfully rather than normalized.
EXPECTED: dict[str, dict[str, set[str]]] = {
    "usa-wa-migrate.service": {
        "After": {"network.target", "postgresql.service"},
        "Before": {"usa-wa.service", "usa-wa-sync-powermap.service"},
    },
    "usa-wa.service": {
        "After": {"network.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
    },
    "usa-wa-sync-powermap.service": {
        "After": {"network-online.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
    },
    "usa-wa-reconcile-committee-active.service": {
        "After": {"network-online.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
    },
    "usa-wa-wsl-refresh.service": {
        "After": {"network-online.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
    },
    # Timers carry their schedule in [Timer]; no [Unit] ordering by design.
    "usa-wa-reconcile-committee-active.timer": {"After": set(), "Before": set()},
    "usa-wa-wsl-refresh.timer": {"After": set(), "Before": set()},
}


def parse_unit_ordering(path: Path) -> tuple[set[str], set[str]]:
    """Return (After tokens, Before tokens) from a unit's [Unit] section.

    Purpose-built rather than configparser: systemd directives may repeat
    across lines (additive) and duplicate keys, which configparser collapses
    or rejects. Tokens are space-split and accumulated as sets.
    """
    after: set[str] = set()
    before: set[str] = set()
    section = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "Unit" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "After":
            after.update(value.split())
        elif key.strip() == "Before":
            before.update(value.split())
    return after, before


def test_every_unit_has_an_expected_entry():
    """Adding a unit forces an explicit ordering decision here."""
    on_disk = {p.name for p in DEPLOY.glob("*.service")} | {p.name for p in DEPLOY.glob("*.timer")}
    assert on_disk == set(EXPECTED)


@pytest.mark.parametrize("name", EXPECTED)
def test_ordering_edges_match(name):
    after, before = parse_unit_ordering(DEPLOY / name)
    assert after == EXPECTED[name]["After"]
    assert before == EXPECTED[name]["Before"]
