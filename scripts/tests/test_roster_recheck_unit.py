"""The roster edition re-check is a read-only, cache-bypassing fetch on a monthly timer (#237).

``usa-wa-roster-pdf-recheck.service`` is the roster harvest (#225) with a fixed argv, and
that argv is the whole design:

* ``--dry-run`` — the harness rolls the archive write back, so a timer can never mutate
  the archive or move what a ``legroster:<revision>`` citation names.
* ``--force`` — the source's freshness cache is 90 days. Unforced, the check is a cache
  hit that fetches nothing for 90 days after every real harvest and reports ``ok``
  throughout: a detector that cannot see. A dry run never refreshes that cache, which is
  why forcing it costs nothing but the one GET the check exists to make.
* no ``--revision`` — the check compares against ``DEFAULT_REVISION`` in code, the one
  place an operator bumps after archiving a new edition. A revision pinned here would be
  a second place to forget, and would keep alerting on an edition already archived.

Pinned by driving the harvest's own ``main()`` with the unit's argv rather than by
matching strings, so a flag renamed in the CLI fails here instead of at the timer's next
elapse. No DB: ``patch_job_runtime`` stands in for the session.
"""

from __future__ import annotations

import re
import shlex
from unittest.mock import patch

import pytest
from systemd_units import DEPLOY, unit_value, unit_values

from clearinghouse_core.job import EXIT_DEGRADED
from clearinghouse_core.testing import patch_job_runtime
from usa_wa_adapter_legislature.roster_pdf import harvest as harvest_module
from usa_wa_adapter_legislature.roster_pdf.harvest import DEFAULT_REVISION, RosterHarvestSummary

SERVICE = DEPLOY / "usa-wa-roster-pdf-recheck.service"
TIMER = DEPLOY / "usa-wa-roster-pdf-recheck.timer"
HARVEST_MODULE = "usa_wa_adapter_legislature.roster_pdf.harvest"

#: A day of the month, every month (``*-*-01``). The docs guard owns the full grammar.
MONTHLY_RE = re.compile(r"^\*-\*-\d{2}\s")


def _harvest_argv() -> list[str]:
    """The arguments the unit passes the harvest module — everything after ``-m <module>``."""
    commands = unit_values(SERVICE, "Service", "ExecStart")
    assert len(commands) == 1, f"{SERVICE.name}: expected one ExecStart, found {commands}"
    tokens = shlex.split(commands[0])
    assert tokens[tokens.index("-m") + 1] == HARVEST_MODULE, (
        f"{SERVICE.name} no longer runs the roster harvest"
    )
    return tokens[tokens.index("-m") + 2 :]


def test_the_unit_runs_a_forced_dry_run_against_the_code_default(monkeypatch) -> None:
    """The unit's argv, parsed by the harvest itself: rolled back, forced, default revision."""
    session = patch_job_runtime(monkeypatch)
    calls: list[dict] = []

    async def _fake_harvest(_session, **kwargs):
        calls.append(kwargs)
        return RosterHarvestSummary(revision=kwargs["revision"], archived=1)

    with patch.object(harvest_module, "harvest_roster", _fake_harvest):
        assert harvest_module.main(_harvest_argv()) == 0

    assert calls == [{"revision": DEFAULT_REVISION, "dry_run": True, "force": True}]
    assert (session.committed, session.rolled_back) == (0, 1), "a dry run must roll back"


def test_the_unit_pins_no_revision_of_its_own() -> None:
    """``DEFAULT_REVISION`` is the one place a new edition is recorded; the unit defers to it."""
    assert not any(arg.startswith("--revision") for arg in _harvest_argv())


@pytest.mark.parametrize(
    "condition",
    [{"mismatch": "stamps 2027-06-01"}, {"unavailable": True}],
    ids=["new-edition", "unlocatable"],
)
def test_both_operator_conditions_exit_degraded_so_the_alert_fires(monkeypatch, condition) -> None:
    """``OnFailure=`` fires on any non-zero exit, but the alert's subject line carries the
    code, and the runbook reads 4 as "act on the edition" and 1 as "an outage, wait". Both
    operator conditions must therefore exit exactly 4 (CR 3)."""
    patch_job_runtime(monkeypatch)

    async def _degraded(_session, **kwargs):
        return RosterHarvestSummary(revision=kwargs["revision"], archived=0, **condition)

    with patch.object(harvest_module, "harvest_roster", _degraded):
        assert harvest_module.main(_harvest_argv()) == EXIT_DEGRADED
    assert unit_values(SERVICE, "Unit", "OnFailure") == ["usa-wa-notify-failure@%n.service"]


def test_the_timer_is_monthly_and_catches_up_after_downtime() -> None:
    """Monthly, not the spec's quarterly (decided on #237). ``Persistent=`` matters more here
    than on a daily: a run missed while the box is down would otherwise wait a whole month."""
    (on_calendar,) = unit_values(TIMER, "Timer", "OnCalendar")
    assert MONTHLY_RE.match(on_calendar), f"{TIMER.name}: {on_calendar!r} is not monthly"
    assert unit_value(TIMER, "Timer", "Persistent") == "true"
