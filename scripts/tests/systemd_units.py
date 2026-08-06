"""Shared systemd unit-file parser for the deploy/ guards (issue #167 CR, finding 5).

Purpose-built rather than configparser: systemd directives may repeat across
lines (additive) and duplicate keys, which configparser collapses or rejects.
Trailing-backslash line continuations are folded first — systemd reads
``After=a.service \\`` + newline ``b.service`` as one logical line.

Not a test module. ``test_unit_ordering`` (the dependency-graph guard) and
``test_docs_timer_drift`` (the docs guard) both import from here so the two can
never disagree about what a unit file says. Extracted from the former, which
the latter used to import directly — importing a *test* module for its helpers
ran that module's collection and broke under a non-default ``importmode``.
``conftest.py`` in this directory puts it on ``sys.path`` regardless of mode.
"""

import re
from pathlib import Path

DEPLOY = Path(__file__).parent.parent.parent / "deploy"  # scripts/tests/ → repo → deploy/


def join_continuations(text: str) -> list[str]:
    """Fold systemd trailing-backslash line continuations into single lines."""
    lines: list[str] = []
    pending = ""
    for raw in text.splitlines():
        if raw.endswith("\\"):
            pending += raw[:-1] + " "
            continue
        lines.append(pending + raw)
        pending = ""
    if pending:  # dangling backslash on the final line
        lines.append(pending)
    return lines


def _directive_values(path: Path, section: str, key: str) -> list[str]:
    """Every value assigned to `key` within `section`, in file order."""
    values: list[str] = []
    current = None
    for raw in join_continuations(path.read_text()):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        if current != section or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            values.append(v.strip())
    return values


def parse_unit_deps(path: Path) -> tuple[set[str], set[str], set[str]]:
    """Return (After, Before, OnFailure) token sets from a unit's [Unit] section.

    These three are *additive* across repeated lines and order-insensitive, so
    tokens are space-split and accumulated as sets.
    """
    return (
        {token for value in _directive_values(path, "Unit", "After") for token in value.split()},
        {token for value in _directive_values(path, "Unit", "Before") for token in value.split()},
        {
            token
            for value in _directive_values(path, "Unit", "OnFailure")
            for token in value.split()
        },
    )


def parse_exec_start_pre(path: Path) -> list[str]:
    """Return the ExecStartPre command values from a unit's [Service] section."""
    return _directive_values(path, "Service", "ExecStartPre")


def unit_values(path: Path, section: str, key: str) -> list[str]:
    """Return every assignment of `key` in `section`.

    Use this over :func:`unit_value` for directives systemd treats as *additive*
    rather than last-wins — ``OnCalendar=`` above all: repeated lines each add an
    elapse expression (and a bare ``OnCalendar=`` resets the list), so reading
    only the last one silently under-reports a multi-schedule timer.
    """
    return _directive_values(path, section, key)


def unit_value(path: Path, section: str, key: str) -> str | None:
    """Return the last value of `key` in `section` (systemd: last assignment wins), or None.

    Correct for scalar directives (``Restart=``, ``RestartSec=``, ``StartLimitBurst=``).
    For additive ones see :func:`unit_values`.
    """
    values = _directive_values(path, section, key)
    return values[-1] if values else None


# systemd time-span units → seconds. A bare number is seconds; tokens may be
# unit-suffixed (`5min`, `300s`, `2h`) and space-combined (`1min 30s`). Our units
# use plain integer seconds, but the idiomatic forms are valid — parse them so a
# `5min` edit asserts cleanly instead of crashing the invariant test on int().
_SPAN_UNIT_SECONDS = {
    "": 1,
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}


def parse_seconds(value: str) -> int:
    """Parse a systemd time span into whole seconds (see _SPAN_UNIT_SECONDS)."""
    tokens = re.findall(r"(\d+)\s*([a-z]*)", value.strip().lower())
    if not tokens:
        raise ValueError(f"unparseable systemd time span: {value!r}")
    total = 0
    for number, unit in tokens:
        if unit not in _SPAN_UNIT_SECONDS:
            raise ValueError(f"unrecognized systemd time unit {unit!r} in {value!r}")
        total += int(number) * _SPAN_UNIT_SECONDS[unit]
    return total
