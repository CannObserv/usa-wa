"""Assert the intended After=/Before=/OnFailure dependency graph across deploy/ units.

This is the guard `systemd-analyze verify` **cannot** provide (issue #52, the
#50-class defect). A misspelled dep — `After=usa-wa-migrat.service` or
`OnFailure=usa-wa-notify-failrue@%n.service` — exits 0 with no warning under
`verify`, because systemd legitimately allows ordering/triggering against
not-yet-installed units. So the `verify` pre-commit gate (scripts/verify-units.sh,
#51) catches directive/section typos and bad ExecStart binaries, but a typo'd
`After=`/`Before=`/`OnFailure=` is a silent no-op it can't see. Here the expected
edges carry the *correct* spelling, so a typo fails on a set mismatch.

`OnFailure=` (issue #49) is the alerting edge: each timer-driven oneshot (and the
migrate oneshot) triggers the `usa-wa-notify-failure@%n.service` handler on a
failed result — emailing the operator via the exe.dev gateway. Asserting it here
keeps the alerting wiring from silently regressing and forces a new failable unit
to make an explicit notify decision.

Pure file parse — no DB, no systemd-analyze; runs everywhere.

The load-bearing assertion is ``test_every_unit_has_an_expected_entry``: it
cross-checks the on-disk unit set against EXPECTED's keys, so adding a unit
without a dependency decision fails the suite.
"""

import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parent.parent.parent / "deploy"  # scripts/tests/ → repo → deploy/

# Intended dependency graph, encoded as data. After=/Before=/OnFailure= are
# space-separated, additive across repeated lines, and order-insensitive — so
# compared as sets.
#
# Note the deliberate asymmetry: usa-wa-migrate.service declares Before= only
# the two long-running serving units (usa-wa + sync-powermap), while the
# oneshot/timer-driven units (reconcile, wsl-refresh) assert After=migrate from
# their own side. Ordering is symmetric in effect (one side suffices), so this
# is correct — captured faithfully rather than normalized.
#
# OnFailure (#49): the three oneshots that can fail unattended — migrate plus the
# two timer-driven oneshots — trigger the templated notify handler with %n (the
# failing unit's full name) as the instance. The serving units restart in place
# (Restart=) and so don't route through the one-shot alert; the timers can't fail
# (they only activate their .service); the handler must not trigger itself.
NOTIFY = {"usa-wa-notify-failure@%n.service"}
EXPECTED: dict[str, dict[str, set[str]]] = {
    "usa-wa-migrate.service": {
        "After": {"network.target", "postgresql.service"},
        "Before": {"usa-wa.service", "usa-wa-sync-powermap.service"},
        "OnFailure": NOTIFY,
    },
    "usa-wa.service": {
        "After": {"network.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
        "OnFailure": set(),
    },
    "usa-wa-sync-powermap.service": {
        "After": {"network-online.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
        "OnFailure": set(),
    },
    "usa-wa-reconcile-committee-active.service": {
        "After": {"network-online.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
        "OnFailure": NOTIFY,
    },
    "usa-wa-reconcile-committee-names.service": {
        "After": {"network-online.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
        "OnFailure": NOTIFY,
    },
    "usa-wa-reconcile-committee-meeting-names.service": {
        "After": {"network-online.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
        "OnFailure": NOTIFY,
    },
    "usa-wa-wsl-refresh.service": {
        "After": {"network-online.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
        "OnFailure": NOTIFY,
    },
    # PDC refresh (#69) binds Position onto the WSL House Persons, so it additionally
    # orders After the WSL refresh (best-effort; a missing predecessor just leaves an
    # unmatched winner logged, not wedged).
    "usa-wa-pdc-refresh.service": {
        "After": {
            "network-online.target",
            "postgresql.service",
            "usa-wa-migrate.service",
            "usa-wa-wsl-refresh.service",
        },
        "Before": set(),
        "OnFailure": NOTIFY,
    },
    # SOS refresh (#101) drives the WSL+SOS House Position seat, reading the sitting
    # roster from the WSL sponsor archive + binding to the WSL House Persons, so it
    # additionally orders After the WSL refresh (best-effort; a missing predecessor
    # just leaves an unmatched member logged, not wedged). Independent of the PDC
    # refresh (PDC is identifier-only since #101).
    "usa-wa-sos-refresh.service": {
        "After": {
            "network-online.target",
            "postgresql.service",
            "usa-wa-migrate.service",
            "usa-wa-wsl-refresh.service",
        },
        "Before": set(),
        "OnFailure": NOTIFY,
    },
    # DB-only sweep (#54) — re-hashes RawPayload vs content_hash. No WSL/PM
    # egress, so plain network.target (not network-online). Fails (exit 1) on a
    # mismatch → notify handler, since it IS the at-rest tamper detector.
    "usa-wa-integrity-sweep.service": {
        "After": {"network.target", "postgresql.service", "usa-wa-migrate.service"},
        "Before": set(),
        "OnFailure": NOTIFY,
    },
    # The notify handler is itself a oneshot; it carries no ordering and must NOT
    # set OnFailure on itself (a failed alert send must not recurse).
    "usa-wa-notify-failure@.service": {"After": set(), "Before": set(), "OnFailure": set()},
    # Timers carry their schedule in [Timer]; no [Unit] ordering by design.
    "usa-wa-reconcile-committee-active.timer": {
        "After": set(),
        "Before": set(),
        "OnFailure": set(),
    },
    "usa-wa-reconcile-committee-names.timer": {
        "After": set(),
        "Before": set(),
        "OnFailure": set(),
    },
    "usa-wa-reconcile-committee-meeting-names.timer": {
        "After": set(),
        "Before": set(),
        "OnFailure": set(),
    },
    "usa-wa-wsl-refresh.timer": {"After": set(), "Before": set(), "OnFailure": set()},
    "usa-wa-pdc-refresh.timer": {"After": set(), "Before": set(), "OnFailure": set()},
    "usa-wa-sos-refresh.timer": {"After": set(), "Before": set(), "OnFailure": set()},
    "usa-wa-integrity-sweep.timer": {"After": set(), "Before": set(), "OnFailure": set()},
}


# Shared branch guard (issue #87). Every code-running prod .service carries this
# as an ExecStartPre so the unit refuses to start off a non-main checkout — the
# enforcement behind the "main is the deployed code" convention #84 showed is not
# self-enforcing. Only .service units that run repo code are guarded; the notify
# handler (the alerting path, runs notify-failure.sh not app code) is exempt, and
# timers can't carry ExecStartPre (they only activate their guarded .service).
GUARD_EXEC = "/home/exedev/usa-wa/scripts/assert-main-checkout.sh"
UNGUARDED_SERVICES = {"usa-wa-notify-failure@.service"}

# Units whose Restart=on-failure engages systemd's start-rate limiter (#87 CR).
# Each must carry a StartLimit window wide enough for the burst to accumulate,
# else the guard's ExecStartPre failure restart-loops unbounded (finding 1) —
# systemd's default 10s window never trips at RestartSec=5.
RESTARTING_SERVICES = {"usa-wa.service", "usa-wa-sync-powermap.service"}


def _join_continuations(text: str) -> list[str]:
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


def parse_unit_deps(path: Path) -> tuple[set[str], set[str], set[str]]:
    """Return (After, Before, OnFailure) token sets from a unit's [Unit] section.

    Purpose-built rather than configparser: systemd directives may repeat
    across lines (additive) and duplicate keys, which configparser collapses
    or rejects. Tokens are space-split and accumulated as sets. Trailing-
    backslash line continuations are joined first (systemd folds a long
    ``After=a.service \\`` + newline ``b.service`` into one logical line).
    """
    after: set[str] = set()
    before: set[str] = set()
    on_failure: set[str] = set()
    section = None
    for raw in _join_continuations(path.read_text()):
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
        elif key.strip() == "OnFailure":
            on_failure.update(value.split())
    return after, before, on_failure


def parse_exec_start_pre(path: Path) -> list[str]:
    """Return the ExecStartPre command values from a unit's [Service] section.

    Space/prefix-char handling matters: systemd allows leading `+`/`!`/`-`
    modifiers on the executable. We compare against the bare guard path, so
    strip a leading modifier char before returning the first token.
    """
    values: list[str] = []
    section = None
    for raw in _join_continuations(path.read_text()):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "Service" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "ExecStartPre":
            values.append(value.strip())
    return values


def _guard_present(path: Path) -> bool:
    for value in parse_exec_start_pre(path):
        exe = value.lstrip("+!-").split()[0] if value else ""
        if exe == GUARD_EXEC:
            return True
    return False


def unit_value(path: Path, section: str, key: str) -> str | None:
    """Return the last value of `key` in `section` (systemd: last assignment wins), or None."""
    current = None
    found: str | None = None
    for raw in _join_continuations(path.read_text()):
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
            found = v.strip()
    return found


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


def _loop_is_bounded(interval: str, restart_sec: str, burst: str) -> bool:
    """Whether a Restart= unit's start-rate limiter can trip (loop is bounded).

    The burst — `burst` starts spaced ~`restart_sec` apart — must fit inside the
    `interval` window, else the limiter never trips and an ExecStartPre failure
    (e.g. the off-main guard) restart-loops forever. `burst` is a count, not a
    span. Shared by the production assertion and its has-teeth proof so the two
    can't drift.
    """
    # burst=0 and interval=0 are both systemd sentinels that DISABLE the limiter
    # (unbounded); treat either as not-bounded rather than letting burst=0 make
    # the `>=` trivially true.
    if int(burst) < 1 or parse_seconds(interval) < 1:
        return False
    return parse_seconds(interval) >= parse_seconds(restart_sec) * int(burst)


def test_branch_guard_on_every_code_running_service():
    """Every prod .service that runs repo code carries the main-branch guard (#87).

    Cross-checked against the on-disk .service set (minus the exempt notify
    handler), so a newly added service can't silently omit the guard — it either
    carries it or is added to UNGUARDED_SERVICES as an explicit decision.
    """
    on_disk_services = {p.name for p in DEPLOY.glob("*.service")}
    expected_guarded = on_disk_services - UNGUARDED_SERVICES
    actually_guarded = {name for name in on_disk_services if _guard_present(DEPLOY / name)}
    assert actually_guarded == expected_guarded


def test_exempt_services_carry_no_guard():
    """The notify handler must not carry the guard (it's the alerting path)."""
    for name in UNGUARDED_SERVICES:
        assert not _guard_present(DEPLOY / name)


def test_guard_script_exists_and_is_executable():
    script = DEPLOY.parent / "scripts" / "assert-main-checkout.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111, "guard script must be executable"


@pytest.mark.parametrize("name", sorted(RESTARTING_SERVICES))
def test_restart_loop_is_bounded(name):
    """A Restart= serving unit's StartLimit window must let the burst accumulate (#87 CR).

    Off-main, the guard fails ExecStartPre on every attempt; without a wide-enough
    window the limiter never trips and the unit restart-loops forever. The
    invariant StartLimitIntervalSec >= RestartSec * StartLimitBurst guarantees the
    burst (N starts ~RestartSec apart) fits inside one window, so the loop is
    provably bounded — while staying generous enough that a transient dependency
    blip still self-heals via Restart=.
    """
    path = DEPLOY / name
    assert unit_value(path, "Service", "Restart") == "on-failure"
    interval = unit_value(path, "Unit", "StartLimitIntervalSec")
    burst = unit_value(path, "Unit", "StartLimitBurst")
    restart_sec = unit_value(path, "Service", "RestartSec")
    assert interval is not None, f"{name} missing StartLimitIntervalSec"
    assert burst is not None, f"{name} missing StartLimitBurst"
    assert restart_sec is not None, f"{name} missing RestartSec"
    assert _loop_is_bounded(interval, restart_sec, burst)


def test_every_restarting_service_is_declared():
    """A new Restart=on-failure serving unit must opt into the bounded-loop assertion."""
    on_disk = {
        p.name
        for p in DEPLOY.glob("*.service")
        if unit_value(p, "Service", "Restart") == "on-failure"
    }
    assert on_disk == RESTARTING_SERVICES


def test_bounded_loop_invariant_would_fail_the_default_window():
    # Proof the shared predicate has teeth: systemd's default (10s / 5) at
    # RestartSec=5 is NOT bounded (10 < 5*5) — exactly the unbounded loop finding
    # 1 fixed. Uses the same _loop_is_bounded as the production assertion, so the
    # proof actually guards it (findings 8): a wrong edit to the predicate fails
    # here too.
    assert not _loop_is_bounded("10", "5", "5")
    # And a widened window (our 5min / 10) is bounded.
    assert _loop_is_bounded("5min", "5", "10")
    # systemd sentinels that disable the limiter (unbounded) are not bounded,
    # even though burst=0 would make the bare `>=` trivially true.
    assert not _loop_is_bounded("300", "5", "0")
    assert not _loop_is_bounded("0", "5", "10")


def test_parse_seconds_handles_systemd_forms():
    assert parse_seconds("300") == 300  # bare number = seconds
    assert parse_seconds("300s") == 300
    assert parse_seconds("5min") == 300
    assert parse_seconds("1min 30s") == 90
    assert parse_seconds("2h") == 7200
    with pytest.raises(ValueError):
        parse_seconds("5furlongs")  # unrecognized unit fails loudly, not silently


def test_every_unit_has_an_expected_entry():
    """Adding a unit forces an explicit dependency decision here."""
    on_disk = {p.name for p in DEPLOY.glob("*.service")} | {p.name for p in DEPLOY.glob("*.timer")}
    assert on_disk == set(EXPECTED)


@pytest.mark.parametrize("name", EXPECTED)
def test_dependency_edges_match(name):
    after, before, on_failure = parse_unit_deps(DEPLOY / name)
    assert after == EXPECTED[name]["After"]
    assert before == EXPECTED[name]["Before"]
    assert on_failure == EXPECTED[name]["OnFailure"]


def test_parser_folds_line_continuations(tmp_path):
    unit = tmp_path / "wrapped.service"
    unit.write_text(
        "[Unit]\nAfter=a.service \\\n      b.service\nBefore=c.service\n"
        "OnFailure=notify@%n.service\n[Service]\nExecStart=/bin/true\n"
    )
    after, before, on_failure = parse_unit_deps(unit)
    assert after == {"a.service", "b.service"}
    assert before == {"c.service"}
    assert on_failure == {"notify@%n.service"}


def test_parser_tolerates_trailing_backslash_at_eof(tmp_path):
    # Dangling continuation on the final line — the `if pending` branch.
    unit = tmp_path / "dangling.service"
    unit.write_text("[Unit]\nAfter=a.service \\")
    after, before, on_failure = parse_unit_deps(unit)
    assert after == {"a.service"}
    assert before == set()
    assert on_failure == set()
