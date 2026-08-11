"""Daily Phase-A archive refresh for the PDC winner cohorts (#201).

    python -m usa_wa_adapter_pdc.archive_refresh

Archives every cohort the **current biennium's** membership can be decided by (#121) — both
House generals (even seating + odd mid-biennium special) and the three Senate cohorts
(staggered evens + the odd special) — through
:func:`~usa_wa_adapter_pdc.harvest.archive_cohorts`, forced past the freshness TTL for daily
determinism, each cohort in its own SAVEPOINT.

**Why it is here and not in the fact (#201).** This half used to run inside
``usa_wa_facts_seats.pdc.refresh``, which made a *fact* import an adapter ``transport`` — one of
the two `import-linter` exceptions #189 had to grant. The adapter owns "refresh my archive"; the
fact owns "rebuild from the archive" (:mod:`usa_wa_facts_seats.pdc.refresh`, still the daily
driver of the ``person_wa_pdc`` identifier links).

**Systemd.** ``usa-wa-pdc-archive-refresh.service``, pulled in and ordered before the rebuild
unit ``usa-wa-pdc-refresh.service`` by a ``Wants=``/``After=`` pair — weak on purpose: a Socrata
outage alerts here and leaves the rebuild to re-derive from the last good archive.

**Flags.** ``--force`` is this half's and is on by default (the daily archive must be the day's
wire); the historical sweep ``python -m usa_wa_adapter_pdc.harvest`` exposes it as an opt-in
flag. ``USA_WA_BIENNIUM`` governs both halves, each resolving it independently. An optional
``USA_WA_PDC_APP_TOKEN`` raises Socrata's rate limit.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_pdc.harvest import ArchiveSummary, archive_cohorts, biennium_resource_ids
from usa_wa_adapter_pdc.transport import PDCClient
from usa_wa_common.elections import election_years_for_biennium, senate_election_years_for_biennium

logger = get_logger(__name__)

#: Stable ledger identity (#178) — distinct from the fact's ``pdc-refresh`` and the sweep's
#: ``pdc-harvest``: ``/api/v1/health/jobs`` is ``DISTINCT ON (job_slug)``, so a shared slug
#: would hide one half's staleness behind the other's last run.
JOB_SLUG = "pdc-archive-refresh"


async def refresh_archive(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    pdc_client: PDCClient | None = None,
    force: bool = True,
) -> ArchiveSummary:
    """Archive the current biennium's winner cohorts. ``pdc_client`` is injectable for tests.
    Operates in the caller's transaction (the harness commits, or rolls back on
    ``--dry-run``)."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    current = biennium_for_date(datetime.now(UTC).date())
    if biennium != current:
        logger.warning(
            "pdc_archive_refresh_noncurrent_biennium",
            extra={"biennium": biennium, "current_biennium": current},
        )

    summary = await archive_cohorts(
        session,
        resource_ids=biennium_resource_ids(biennium),
        biennium=biennium,
        pdc_client=pdc_client,
        force=force,
    )
    logger.info(
        # #121 CR-3: the completion line self-describes the five-cohort cycle, so a
        # cohorts_archived shortfall is triageable from one line.
        "pdc_archive_refresh_complete",
        extra={
            "biennium": biennium,
            "house_years": election_years_for_biennium(biennium),
            "senate_years": senate_election_years_for_biennium(biennium),
            "cohorts_archived": summary.cohorts_archived,
            "cohorts_skipped": summary.cohorts_skipped,
        },
    )
    return summary


async def _refresh_job(ctx: JobContext) -> JobResult:
    """Harness handler. ``degraded`` when *every* cohort failed — a Socrata outage on the day's
    archive, which pre-split exited 0 behind a WARNING nothing consumed. One flaky cohort is a
    healthy run: the others archived and the rebuild has what it needs."""
    summary = await refresh_archive(ctx.require_session())
    if summary.cohorts_skipped > 0 and summary.cohorts_skipped == summary.cohorts:
        return JobResult.degraded(summary)
    return JobResult.ok(summary)


def main(argv: list[str] | None = None) -> int:
    """Refresh the PDC cohort archive for the current biennium.

    Exit ``0`` clean · ``1`` failed · ``2`` config · ``4``
    (:data:`~clearinghouse_core.job.EXIT_DEGRADED`) every cohort unserved.
    """
    return run_job(
        JOB_SLUG,
        _refresh_job,
        argv=argv,
        prog="python -m usa_wa_adapter_pdc.archive_refresh",
        description="Archive the current biennium's PDC winner cohorts (#201 Phase A, daily).",
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
