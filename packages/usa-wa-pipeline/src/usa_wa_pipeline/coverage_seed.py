"""Reconcile every source's declared coverage claims into ``source_coverage`` (#412 PR E).

    python -m usa_wa_pipeline.coverage_seed [--dry-run]

``/sources/{slug}/coverage`` serves the claims each adapter declares in its ``coverage``
module (#180, coverage-as-data). They reach the table through the adapter's
``provisioning``, whose ``get_or_create_*source`` reconciles them on every call. The daily
canonical refreshes made that call until #412 PR E disabled them. The table outlived
the canonical tier, so the nightly makes the call instead: a re-audited claim merged to
main reaches the API by the next morning.

Idempotent: a run whose claims already match writes nothing. Exit ``0`` reconciled ·
``1`` a source failed to reconcile (the harness's failure path).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.provisioning import get_or_create_source as wsl_source
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source
from usa_wa_adapter_pdc.provisioning import get_or_create_source as pdc_source
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_adapter_sos.provisioning import get_or_create_source as sos_filings_source
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "coverage-seed"

#: Every source that declares coverage claims. The operator source declares none.
PROVISIONERS = (
    wsl_source,
    get_or_create_roster_source,
    pdc_source,
    sos_filings_source,
    get_or_create_results_source,
)


async def reconcile_coverage(session: AsyncSession) -> dict[str, Any]:
    """Get-or-create every declaring source, reconciling its claims, in the caller's
    transaction."""
    jurisdiction = await resolve_jurisdiction(session)
    slugs = [(await provision(session, jurisdiction)).slug for provision in PROVISIONERS]
    return {"sources": len(slugs), "slugs": slugs}


async def _seed_job(ctx: JobContext) -> JobResult:
    counters = await reconcile_coverage(ctx.require_session())
    logger.info("coverage_seed_reconciled", extra=counters)
    return JobResult.ok(counters)


def main(argv: list[str] | None = None) -> int:
    """Reconcile every declared coverage claim. Exit ``0`` done · ``1`` failed."""
    return run_job(
        JOB_SLUG,
        _seed_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.coverage_seed",
        description="Reconcile each source's declared coverage claims into source_coverage.",
        dry_run_help="reconcile, then roll back instead of committing",
    )


if __name__ == "__main__":
    raise SystemExit(main())
