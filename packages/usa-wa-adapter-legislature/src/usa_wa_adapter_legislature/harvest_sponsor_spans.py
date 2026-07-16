"""Phase B span builder CLI (#78 increment 2b-ii) — archive → merged-span Assignments.

Reads every archived ``sponsors:<biennium>`` roster **offline** (via
:class:`~usa_wa_adapter_legislature.sponsor_cohort.SponsorRosterCohortProvider`, no WSL
re-pull), projects the rows to tenure observations (:mod:`sponsor_observations`), builds
merged :class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s, and emits one
:class:`Assignment` per tenure with per-biennium citations (:mod:`sponsor_span_emit`).

Derives entirely from the local archive — re-runnable / re-tunable without touching WSL.
Depends on the #77 harvest having archived the rosters first. ``--dry-run`` rolls back.

    python -m usa_wa_adapter_legislature.harvest_sponsor_spans [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.provisioning import get_or_create_source, resolve_jurisdiction
from usa_wa_adapter_legislature.span_emit import (
    MAX_CLOSE_FRACTION_DEFAULT,
    SOURCE,
    SpanBuildResult,
    close_fraction,
    close_stale_spans,
)
from usa_wa_adapter_legislature.sponsor_cohort import SponsorRosterCohortProvider
from usa_wa_adapter_legislature.sponsor_observations import (
    KIND_PARTY,
    KIND_SENATE,
    build_sponsor_observations,
)
from usa_wa_adapter_legislature.sponsor_span_emit import emit_sponsor_spans
from usa_wa_adapter_legislature.synthesis import biennium_for_date
from usa_wa_adapter_legislature.tenure_spans import build_tenure_spans
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)


async def build_sponsor_spans(
    session: AsyncSession,
    *,
    sponsor_client: WSLClient | None = None,
    current_biennium: str | None = None,
    restrict_to_biennium: str | None = None,
    max_close_fraction: float = MAX_CLOSE_FRACTION_DEFAULT,
) -> SpanBuildResult:
    """Build + emit merged-span Assignments from the local sponsor archive; return the result.

    Archive-derived: the provider re-parses each ``sponsors:<biennium>`` offline (the
    ``sponsor_client`` is only a fallback for an un-archived biennium). ``current_biennium``
    determines which spans stay open (defaults to the date-current biennium).

    ``restrict_to_biennium`` scopes the rebuild to **members observed in that biennium's
    roster** — the daily refresh passes the current biennium so it re-asserts only that day's
    cohort (their full span history, not just the current run), rather than rebuilding every
    member's whole archive every day (#78-2c). Each scoped member keeps their *full*
    cross-biennium span history; only members absent from that biennium are skipped. ``None``
    (the harvest / migration path) rebuilds all members.

    Either way, spans the rebuilt set no longer asserts are **closed** (#83,
    :func:`~usa_wa_adapter_legislature.span_emit.close_stale_spans`) — a departed member's
    open row must not stay ``is_active`` forever."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=current, jurisdiction_id=jurisdiction.id
    )
    provider = SponsorRosterCohortProvider(
        sponsor_client or WSLClient("SponsorService"), session=session, source_id=source.id
    )
    bienniums = await provider.archived_bienniums()
    if not bienniums:
        logger.warning("sponsor_span_build_no_archive")
        return SpanBuildResult(emitted=0)
    roster = await provider.roster_map(bienniums)
    observations = build_sponsor_observations(roster)
    if restrict_to_biennium is not None:
        scoped = {o.member_id for o in observations if o.biennium == restrict_to_biennium}
        observations = [o for o in observations if o.member_id in scoped]
    spans = build_tenure_spans(observations, current_biennium=current)
    fetch_events = await provider.fetch_event_map(bienniums)
    emitted = await emit_sponsor_spans(
        session, spans, anchors=anchors, reliability=source.reliability, fetch_events=fetch_events
    )
    sweep = await close_stale_spans(
        session,
        assignment_source=SOURCE,
        kinds={KIND_PARTY, KIND_SENATE},
        asserted_source_ids={s.source_id for s in spans},
        current_biennium=current,
        max_close_fraction=max_close_fraction,
    )
    logger.info(
        "sponsor_span_build_complete",
        extra={
            "bienniums": len(bienniums),
            "spans": len(spans),
            "emitted": emitted,
            "closed_stale": sweep.closed,
            "sweep_aborted": sweep.aborted,
            "restricted": restrict_to_biennium,
        },
    )
    return SpanBuildResult(emitted=emitted, closed_stale=sweep.closed, sweep_aborted=sweep.aborted)


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Build merged-span member Assignments from the sponsor archive (#78 Phase B)."
    )
    parser.add_argument("--dry-run", action="store_true", help="build but roll back (preview)")
    parser.add_argument(
        "--max-close-fraction",
        type=close_fraction,
        default=MAX_CLOSE_FRACTION_DEFAULT,
        help="mass-close guard ceiling in (0, 1] (#83); 1.0 disables the guard for a "
        "deliberate mass close (e.g. a wholesale WSL committee-Id re-key)",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await build_sponsor_spans(session, max_close_fraction=args.max_close_fraction)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("sponsor_span_build_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Sponsor span build: emitted={result.emitted} closed_stale={result.closed_stale} "
        f"sweep_aborted={result.sweep_aborted} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
