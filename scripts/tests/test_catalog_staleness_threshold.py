"""The catalog's published staleness threshold is pinned to the deployed schedule (#386).

``catalog.json`` tells a consumer ``stale_after_seconds``: how long after
``checked_at`` a missing run is a finding rather than a quiet day. The number is a
constant in the publisher, and the schedule it describes lives in two unit files
the publisher never reads — so a timer edit could silently invalidate a threshold
power-map derives its alerting from. This pins one against the other.

The bounds, both directions:

* **Floor** — the latest a healthy run can land: the timer period, plus the
  timer's ``RandomizedDelaySec=``, plus the service's ``TimeoutStartSec=`` (publish
  runs inside the chain, so it completes before the unit's own timeout or not at
  all). Below it, a slow-but-healthy night reads as an outage.
* **Ceiling** — under two periods, so a single missed run is detected rather than
  hidden until a second one is missed too.

Cross-package by nature (pipeline constant ⟷ ``deploy/`` units), which is why it
lives here rather than in the pipeline's own suite.
"""

import re

from systemd_units import DEPLOY, parse_seconds, unit_value, unit_values

from usa_wa_pipeline.publish import STALE_AFTER_SECONDS

TIMER = DEPLOY / "usa-wa-pipeline.timer"
SERVICE = DEPLOY / "usa-wa-pipeline.service"

DAILY_RE = re.compile(r"^\*-\*-\*\s+\d{2}:\d{2}:\d{2}\s+UTC$")
DAY = 86400


def _period_seconds() -> int:
    """The pipeline timer's period — daily is the only form this guard models."""
    values = unit_values(TIMER, "Timer", "OnCalendar")
    assert len(values) == 1, f"{TIMER.name}: expected one OnCalendar=, found {values!r}"
    assert DAILY_RE.match(values[0]), (
        f"{TIMER.name}: OnCalendar={values[0]!r} is not daily — re-derive "
        "STALE_AFTER_SECONDS for the new cadence and extend this guard"
    )
    return DAY


def _latest_healthy_publish() -> int:
    jitter = unit_value(TIMER, "Timer", "RandomizedDelaySec")
    timeout = unit_value(SERVICE, "Service", "TimeoutStartSec")
    assert timeout, f"{SERVICE.name}: no TimeoutStartSec= — the chain has no bound to derive from"
    return _period_seconds() + parse_seconds(jitter or "0") + parse_seconds(timeout)


def test_a_slow_healthy_night_is_not_reported_stale() -> None:
    floor = _latest_healthy_publish()
    assert STALE_AFTER_SECONDS >= floor, (
        f"STALE_AFTER_SECONDS={STALE_AFTER_SECONDS} is below the latest a healthy run "
        f"can publish ({floor}s after the previous one) — raise it"
    )


def test_one_missed_run_is_reported_stale() -> None:
    ceiling = 2 * _period_seconds()
    assert STALE_AFTER_SECONDS < ceiling, (
        f"STALE_AFTER_SECONDS={STALE_AFTER_SECONDS} hides a missed run until a second "
        f"one is missed too (>= {ceiling}s) — lower it"
    )
