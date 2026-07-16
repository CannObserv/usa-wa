"""Phase B committee-membership span builder (#82) — archive → merged Assignment spans.

Reads every archived ``committee-members-hist:<biennium>:<id>:…`` roster **offline** (via
:class:`~usa_wa_adapter_legislature.committee_member_cohort.CommitteeMemberCohortProvider`,
no WSL re-pull), projects the rows to membership observations
(:mod:`committee_membership_observations`), merges contiguous biennia into
:class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s, and emits one
:class:`Assignment` per committee tenure with a Citation per (biennium, committee) roster
(:mod:`committee_span_emit`).

Derives entirely from the local archive — re-runnable / re-tunable without touching WSL.
Depends on the Phase A harvest (:mod:`harvest_committee_members`) having archived the
rosters, and on the Persons (#77) + committee Orgs (sub-project 3) existing.

    python -m usa_wa_adapter_legislature.harvest_committee_member_spans [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.committee_member_cohort import CommitteeMemberCohortProvider
from usa_wa_adapter_legislature.committee_membership_observations import (
    KIND_COMMITTEE,
    build_committee_membership_observations,
)
from usa_wa_adapter_legislature.committee_span_emit import emit_committee_spans
from usa_wa_adapter_legislature.provisioning import get_or_create_source, resolve_jurisdiction
from usa_wa_adapter_legislature.span_emit import SOURCE, close_stale_spans
from usa_wa_adapter_legislature.synthesis import biennium_for_date
from usa_wa_adapter_legislature.tenure_spans import build_tenure_spans
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)


async def build_committee_member_spans(
    session: AsyncSession,
    *,
    member_client: WSLClient | None = None,
    current_biennium: str | None = None,
    restrict_to_biennium: str | None = None,
) -> int:
    """Build + emit merged committee-membership Assignment spans from the archive.

    ``current_biennium`` decides which spans stay open (defaults to the date-current one).

    ``restrict_to_biennium`` scopes the rebuild to the **(member, committee) pairs observed
    in that biennium's rosters** — the daily refresh passes the current biennium so it
    re-asserts only today's memberships (each with its *full* cross-biennium history), rather
    than rebuilding every member's whole committee archive every day. ``None`` (the harvest
    path) rebuilds all.

    Either way, memberships the rebuilt set no longer asserts are **closed** (#83,
    :func:`~usa_wa_adapter_legislature.span_emit.close_stale_spans`) — a member who left the
    committee (or the legislature) must not keep an ``is_active`` row forever."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())

    provider = CommitteeMemberCohortProvider(
        member_client or WSLClient("CommitteeService"), session=session, source_id=source.id
    )
    rosters = await provider.archived_rosters()
    if not rosters:
        logger.warning("committee_member_span_build_no_archive")
        return 0

    observations = build_committee_membership_observations(rosters)
    if restrict_to_biennium is not None:
        scoped = {
            (o.member_id, o.discriminator)
            for o in observations
            if o.biennium == restrict_to_biennium
        }
        observations = [o for o in observations if (o.member_id, o.discriminator) in scoped]

    spans = build_tenure_spans(observations, current_biennium=current)
    fetch_events = await provider.fetch_event_map()
    emitted = await emit_committee_spans(
        session, spans, reliability=source.reliability, fetch_events=fetch_events
    )
    closed = await close_stale_spans(
        session,
        assignment_source=SOURCE,
        kinds={KIND_COMMITTEE},
        asserted_source_ids={s.source_id for s in spans},
        current_biennium=current,
    )
    logger.info(
        "committee_member_span_build_complete",
        extra={
            "rosters": len(rosters),
            "spans": len(spans),
            "emitted": emitted,
            "closed_stale": closed,
            "restricted": restrict_to_biennium,
        },
    )
    return emitted


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Build merged committee-membership spans from the roster archive (#82)."
    )
    parser.add_argument("--dry-run", action="store_true", help="build but roll back (preview)")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            emitted = await build_committee_member_spans(session)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("committee_member_span_build_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Committee membership span build: emitted={emitted} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
