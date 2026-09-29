"""Roster-PDF raw-tier harvest (#421): one roster edition into the file store.

    python -m usa_wa_adapter_legislature.roster_pdf.raw_harvest --revision 2025-06-05 \\
        [--force] [--root PATH] [--pause-seconds S]

The file-store sibling of the three nightly ``raw_harvest`` modules, and the roster's only
Phase A once #412 PR F deletes :mod:`usa_wa_adapter_legislature.roster_pdf.harvest` (the
Postgres ``archive_only`` path). The #302 roster staging model parses the newest
``legroster:<revision>`` in ``raw/usa_wa_legislature_roster/`` by ``fetched_at``, so archiving
an edition here is what puts it in the published datasets. No Postgres provenance is written.

**On demand, not nightly.** The source publishes one cumulative document per revision, about
twice a decade, at 5.7MB. A fresh ``latest.json`` entry (:data:`FRESHNESS_TTL_DAYS`) makes a
re-run fetch nothing; ``--force`` fetches past it.

**The monthly edition re-check (#237)** is this module run ``--dry-run --force`` by
``usa-wa-roster-pdf-recheck.timer``: fetch, verify the stamp against
:data:`~usa_wa_adapter_legislature.roster_pdf.edition.DEFAULT_REVISION`, write nothing. A new
edition exits ``4`` and alerts through ``OnFailure=``, monthly until the edition is archived and
the default is bumped.

**Why not** :func:`~clearinghouse_core.rawstore.record_fetch`: it records every fetch exception
as a generic ``err`` entry, which would flatten the two operator conditions (a new edition, an
unlocatable document) into one, and it cannot skip the write for a dry run. With one resource
per run the loop body it shares is three lines. An outage (a non-404 status, a timeout) raises
and writes nothing: exit ``1``, and the next run retries.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.edition import (
    DEFAULT_REVISION,
    RosterRevisionMismatch,
    verify_edition,
)
from usa_wa_adapter_legislature.roster_pdf.resources import roster_resource_id
from usa_wa_adapter_legislature.roster_pdf.transport import (
    DEFAULT_LEG_MIN_REQUEST_INTERVAL,
    RosterPdfClient,
    RosterUnavailable,
    configure_leg_rate_limit,
)

logger = get_logger(__name__)

#: Stable ledger identity (#178); distinct from ``roster-pdf-harvest`` (Postgres tier).
JOB_SLUG = "roster-pdf-raw-harvest"

#: The Postgres source's ``cache_ttl_days``, carried over: a re-run inside it fetches nothing.
FRESHNESS_TTL_DAYS = 90


@dataclass(frozen=True)
class RosterRawHarvestSummary:
    """What one harvest did, in the sibling raw harvests' counter vocabulary.

    ``fetched`` counts a fetch that verified, ``unchanged`` one whose bytes the store already
    held, ``skipped_fresh`` a run the freshness window answered. ``unavailable`` and
    ``mismatch`` separate "we could not find the document" and "we found a *different*
    edition" from "nothing to do".
    """

    revision: str
    fetched: int = 0
    unchanged: int = 0
    skipped_fresh: int = 0
    unavailable: bool = False
    #: Set when the fetched document stamps a different edition than ``revision`` — a new
    #: edition is published and the operator must re-run with it.
    mismatch: str | None = None
    #: Nothing was written, so ``fetched`` counts a verification, not an archive. The ledger
    #: records this summary as the run's counters and ``/health/jobs`` serves the latest one
    #: per job, so the monthly re-check must not read there as an archive.
    dry_run: bool = False


async def harvest_roster_raw(
    root: Path | str,
    *,
    revision: str = DEFAULT_REVISION,
    dry_run: bool = False,
    force: bool = False,
    client: RosterPdfClient | None = None,
) -> RosterRawHarvestSummary:
    """Archive one roster edition into the raw store. Idempotent: the store dedups by hash."""
    store = RawStore(root, ROSTER_SOURCE_SLUG)
    resource_id = roster_resource_id(revision)
    if not force and store.is_fresh(resource_id, ttl_days=FRESHNESS_TTL_DAYS):
        return RosterRawHarvestSummary(revision=revision, skipped_fresh=1, dry_run=dry_run)
    try:
        fetched = await (client or RosterPdfClient()).fetch_roster()
    except RosterUnavailable:
        logger.warning("roster_raw_harvest_unavailable", extra={"revision": revision})
        return RosterRawHarvestSummary(revision=revision, unavailable=True, dry_run=dry_run)
    try:
        verify_edition(fetched.wire, revision, url=fetched.url)
    except RosterRevisionMismatch as exc:
        logger.warning(
            "roster_raw_harvest_revision_mismatch",
            extra={"revision": revision, "detail": str(exc)},
        )
        return RosterRawHarvestSummary(revision=revision, mismatch=str(exc), dry_run=dry_run)
    unchanged = 0
    if not dry_run:
        run = store.open_run()
        try:
            recorded = run.record(
                resource_id, fetched.wire, url=fetched.url, content_type=fetched.content_type
            )
        finally:
            run.close()
        unchanged = int(not recorded.newly_stored)
    summary = RosterRawHarvestSummary(
        revision=revision, fetched=1, unchanged=unchanged, dry_run=dry_run
    )
    logger.info(
        "roster_raw_harvest_complete",
        extra={
            "revision": revision,
            "unchanged": unchanged,
            "dry_run": dry_run,
            # Absolute: the default root is cwd-relative, and an edition that lands in a
            # worktree's raw/ never reaches the pipeline — the one place that shows it (CR 1).
            "store": str(store.source_dir.resolve()),
        },
    )
    return summary


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--revision",
        default=DEFAULT_REVISION,
        help="Roster revision date (YYYY-MM-DD) — the document's own 'Revision Date'.",
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch past the freshness window.")
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=None,
        help=(
            "min interval between leg.wa.gov requests (sets the host limiter); unset leaves the "
            "value seeded from USA_WA_LEG_MIN_REQUEST_INTERVAL "
            f"(default {DEFAULT_LEG_MIN_REQUEST_INTERVAL}) in place"
        ),
    )


async def _harvest_job(ctx: JobContext) -> JobResult:
    """Archive the edition; a source we cannot locate — or a newer edition than the one
    requested — is ``degraded``, since both need an operator rather than a retry."""
    # Only when the operator asked (#169): an unconditional call would let the flag's default
    # silently overwrite the env-seeded interval.
    if ctx.args.pause_seconds is not None:
        configure_leg_rate_limit(ctx.args.pause_seconds)
    summary = await harvest_roster_raw(
        Path(ctx.args.root) if ctx.args.root else get_raw_root(),
        revision=ctx.args.revision,
        dry_run=ctx.dry_run,
        force=ctx.args.force,
    )
    if summary.unavailable or summary.mismatch:
        return JobResult.degraded(summary)
    return JobResult.ok(summary)


def main(argv: list[str] | None = None) -> int:
    """Archive the roster PDF into the raw store.

    Exit ``0`` clean · ``1`` failed · ``2`` config · ``4``
    (:data:`~clearinghouse_core.job.EXIT_DEGRADED`) the document could not be located, **or** a
    new edition is published and ``--revision`` names the old one.
    """
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.roster_pdf.raw_harvest",
        description="Archive the WA Legislature roster PDF into the raw file store (#421).",
        extra_args=_add_args,
        dry_run_help="fetch and verify the edition's stamp, write nothing (the #237 re-check)",
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
