"""The WSL availability probe's unit: a daily, write-free measurement with a window (#135).

``usa-wa-wsl-availability-probe.service`` is ``probe_availability`` with a fixed argv, and
the argv is the design: the incoming biennium, the last day worth measuring, and an absolute
log path. Pinned by driving the probe's own ``main()`` with the unit's argv, so a renamed
flag fails here rather than at the timer's next elapse.
"""

from __future__ import annotations

import shlex
from datetime import date
from pathlib import Path
from unittest.mock import patch

from systemd_units import DEPLOY, unit_value, unit_values

from clearinghouse_core.job import JobResult
from clearinghouse_core.testing import patch_job_runtime
from usa_wa_adapter_legislature import probe_availability as probe_module

SERVICE = DEPLOY / "usa-wa-wsl-availability-probe.service"
TIMER = DEPLOY / "usa-wa-wsl-availability-probe.timer"
MODULE = "usa_wa_adapter_legislature.probe_availability"


def _argv() -> list[str]:
    commands = unit_values(SERVICE, "Service", "ExecStart")
    assert len(commands) == 1, f"{SERVICE.name}: expected one ExecStart, found {commands}"
    tokens = shlex.split(commands[0])
    assert tokens[tokens.index("-m") + 1] == MODULE, f"{SERVICE.name} no longer runs the probe"
    return tokens[tokens.index("-m") + 2 :]


def test_the_unit_measures_2027_28_through_february_into_data(monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    calls: list[tuple] = []

    async def _fake_probe(biennium, log, **kwargs):
        calls.append((biennium, log, kwargs))
        return JobResult.ok({})

    with patch.object(probe_module, "probe", _fake_probe):
        assert probe_module.main(_argv()) == 0

    [(biennium, log, kwargs)] = calls
    assert biennium == "2027-28"
    assert kwargs == {"until": date(2027, 2, 28), "record": True}
    assert log == Path("/home/exedev/usa-wa/data/research/wsl-availability.jsonl")


def test_a_change_reaches_the_operator() -> None:
    """Exit 4 is the news; without OnFailure= it would land in the journal and nowhere else."""
    assert unit_value(SERVICE, "Unit", "OnFailure") == "usa-wa-notify-failure@%n.service"


def test_the_timer_is_daily() -> None:
    assert unit_value(TIMER, "Timer", "OnCalendar") == "*-*-* 09:20:00 UTC"
