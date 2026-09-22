"""The catalog's staleness deadline is pinned to the deployed schedule (#386).

``catalog.json`` tells a consumer ``stale_after``: the instant by which the next
``checked_at`` is due — the next scheduled run after the last one, plus a grace
for that run to publish. The publisher computes it from two constants, and the
schedule they restate lives in two unit files the publisher never reads, so a
timer edit could silently move the deadline power-map alerts on. This pins one
against the other.

* **The schedule** — :data:`SCHEDULED_RUN_UTC` is the timer's ``OnCalendar=``.
* **Grace floor** — at least the timer's ``RandomizedDelaySec=`` plus the
  service's ``TimeoutStartSec=`` (publish runs inside the chain, so it completes
  before the unit's own timeout or not at all). Below it, a slow-but-healthy
  night reads as an outage.
* **Grace ceiling** — under one period, so the deadline passes before the next
  scheduled run could quietly repair a miss.

Cross-package by nature (pipeline constants ⟷ ``deploy/`` units), which is why
it lives here rather than in the pipeline's own suite.
"""

import re
from datetime import time, timedelta

from systemd_units import DEPLOY, parse_seconds, unit_value, unit_values

from usa_wa_pipeline.publish import PUBLISH_GRACE, SCHEDULED_RUN_UTC

TIMER = DEPLOY / "usa-wa-pipeline.timer"
SERVICE = DEPLOY / "usa-wa-pipeline.service"

DAILY_RE = re.compile(r"^\*-\*-\*\s+(?P<hh>\d{2}):(?P<mm>\d{2}):(?P<ss>\d{2})\s+UTC$")


def _scheduled_time() -> time:
    """The pipeline timer's daily run time — daily is the only form this guard models."""
    values = unit_values(TIMER, "Timer", "OnCalendar")
    assert len(values) == 1, f"{TIMER.name}: expected one OnCalendar=, found {values!r}"
    match = DAILY_RE.match(values[0])
    assert match, (
        f"{TIMER.name}: OnCalendar={values[0]!r} is not daily — re-derive the "
        "catalog's stale_after for the new cadence and extend this guard"
    )
    return time(int(match["hh"]), int(match["mm"]), int(match["ss"]))


def test_the_deadline_restates_the_timers_schedule() -> None:
    assert _scheduled_time() == SCHEDULED_RUN_UTC, (
        f"{TIMER.name} runs at {_scheduled_time()} UTC but the catalog's stale_after "
        f"assumes {SCHEDULED_RUN_UTC} — update SCHEDULED_RUN_UTC"
    )


def test_a_slow_healthy_night_is_not_reported_stale() -> None:
    jitter = unit_value(TIMER, "Timer", "RandomizedDelaySec")
    timeout = unit_value(SERVICE, "Service", "TimeoutStartSec")
    assert timeout, f"{SERVICE.name}: no TimeoutStartSec= — the chain has no bound to derive from"
    floor = timedelta(seconds=parse_seconds(jitter or "0") + parse_seconds(timeout))
    assert PUBLISH_GRACE >= floor, (
        f"PUBLISH_GRACE={PUBLISH_GRACE} is shorter than the latest a healthy run can "
        f"publish after its scheduled time ({floor}) — raise it"
    )


def test_the_deadline_passes_before_the_next_run() -> None:
    assert PUBLISH_GRACE < timedelta(days=1), (
        f"PUBLISH_GRACE={PUBLISH_GRACE} reaches the next scheduled run, which could "
        "repair a miss before the deadline ever showed it — lower it"
    )
