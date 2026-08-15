"""Phase A roster harvest (#225) — archive one roster edition.

Archives the pristine PDF through :meth:`~clearinghouse_core.runner.AdapterRunner.archive_only`
(wire + #54 hash, no normalize). Phase B (:mod:`usa_wa_adapter_legislature.roster_pdf.cohort`)
parses it offline.

**One cohort, not a sweep.** Every sibling harvest walks a year range because its source
publishes per-year cohorts; this source publishes *one document per revision*, so the harvest
archives exactly one resource. Re-running is a cache hit — the whole point, given a 5.7MB body
and a document that changes about twice a decade.

**Not a timer.** This never joins the daily refresh: closed history does not drift, and the
edition lags the current biennium by design. Run it quarterly, or after a revision lands.

    python -m usa_wa_adapter_legislature.roster_pdf.harvest --revision 2025-06-05 [--force]

A rotated media key with no discoverable href is **degraded**, not a crash: the transport already
tried to re-discover, so the remaining condition needs an operator to re-point the source, and
the run should say so through its exit code rather than through a traceback.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_legislature.roster_pdf.adapter import RosterPdfAdapter, roster_resource_id
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source
from usa_wa_adapter_legislature.roster_pdf.transport import RosterUnavailable
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "roster-pdf-harvest"

#: The revision shipped with this source. Override when a newer edition is published.
DEFAULT_REVISION = "2025-06-05"


@dataclass(frozen=True)
class RosterHarvestSummary:
    """What one harvest did. ``archived`` is 0 on a cache hit *and* on an unavailable source —
    ``unavailable`` is what separates "nothing to do" from "we could not find the document"."""

    revision: str
    archived: int
    unavailable: bool = False


async def harvest_roster(
    session: AsyncSession,
    *,
    revision: str = DEFAULT_REVISION,
    dry_run: bool = False,
    force: bool = False,
) -> RosterHarvestSummary:
    """Archive one roster edition. Idempotent: a second run is a cache hit."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_roster_source(session, jurisdiction)
    adapter = RosterPdfAdapter(revision=revision)
    runner = AdapterRunner(adapter, session, source=source, jurisdiction=jurisdiction)
    resource_id = roster_resource_id(revision)
    try:
        fetched = await runner.archive_only(resource_id, force=force)
    except RosterUnavailable:
        logger.warning("roster_harvest_unavailable", extra={"revision": revision})
        return RosterHarvestSummary(revision=revision, archived=0, unavailable=True)
    if dry_run:
        await session.rollback()
    logger.info(
        "roster_harvest_complete",
        extra={"revision": revision, "archived": int(fetched), "dry_run": dry_run},
    )
    return RosterHarvestSummary(revision=revision, archived=int(fetched))


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--revision",
        default=DEFAULT_REVISION,
        help="Roster revision date (YYYY-MM-DD) — the document's own 'Revision Date'.",
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch past the freshness cache.")


async def _harvest_job(ctx: JobContext) -> JobResult:
    """Archive the roster edition; a source we cannot locate is ``degraded``."""
    summary = await harvest_roster(
        ctx.require_session(),
        revision=ctx.args.revision,
        dry_run=ctx.dry_run,
        force=ctx.args.force,
    )
    if summary.unavailable:
        return JobResult.degraded(summary)
    return JobResult.ok(summary)


def main(argv: list[str] | None = None) -> int:
    """Archive the roster PDF.

    Exit ``0`` clean · ``1`` failed · ``2`` config · ``4``
    (:data:`~clearinghouse_core.job.EXIT_DEGRADED`) the document could not be located.
    """
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.roster_pdf.harvest",
        description="Archive the WA Legislature roster PDF (archive-only, #225 Phase A).",
        extra_args=_add_args,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
