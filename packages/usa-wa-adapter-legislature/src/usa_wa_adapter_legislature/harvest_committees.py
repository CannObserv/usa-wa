"""Phase A harvester (sub-project 3) — sweep GetCommittees rosters, archive wire,
materialize standing committees by stable ``Id``.

For each biennium in a range, fetch ``CommitteeService.GetCommittees(biennium)`` (the
full historical roster) through the AdapterRunner under a new resource id
``committees-roster:<biennium>`` — archiving the pristine SOAP wire (RawPayload,
hashed, #54) and inserting ``org_type='committee'`` rows keyed by stable ``Id``. Runs
the runner **`fill_only=True`** (#65): a committee already present (from the daily
refresh or an earlier biennium) is never clobbered — its PM-curated ``name``/``acronym``
stand; only genuinely new historical committees are inserted. Closed windows are cache
hits on re-run, so a re-harvest never re-pulls or re-stores immutable history.

Distinct from the meeting harvest (#39, Joint/Other) and the daily refresh
(GetActiveCommittees under ``committees:<biennium>``). **No seed is frozen** — deferred
(the RawPayload archive is the durable record). An inter-request ``pause`` drips the
sweep against WSL, a vital upstream.

    python -m usa_wa_adapter_legislature.harvest_committees \\
        --from-biennium 2011-12 --to-biennium 2025-26 [--pause-seconds 2] [--dry-run]

Omit ``--from-biennium`` to auto-probe the earliest biennium GetCommittees returns
(a cheap committee-only walk — no meeting pulls).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_legislature.adapter import (
    COMMITTEES_ROSTER_RESOURCE_PREFIX,
    WALegislatureAdapter,
)
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.harvest_committee_meetings import bienniums_in_range
from usa_wa_adapter_legislature.probe_committee_extent import probe_committee_floor
from usa_wa_adapter_legislature.refresh import (
    _get_or_create_source,
    _resolve_jurisdiction,
    biennium_for_date,
)
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)

#: Default inter-request pause (seconds) between window fetches — drips the sweep.
DEFAULT_PAUSE_SECONDS = 2.0


@dataclass(frozen=True)
class HarvestSummary:
    """Outcome of one :func:`harvest_committees` run."""

    windows: int
    upserted: int
    dry_run: bool


async def harvest_committees(
    session: AsyncSession,
    *,
    bienniums: list[str],
    committee_client: WSLClient | None = None,
    pause_seconds: float = 0.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    dry_run: bool = False,
) -> HarvestSummary:
    """Archive + fill-only-materialize each biennium's committee roster.

    Operates in the caller's transaction (the CLI commits, or rolls back on
    ``dry_run``). ``sleep`` is injectable so tests don't wait. Pauses **between**
    windows only (not after the last)."""
    jurisdiction = await _resolve_jurisdiction(session)
    source = await _get_or_create_source(session, jurisdiction)
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=bienniums[0], jurisdiction_id=jurisdiction.id
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=jurisdiction.id,
        biennium=bienniums[0],
        client=committee_client,
    )
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        # Additive discovery (#65): insert new historical committees, never clobber a
        # PM-curated existing row. Same stance as the daily refresh.
        fill_only=True,
    )

    upserted = 0
    for i, biennium in enumerate(bienniums):
        resource_id = f"{COMMITTEES_ROSTER_RESOURCE_PREFIX}{biennium}"
        # force=False: a closed roster already archived is a free cache hit.
        upserted += await runner.fetch_and_normalize(resource_id)
        logger.info("wsl_committee_roster_harvested", extra={"biennium": biennium})
        if pause_seconds and i < len(bienniums) - 1:
            await sleep(pause_seconds)

    return HarvestSummary(windows=len(bienniums), upserted=upserted, dry_run=dry_run)


async def _resolve_bienniums(
    from_biennium: str | None, to_biennium: str, committee_client: WSLClient
) -> list[str]:
    """Explicit ``--from`` wins; else probe the committee floor (GetCommittees-only)."""
    if from_biennium is not None:
        return bienniums_in_range(from_biennium, to_biennium)
    floor = await probe_committee_floor(committee_client, start_biennium=to_biennium)
    earliest = floor["earliest_with_data"]
    if earliest is None:
        raise ValueError("committee floor probe found no data")
    logger.info("wsl_committee_floor_probed", extra={"earliest": earliest})
    return bienniums_in_range(earliest, to_biennium)


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Harvest historical committee rosters (sub-project 3, Phase A)."
    )
    parser.add_argument(
        "--from-biennium", default=None, help="e.g. 2011-12 (else auto-probe the floor)"
    )
    parser.add_argument("--to-biennium", default=None, help="default: current from date")
    parser.add_argument("--pause-seconds", type=float, default=DEFAULT_PAUSE_SECONDS)
    parser.add_argument("--dry-run", action="store_true", help="harvest but roll back (preview)")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    to_biennium = args.to_biennium or biennium_for_date(datetime.now(UTC).date())
    committee_client = WSLClient("CommitteeService")
    try:
        bienniums = await _resolve_bienniums(args.from_biennium, to_biennium, committee_client)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            summary = await harvest_committees(
                session,
                bienniums=bienniums,
                committee_client=committee_client,
                pause_seconds=args.pause_seconds,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("wsl_committee_harvest_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Committee roster harvest: windows={summary.windows} upserted={summary.upserted} "
        f"{'(dry-run, rolled back)' if summary.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
