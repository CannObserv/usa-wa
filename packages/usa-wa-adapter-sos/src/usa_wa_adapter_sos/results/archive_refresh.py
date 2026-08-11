"""Daily Phase-A archive refresh for the SOS results source (#201).

    python -m usa_wa_adapter_sos.results.archive_refresh

Archives every ``sos-legresults:<YYYYMMDD>`` cohort the **current biennium's** membership can
be decided by (#106) — the even seating year and the odd mid-biennium special — through the
sweep in :mod:`~usa_wa_adapter_sos.results.harvest`, forced past the freshness TTL for daily
determinism. Each cohort archives in its own SAVEPOINT, so an un-certified odd cohort or a
transient votewa failure skips that cohort, not the run.

**Why it is here and not in the fact (#201).** This half used to run inside
``usa_wa_facts_seats.house.refresh``, which made a *fact* import an adapter ``transport`` — one
of the two `import-linter` exceptions #189 had to grant. Sourcing belongs with the source: the
adapter owns "refresh my archive", the fact owns "rebuild from the archive"
(:mod:`usa_wa_facts_seats.house.refresh`, still the seat's daily driver).

**Systemd.** ``usa-wa-sos-archive-refresh.service``, pulled in and ordered before the rebuild
unit ``usa-wa-sos-refresh.service`` by a ``Wants=``/``After=`` pair — weak on purpose. A votewa
outage alerts on *this* unit and leaves the rebuild to re-derive the seat from the last good
archive; the seat still tracks the WSL roster, which votewa has no part in.

**Flags.** ``--force`` is this half's, and it is on by default here: the daily archive must be
the day's wire, not a cache hit. (The historical sweep,
``python -m usa_wa_adapter_sos.results.harvest``, exposes it as an opt-in flag.) The rebuild
half has no cache to bypass and so has no ``--force``. ``USA_WA_BIENNIUM`` governs **both**
halves — each resolves it independently and warns when it names a closed biennium.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_sos.results.harvest import HarvestSummary, harvest_results
from usa_wa_adapter_sos.results.transport import SOSResultsClient
from usa_wa_common.elections import election_years_for_biennium

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
#: Distinct from the fact's ``sos-refresh`` and the sweep's ``sos-results-harvest``:
#: ``/api/v1/health/jobs`` is ``DISTINCT ON (job_slug)``, so a shared slug would hide one
#: half's staleness behind the other's last run.
JOB_SLUG = "sos-archive-refresh"


async def refresh_archive(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    results_client: SOSResultsClient | None = None,
    force: bool = True,
) -> HarvestSummary:
    """Archive the current biennium's results cohorts. ``results_client`` is injectable for
    tests. Operates in the caller's transaction (the harness commits, or rolls back on
    ``--dry-run``)."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    current = biennium_for_date(datetime.now(UTC).date())
    if biennium != current:
        # A stale USA_WA_BIENNIUM pin points the daily archive at closed history, so the
        # cohorts the seat is rebuilt from silently stop refreshing.
        logger.warning(
            "sos_archive_refresh_noncurrent_biennium",
            extra={"biennium": biennium, "current_biennium": current},
        )

    years = election_years_for_biennium(biennium)
    # Only the even SEATING cohort is a past election that *should* serve, so only its failure
    # is a WARNING; the odd cohort's is INFO until that November is certified (#106 A3).
    summary = await harvest_results(
        session,
        years=years,
        results_client=results_client,
        force=force,
        expected_years={years[0]},
    )
    logger.info(
        "sos_archive_refresh_complete",
        extra={
            "biennium": biennium,
            "election_years": years,
            "cohorts_archived": summary.cohorts_archived,
            "cohorts_absent": summary.cohorts_absent,
            "cohorts_skipped": summary.cohorts_skipped,
        },
    )
    return summary


async def _refresh_job(ctx: JobContext) -> JobResult:
    """Harness handler. ``degraded`` when *every* cohort failed to serve — a whole-source
    outage on the day's archive, which pre-split exited 0 behind a WARNING nothing consumed.

    The test is skipped-vs-total, as in the sweep: ``archive_only`` returns False on a cache
    hit, and an absent cohort (a general with no legislative race) is expected, not an outage.
    """
    summary = await refresh_archive(ctx.require_session())
    if summary.cohorts_skipped > 0 and summary.cohorts_skipped == summary.years:
        return JobResult.degraded(summary)
    return JobResult.ok(summary)


def main(argv: list[str] | None = None) -> int:
    """Refresh the SOS results archive for the current biennium.

    Exit ``0`` clean · ``1`` failed · ``2`` config · ``4``
    (:data:`~clearinghouse_core.job.EXIT_DEGRADED`) every cohort unserved.
    """
    return run_job(
        JOB_SLUG,
        _refresh_job,
        argv=argv,
        prog="python -m usa_wa_adapter_sos.results.archive_refresh",
        description=("Archive the current biennium's SOS results cohorts (#201 Phase A, daily)."),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
