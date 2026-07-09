"""Phase A harvester (#77) — sweep GetSponsors rosters, archive wire, materialize Persons.

For each biennium in a range (default: the WSL floor ``1991-92`` → current), fetch
``SponsorService.GetSponsors(biennium)`` through the AdapterRunner under the
``sponsors:<biennium>`` resource id — archiving the pristine SOAP wire (RawPayload,
hashed, #54) and materializing **Persons + `wa_legislature_member_id` identifiers only**.
The sponsor normalizer emits the Person cluster only (#78-2c); party / chamber-seat /
committee tenure are **merged spans** built from the full archive in Phase B (#78), not
per-biennium here.

Runs the runner **`fill_only=True`** (#65): a Person already present (from the daily
refresh or an earlier biennium) is never clobbered — deduped by the stable WSL ``Id``
(#81 confirmed stable across 1991→2025, so a member seen in many biennia collapses to one
Person). Closed biennia are cache hits on re-run, so a re-harvest never re-pulls or
re-stores immutable history.

Same op/resource key as the daily path — historical biennia are just older resource ids.
Pacing is **central**: ``--pause-seconds`` sets the global WSL request limiter (#77), so
every underlying GetSponsors POST drips against WSL rather than the CLI pacing itself.

    python -m usa_wa_adapter_legislature.harvest_sponsors \\
        [--from-biennium 1991-92] [--to-biennium 2025-26] [--pause-seconds 1] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_legislature.adapter import SPONSORS_RESOURCE_PREFIX, WALegislatureAdapter
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.harvest_committee_meetings import bienniums_in_range
from usa_wa_adapter_legislature.probe_member_identity import DEFAULT_HISTORY_FLOOR
from usa_wa_adapter_legislature.provisioning import (
    get_or_create_source,
    resolve_jurisdiction,
)
from usa_wa_adapter_legislature.refresh import (
    biennium_for_date,
)
from usa_wa_adapter_legislature.transport import WSLClient, configure_wsl_rate_limit

logger = get_logger(__name__)

#: Default inter-request pace (seconds) applied to the central WSL limiter for the sweep.
DEFAULT_PAUSE_SECONDS = 1.0


@dataclass(frozen=True)
class HarvestSummary:
    """Outcome of one :func:`harvest_sponsors` run."""

    windows: int
    upserted: int
    dry_run: bool


async def harvest_sponsors(
    session: AsyncSession,
    *,
    bienniums: list[str],
    sponsor_client: WSLClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> HarvestSummary:
    """Archive + fill-only-materialize Persons (identifiers only) for each biennium.

    Operates in the caller's transaction (the CLI commits, or rolls back on ``dry_run``).
    ``force`` bypasses the runner's freshness cache to re-materialize rolled-back Persons
    while the roster stays archived (byte-identical wire dedups to the existing
    RawPayload). Pacing is handled centrally by the WSL limiter, not here."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=bienniums[0], jurisdiction_id=jurisdiction.id
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=jurisdiction.id,
        biennium=bienniums[0],
        sponsor_client=sponsor_client,
        session=session,
    )
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,  # additive; never clobber an existing (PM-curated) Person
    )

    upserted = 0
    for biennium in bienniums:
        upserted += await runner.fetch_and_normalize(
            f"{SPONSORS_RESOURCE_PREFIX}{biennium}", force=force
        )
        logger.info("wsl_sponsor_roster_harvested", extra={"biennium": biennium})

    return HarvestSummary(windows=len(bienniums), upserted=upserted, dry_run=dry_run)


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Harvest historical member rosters (Persons only, #77 Phase A)."
    )
    parser.add_argument(
        "--from-biennium",
        default=DEFAULT_HISTORY_FLOOR,
        help=f"e.g. 1991-92 (default {DEFAULT_HISTORY_FLOOR}, the WSL GetSponsors floor)",
    )
    parser.add_argument("--to-biennium", default=None, help="default: current from date")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help="min interval between WSL requests (sets the central limiter)",
    )
    parser.add_argument("--dry-run", action="store_true", help="harvest but roll back (preview)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-fetch + re-materialize even on a fresh cache hit",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    to_biennium = args.to_biennium or biennium_for_date(datetime.now(UTC).date())
    bienniums = bienniums_in_range(args.from_biennium, to_biennium)
    configure_wsl_rate_limit(args.pause_seconds)  # central pacing for the whole sweep

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            summary = await harvest_sponsors(
                session,
                bienniums=bienniums,
                sponsor_client=WSLClient("SponsorService"),
                dry_run=args.dry_run,
                force=args.force,
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("wsl_sponsor_harvest_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Sponsor roster harvest: windows={summary.windows} upserted={summary.upserted} "
        f"{'(dry-run, rolled back)' if summary.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
