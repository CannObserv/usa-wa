"""The patching-hosts knob reads clean, and keeps the decisions it records (#497).

``.skills/patching-hosts`` tells the vendored ``patching-hosts`` skill what only
this repo's owner knows: the window, the quiet ranges, the backup that stands in
for a dump, who is told before a restart. **A malformed line makes the host
report-only** until it is fixed, which reads as "nothing to do" at the next
monthly run rather than as an error, so the skill's own reader runs here.

The reader ships in the submodule; an unpopulated one (a fresh worktree) skips
that test. The rest are pure parses of the knob and ``deploy/``:

- **Each timer's run sits inside a quiet range**, from 5 min before its
  ``OnCalendar=`` to its ``RandomizedDelaySec=`` plus its service's
  ``TimeoutStartSec=``. Moving a timer without its range fails here, not as a
  patch step started on top of a pipeline run.
- **The inflight count sees a running oneshot.** Every ``usa-wa-*`` oneshot is
  ``activating`` while it runs (no ``RemainAfterExit=``), so ``--state=active``
  alone counts 0 mid-run; ``usa-wa-migrate`` is the one that stays ``active``.
- **No knob command escalates.** ``health`` and ``inflight`` run as the invoking
  user by design; a ``sudo`` in one would hand root to whoever commits the knob.
- **The backup covers the data store**, so the recovery point is the nightly
  GCS backup (#434), not a second dump.
- **No ``image-owner`` line, and only public callers**: this repo is public, so
  either would publish a private repo's name (gregoryfoster/skills#350).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]  # scripts/tests/ → repo root
KNOB = REPO / ".skills" / "patching-hosts"
READ_KNOB = REPO / "skills" / "patching-hosts" / "scripts" / "read-knob.sh"
DEPLOY = REPO / "deploy"
HOST = "usa-wa"

# Past every review-by date the knob could hold.
FAR_FUTURE = "2099-12-31"
# How far ahead of OnCalendar= each quiet range opens.
LEAD_MINUTES = 5
DAY = 24 * 60

PUBLIC_CALLERS = {"CannObserv/power-map"}


def _read_knob(today: str | None = None) -> dict:
    """The knob as the vendored reader resolves it for this host."""
    if not READ_KNOB.is_file():
        pytest.skip("skills-vendor/ unpopulated: git submodule update --init --recursive")
    bash = shutil.which("bash")
    assert bash
    argv = [bash, str(READ_KNOB), "--config", str(KNOB), "--host", HOST]
    if today:
        argv += ["--today", today]
    result = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _directives() -> list[list[str]]:
    """Each directive line's words; ``#`` opens a comment at a line's start or
    after whitespace (knob.md), so a command keeps a bare ``#``."""
    lines = []
    for raw in KNOB.read_text().splitlines():
        words = []
        for word in raw.split():
            if word.startswith("#"):
                break
            words.append(word)
        if words:
            lines.append(words)
    return lines


def _named(directive: str) -> list[list[str]]:
    return [words[1:] for words in _directives() if words[0] == directive]


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _seconds(value: str) -> int:
    """A systemd time span as this repo writes them: ``300``, ``10min``, ``1h``."""
    match = re.fullmatch(r"(\d+)\s*(s|sec|min|m|h)?", value.strip())
    assert match, value
    scale = {None: 1, "s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600}[match.group(2)]
    return int(match.group(1)) * scale


def _unit_setting(path: Path, key: str) -> str | None:
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return None


def _quiet_ranges() -> list[tuple[int, int]]:
    """Each weekday-free quiet range as (start, end) minutes, unwrapped past midnight."""
    ranges = []
    for words in _named("quiet"):
        assert len(words) == 1, f"a weekday-scoped quiet range needs its own check: {words}"
        start, end = (_minutes(t) for t in words[0].split("-"))
        ranges.append((start, end if end > start else end + DAY))
    return ranges


def _covered(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(lo <= start and end <= hi for lo, hi in ranges) or any(
        lo <= start + DAY and end + DAY <= hi for lo, hi in ranges
    )


def _timer_runs() -> list[tuple[str, int, int]]:
    """Each timer's (name, earliest, latest) minute of day a run can be active."""
    runs = []
    for timer in sorted(DEPLOY.glob("usa-wa-*.timer")):
        calendar = _unit_setting(timer, "OnCalendar")
        assert calendar, timer
        hours, minutes = re.search(r"(\d\d):(\d\d):\d\d", calendar).groups()
        at = int(hours) * 60 + int(minutes)
        delay = _seconds(_unit_setting(timer, "RandomizedDelaySec") or "0")
        service = timer.with_suffix(".service")
        timeout = _unit_setting(service, "TimeoutStartSec")
        # A oneshot's default is infinity: no quiet range can cover an unbounded run.
        assert timeout, f"{service.name}: a oneshot without TimeoutStartSec= has no bound"
        timeout = _seconds(timeout)
        latest = at + -(-(delay + timeout) // 60)
        runs.append((timer.name, at - LEAD_MINUTES, latest))
    return runs


@pytest.mark.parametrize("today", [None, FAR_FUTURE])
def test_the_knob_reads_clean(today: str | None) -> None:
    """No malformed line on any date; an expired exception is the probe's to report."""
    knob = _read_knob(today)
    assert knob["knob"]["present"]
    assert [f for f in knob["findings"] if f["kind"] != "expired-exception"] == []


def test_the_host_is_scheduled_production_and_not_report_only() -> None:
    knob = _read_knob()
    assert knob["report_only"] is False, knob["report_only_reasons"]
    assert knob["class"]["value"] == "production"
    assert knob["posture"]["value"] == "scheduled"
    assert knob["window"], "no window: apply.sh refuses every step"


def test_every_timer_run_sits_inside_a_quiet_range() -> None:
    ranges = _quiet_ranges()
    runs = _timer_runs()
    assert runs
    for name, start, end in runs:
        at = f"{start // 60:02d}:{start % 60:02d}"
        assert _covered(start, end, ranges), f"{name}: the run from {at} meets no quiet range"


def test_no_window_meets_a_quiet_range() -> None:
    ranges = _quiet_ranges()
    for words in _named("window"):
        start, end = (_minutes(t) for t in words[1].split("-"))
        end = end if end > start else end + DAY
        for lo, hi in ranges:
            for shift in (0, DAY, -DAY):
                assert end <= lo + shift or hi + shift <= start, (words, (lo, hi))


def test_inflight_counts_running_oneshots_but_not_migrate() -> None:
    commands = [" ".join(words) for words in _named("inflight")]
    (oneshots,) = [c for c in commands if "usa-wa-*" in c]
    states = oneshots.split("--state=", 1)[1].split()[0].split(",")
    assert "activating" in states
    assert "usa-wa-migrate.service" in oneshots
    assert "RemainAfterExit=yes" in (DEPLOY / "usa-wa-migrate.service").read_text()


def test_no_knob_command_escalates() -> None:
    commands = [" ".join(words) for words in _named("health") + _named("inflight")]
    assert commands
    for command in commands:
        for word in ("sudo", "runuser", "su ", "pkexec"):
            assert word not in command, f"{word!r} in a knob command: {command}"


def test_the_backup_covers_the_postgres_datastore() -> None:
    assert _named("datastore") == [["postgres", "postgresql@16-main", "usa_wa"]]
    assert _named("backup") == [["usa-wa-backup.service", "postgresql@16-main"]]
    assert (DEPLOY / "usa-wa-backup.service").is_file()


def test_the_service_is_usa_wa() -> None:
    assert _named("service") == [["usa-wa.service"]]


def test_only_public_callers_and_no_image_owner() -> None:
    assert {words[0] for words in _named("caller")} == PUBLIC_CALLERS
    assert _named("image-owner") == []


OWNER_WINDOWS = [["Tue", "16:00-23:30"], ["Wed", "16:00-23:30"]]

# An out-of-cycle window for a security bulletin (policy.md § two lanes), each
# keyed to the issue whose closing PR removes it again.
TEMPORARY_WINDOWS = {
    ("Sun", "17:00-23:30"): "#534",  # Tailscale 1.104.2, TS-2026-013 (owner, 2026-10-25)
}


def test_the_window_is_the_owners() -> None:
    """Tue and Wed, 16:00-23:30 UTC (owner, 2026-10-14): 09:00-16:30 PDT, 08:00-15:30 PST."""
    windows = _named("window")
    assert [w for w in windows if w in OWNER_WINDOWS] == OWNER_WINDOWS


def test_every_other_window_is_a_listed_temporary_one() -> None:
    """A window beyond the owner's is temporary, named here with its issue."""
    others = {tuple(w) for w in _named("window") if w not in OWNER_WINDOWS}
    assert others == set(TEMPORARY_WINDOWS)


def test_tailscale_is_followed_scoped_and_its_own_step() -> None:
    """Followed in the maintenance lane, scoped by the pin file, and held: its
    postinst restarts tailscaled, so it is approved on its own (3(a))."""
    assert _named("origin") == [["pkgs.tailscale.com", "follow"]]
    assert ["tailscale", "tailscale"] in _named("hold")
    assert (DEPLOY / "apt-preferences.d" / "tailscale.pref").is_file()


def test_the_checkout_tree_is_owned_by_this_repo() -> None:
    """The probe reads a tree's owner from its origin, and misses the deploy-key
    alias remote (``git@github-usa-wa:…``), so the knob names it: without this
    line the language class reports owner ``unknown``."""
    assert ["/home/exedev/usa-wa", "self"] in _named("owner")


def test_fwupd_is_declared_purged() -> None:
    """The image deleted fwupd's motd conffile, so its upgrade stopped
    unattended-upgrade at a conffile prompt; it was purged with its 53 orphans
    on 2026-10-14 (#497, wslcb-licensing-tracker#193's route)."""
    (purged,) = [e for e in _named("exception") if e[0] == "purged:fwupd"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", purged[1])


def test_migrate_is_the_only_oneshot_that_stays_active() -> None:
    """The inflight count excludes ``usa-wa-migrate`` alone. A second unit with
    ``RemainAfterExit=yes`` would read ``active (exited)`` for good, so the
    count would never reach 0 and every step would refuse."""
    remain = {
        unit.name
        for unit in DEPLOY.glob("usa-wa-*.service")
        if "RemainAfterExit=yes" in unit.read_text()
    }
    assert remain == {"usa-wa-migrate.service"}
