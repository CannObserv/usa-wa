"""The PM producer CLIs' shared exit contract, on top of the job harness (#179b).

Twelve sidecar-adjacent CLIs — the reconcilers, the heals, the prune, the validate, the
event producer, the contact-label backfill — all publish the *same* documented exit
codes (`docs/COMMANDS-SYNC.md`):

===== ============================================================================
``0``  clean, or a dry run
``1``  the run happened but some rows were rejected/failed by PM
``2``  a global auth block (:class:`DeliveryBlockedError` — check ``POWERMAP_API_KEY``)
``3``  a guardrail abort: the run **took no action** (empty pull, rename storm, prune floor)
===== ============================================================================

Before #179b each one re-implemented that mapping inline, which is why the ``3`` had to
be carved out of the harness's own code space (:mod:`clearinghouse_core.job` deliberately
skips it). This module states the mapping once so the family cannot drift, and so the
#178 ledger records an honest ``outcome`` beneath the bespoke code:

- an abort is **``degraded``** — the defining case of "ran to completion, work did not
  land" — carried out on ``3`` rather than the harness's ``EXIT_DEGRADED``, because ``3``
  is what systemd units and operators already read here;
- an auth block and a rejected-rows run are both **``failed``**, on ``2`` and ``1``.

The exit code is per-job convention; the ledger outcome is comparable across every job in
the repo. That is exactly the split :class:`~clearinghouse_core.job.JobResult`'s
``exit_code`` override exists for.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from clearinghouse_core.job import EXIT_FAILED, JobResult
from clearinghouse_sync_powermap.client import DeliveryBlockedError

EXIT_AUTH_BLOCKED = 2
"""A global auth block. Shares the harness's ``EXIT_CONFIG`` code deliberately: both mean
"the operator must fix the environment before this job can run", and both were ``2`` here
before #179b."""

EXIT_ABORTED = 3
"""A guardrail abort — the run took no action. The code
:mod:`clearinghouse_core.job` leaves free precisely for this convention."""

Summary = dict[str, Any]


def rows_unsettled(summary: Summary) -> bool:
    """Whether PM rejected or failed any row — the family's ``1`` condition."""
    return bool(summary.get("rejected", 0) or summary.get("failed", 0))


def never(_summary: Summary) -> bool:
    """For the jobs whose only non-zero outcomes are an abort or an auth block."""
    return False


def pm_job_result(summary: Summary, *, failed: bool = False) -> JobResult:
    """Map a reconciler summary onto ``(outcome, exit_code)``. See the module docstring."""
    if summary.get("aborted"):
        return JobResult.degraded(summary, exit_code=EXIT_ABORTED)
    if failed:
        return JobResult.failed(summary, exit_code=EXIT_FAILED)
    return JobResult.ok(summary)


async def run_pm_job(
    work: Callable[[], Awaitable[Summary]],
    *,
    failed_when: Callable[[Summary], bool] = rows_unsettled,
) -> JobResult:
    """Await ``work`` and grade it, turning an auth block into ``2`` instead of a traceback.

    The blocked diagnostic goes to **stderr** since #179b (it was stdout): the harness owns
    stdout's last line, and interleaving a second JSON object there would break anything
    parsing the run summary.
    """
    try:
        summary = await work()
    except DeliveryBlockedError as exc:
        json.dump(
            {"error": "delivery blocked — check POWERMAP_API_KEY", "detail": str(exc)}, sys.stderr
        )
        sys.stderr.write("\n")
        return JobResult.failed(
            {"error": "delivery_blocked", "detail": str(exc)}, exit_code=EXIT_AUTH_BLOCKED
        )
    return pm_job_result(summary, failed=failed_when(summary))
