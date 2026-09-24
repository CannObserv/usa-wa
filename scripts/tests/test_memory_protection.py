"""The host-side half of the #389 memory protection, pinned (CR 1, CR 2).

#389 landed four layers. Only one of them — ``MemoryLow=``/``OOMScoreAdjust=`` on
``usa-wa.service`` — was a repo artifact, and it is the one that does **nothing**
on its own:

    /sys/fs/cgroup/system.slice/memory.low = 0
    systemctl show system.slice -p MemoryLow → 0
    DefaultMemoryLow unset in system.conf
    cgroup2 mounted rw,relatime (no memory_recursiveprot)

In cgroup v2 a cgroup's effective ``memory.low`` is capped by its ancestors', so
``min(256M, 0)`` is **0**. The unit's reservation needs a ``system.slice`` drop-in
to draw from, and until CR 1 there was none — the directive was present, the
documentation said it protected the unit, and the protection was zero. That is
the shape this repo treats as worse than an absent guard.

The other two layers (``vm.min_free_kbytes``, earlyoom) were hand-written on the
VM and existed in no reviewable artifact at all (CR 2), so a rebuild lost them
silently and nothing could see them drift. They now live under ``deploy/``,
mirroring the install path, exactly as the units do.

These tests read the deploy artifacts, not the live host: the gate must hold in a
worktree and in CI. What the live host carries is verified at install time —
``docs/DEPLOYMENT-HOST.md`` § Memory pressure carries the commands.
"""

from __future__ import annotations

import re

import pytest
from systemd_units import DEPLOY, parse_bytes, unit_value

SLICE_DROP_IN = DEPLOY / "system.slice.d" / "10-usa-wa-memory.conf"
SYSCTL_DROP_IN = DEPLOY / "sysctl.d" / "60-usa-wa-memory.conf"
EARLYOOM_DEFAULTS = DEPLOY / "default" / "earlyoom"
SERVING_UNIT = DEPLOY / "usa-wa.service"

#: The floor #389 measured and chose. Raising the unit's reservation without
#: raising the slice's silently re-creates CR 1, so the relation is asserted
#: rather than the literal.
MIN_FREE_KBYTES_FLOOR = 65536


def test_the_slice_drop_in_exists() -> None:
    """Without it, the unit's MemoryLow has no parent protection to draw from."""
    assert SLICE_DROP_IN.is_file(), (
        f"{SLICE_DROP_IN} missing — usa-wa.service's MemoryLow= is capped by "
        "system.slice's, which defaults to 0, so the reservation is inert (CR 1)"
    )


def test_the_slice_reservation_covers_the_units() -> None:
    """``system.slice`` must protect at least what the units beneath it claim.

    The cap is the finding: a child's effective low is ``min(own, parent's
    unclaimed)``. Asserting the *relation* rather than a literal means raising
    ``usa-wa.service``'s reservation without raising the slice's fails here
    instead of quietly restoring a zero.
    """
    slice_low = unit_value(SLICE_DROP_IN, "Slice", "MemoryLow")
    assert slice_low is not None, f"{SLICE_DROP_IN.name}: no MemoryLow= in [Slice]"

    unit_low = unit_value(SERVING_UNIT, "Service", "MemoryLow")
    assert unit_low is not None, "usa-wa.service lost its MemoryLow="

    for name, low in (("system.slice", slice_low), ("usa-wa.service", unit_low)):
        assert not low.strip().endswith("%"), (
            f"{name}: MemoryLow={low} is host-relative, so the two sides of this "
            "comparison would not mean the same thing (CR 3)"
        )

    assert parse_bytes(slice_low) >= parse_bytes(unit_low), (
        f"system.slice MemoryLow={slice_low} is below usa-wa.service's {unit_low}; "
        "the unit's effective protection is capped by its parent's, so the excess "
        "is silently zero (CR 1)"
    )


def test_the_sysctl_drop_in_raises_the_atomic_allocation_reserve() -> None:
    """``vm.min_free_kbytes`` at the value #389 measured, or above.

    The default here was 11399 (~11 MiB). Past the ceiling with no swap the
    kernel fails atomic allocations in unrelated processes rather than killing
    anything — the shape of the incident #389 cites.
    """
    assert SYSCTL_DROP_IN.is_file(), f"{SYSCTL_DROP_IN} missing (CR 2)"

    settings = {}
    for raw in SYSCTL_DROP_IN.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        settings[key.strip()] = value.strip()

    value = settings.get("vm.min_free_kbytes")
    assert value is not None, f"{SYSCTL_DROP_IN.name}: vm.min_free_kbytes not set"
    assert int(value) >= MIN_FREE_KBYTES_FLOOR, (
        f"vm.min_free_kbytes={value} is below the {MIN_FREE_KBYTES_FLOOR} #389 measured"
    )


def _earlyoom_regex(flag: str) -> re.Pattern[str]:
    """The regex ``flag`` carries in the deployed EARLYOOM_ARGS line."""
    args = ""
    for line in EARLYOOM_DEFAULTS.read_text().splitlines():
        if line.startswith("EARLYOOM_ARGS="):
            args = line.partition("=")[2].strip().strip('"')
    assert args, f"{EARLYOOM_DEFAULTS.name}: no EARLYOOM_ARGS= line"

    match = re.search(rf"{re.escape(flag)}\s+'([^']+)'", args)
    assert match, f"{flag} not found in EARLYOOM_ARGS"
    return re.compile(match.group(1))


def test_earlyoom_defaults_exist() -> None:
    assert EARLYOOM_DEFAULTS.is_file(), f"{EARLYOOM_DEFAULTS} missing (CR 2)"


@pytest.mark.parametrize("comm", ["uv", "postgres"])
def test_earlyoom_avoids_the_processes_whose_death_is_an_outage(comm: str) -> None:
    """The regex is **executed**, against the process names the kernel actually sees.

    ``uv`` is the trap. The serving unit's ExecStart is ``uv run … uvicorn``, so
    its ``comm`` is ``uv`` — an ``--avoid`` regex written for ``uvicorn``, which
    is the name everything in this repo calls that process, protects nothing and
    reads as though it does.
    """
    assert _earlyoom_regex("--avoid").fullmatch(comm)


@pytest.mark.parametrize("comm", ["node", "npm"])
def test_earlyoom_prefers_the_processes_that_cause_the_pressure(comm: str) -> None:
    """The MCP servers and the npm install a launch performs (#389)."""
    assert _earlyoom_regex("--prefer").fullmatch(comm)
