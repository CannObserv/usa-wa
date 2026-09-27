"""The weekly integrity sweep verifies the raw store, not Postgres (#412 PR C).

``usa-wa-integrity-sweep.service`` ran ``clearinghouse_core.integrity`` (#54), which
re-hashes Postgres ``RawPayload`` rows. The raw file store is now the record every dataset
is built from, and its sweep (``clearinghouse_core.raw_integrity``, #304) had no timer, so
nothing checked it. The unit now runs the raw sweep. The Postgres copies get one last check
from PR F's final ``raw_export``, which re-hashes every body it carries before it lands.

Pinned by driving the sweep's own ``main()`` with the unit's argv rather than by matching
strings, so a renamed flag fails here instead of at the timer's next elapse. No DB:
``patch_job_runtime`` stands in for the ledger session.
"""

from __future__ import annotations

import json
import re
import shlex

from systemd_units import DEPLOY, unit_value, unit_values

from clearinghouse_core.raw_integrity import STATE_FILENAME, main
from clearinghouse_core.rawstore import RawStore
from clearinghouse_core.testing import patch_job_runtime

SERVICE = DEPLOY / "usa-wa-integrity-sweep.service"
TIMER = DEPLOY / "usa-wa-integrity-sweep.timer"
PIPELINE = DEPLOY / "usa-wa-pipeline.service"
SWEEP_MODULE = "clearinghouse_core.raw_integrity"

#: One weekday, every week (``Sun *-*-*``). The docs guard owns the full grammar.
WEEKLY_RE = re.compile(r"^[A-Z][a-z]{2}\s+\*-\*-\*\s")


def _sweep_argv() -> list[str]:
    """The arguments the unit passes the sweep — everything after ``-m <module>``."""
    commands = unit_values(SERVICE, "Service", "ExecStart")
    assert len(commands) == 1, f"{SERVICE.name}: expected one ExecStart, found {commands}"
    tokens = shlex.split(commands[0])
    assert tokens[tokens.index("-m") + 1] == SWEEP_MODULE, (
        f"{SERVICE.name} no longer runs the raw-store sweep"
    )
    return tokens[tokens.index("-m") + 2 :]


def _seed(root, body: bytes) -> RawStore:
    store = RawStore(root, "src-a")
    run = store.open_run()
    run.record("r0", body, url="u")
    run.close()
    return store


def test_the_unit_runs_the_raw_store_sweep_and_persists_its_cursor(tmp_path, monkeypatch) -> None:
    """Not a dry run: the rolling cursor must advance between weekly runs, or a store larger
    than one byte budget would re-verify its first slice forever."""
    patch_job_runtime(monkeypatch)
    monkeypatch.setenv("USA_WA_RAW_ROOT", str(tmp_path))
    for index in range(3):
        _seed(tmp_path, f"body-{index}".encode())

    argv = _sweep_argv()
    assert "--dry-run" not in argv
    assert main([*argv, "--byte-budget", "1"]) == 0
    assert json.loads((tmp_path / STATE_FILENAME).read_text())["cursors"][""]


def test_a_tampered_object_exits_one_so_the_alert_fires(tmp_path, monkeypatch) -> None:
    """The sweep is the at-rest tamper detector: exit 1 is what ``OnFailure=`` emails on."""
    patch_job_runtime(monkeypatch)
    monkeypatch.setenv("USA_WA_RAW_ROOT", str(tmp_path))
    store = _seed(tmp_path, b"original")
    [obj] = [path for path in store.objects_dir.rglob("*") if path.is_file()]
    obj.write_bytes(b"tampered")

    assert main(_sweep_argv()) == 1
    assert unit_values(SERVICE, "Unit", "OnFailure") == ["usa-wa-notify-failure@%n.service"]


def test_the_unit_sweeps_the_store_the_harvests_write() -> None:
    """With ``USA_WA_RAW_ROOT`` unset the store is ``raw/`` under the working directory, so
    the sweep and the nightly harvests must share one, or the sweep verifies an empty
    directory and reports ``ok``."""
    assert unit_value(SERVICE, "Service", "WorkingDirectory") == unit_value(
        PIPELINE, "Service", "WorkingDirectory"
    )
    assert unit_values(SERVICE, "Service", "EnvironmentFile") == unit_values(
        PIPELINE, "Service", "EnvironmentFile"
    )


def test_the_timer_stays_weekly_and_catches_up_after_downtime() -> None:
    (on_calendar,) = unit_values(TIMER, "Timer", "OnCalendar")
    assert WEEKLY_RE.match(on_calendar), f"{TIMER.name}: {on_calendar!r} is not weekly"
    assert unit_value(TIMER, "Timer", "Persistent") == "true"
