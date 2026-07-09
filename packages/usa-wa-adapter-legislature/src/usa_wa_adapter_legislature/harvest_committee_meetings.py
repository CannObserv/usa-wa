"""Backfill harvester (#39) — sweep the meeting docket, archive wire, freeze the seed.

A one-shot CLI that, for each biennium in a configurable range, fetches the
``CommitteeMeetingService.GetCommitteeMeetings`` window through the AdapterRunner —
archiving the **pristine SOAP wire** (``RawPayload``, hashed, archival retention, #54)
and upserting the Joint/`Other` ``org_type='other'`` rows — then **freezes the deduped
durable cohort** to the checked-in seed (`committee_seed`) with `seed_manifest` sidecars.

This is *not* the daily loop: closed windows are immutable, so the runner's cache-or-fetch
fetches each once and a re-run is a free cache hit (frugality — WSL is a vital upstream).
The daily refresh handles only the current window (see `refresh.py`); this handles history.

    python -m usa_wa_adapter_legislature.harvest_committee_meetings \\
        --from-biennium 2023-24 --to-biennium 2025-26 [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from ulid import ULID as _ULID

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Citation, FetchEvent
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_core.seed_manifest import write_sidecars
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.adapter import WALegislatureAdapter
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.committee_seed import (
    DEFAULT_SEED_PATH,
    SeedCommittee,
    serialize_seed,
)
from usa_wa_adapter_legislature.meeting_windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.provisioning import (
    get_or_create_source,
    resolve_jurisdiction,
)
from usa_wa_adapter_legislature.synthesis import _biennium_start_year
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
_OTHER = "other"


@dataclass(frozen=True)
class HarvestSummary:
    """Outcome of one :func:`harvest_committee_meetings` run."""

    windows: int
    upserted: int
    committees: int
    seed_path: Path
    dry_run: bool


def bienniums_in_range(from_biennium: str, to_biennium: str) -> list[str]:
    """Inclusive list of biennium labels from ``from_biennium`` to ``to_biennium``.

    Bienniums start on odd years; the range walks by 2. ``from`` must not be after
    ``to``."""
    start = _biennium_start_year(from_biennium)
    end = _biennium_start_year(to_biennium)
    if start > end:
        raise ValueError(f"from-biennium {from_biennium!r} is after to-biennium {to_biennium!r}")
    return [f"{y}-{(y + 1) % 100:02d}" for y in range(start, end + 1, 2)]


async def _other_class_cohort(
    session: AsyncSession, source_id: _ULID, window_resource_ids: list[str]
) -> list[Organization]:
    """The org_type='other' cohort discovered **in this run's windows** — the seed content.

    Scoped via the citations linking each org to the FetchEvents of exactly these window
    resource ids, so the frozen seed is a deterministic function of the swept windows'
    WSL data rather than whatever else happens to sit in the DB (daily-refresh rows,
    earlier harvests, prior ingests). Reproducible across DBs given the same upstream."""
    fetch_event_ids = select(FetchEvent.id).where(
        FetchEvent.source_id == source_id,
        FetchEvent.resource_id.in_(window_resource_ids),
    )
    cited_org_ids = select(Citation.entity_id).where(
        Citation.entity_type == "organization",
        Citation.fetch_event_id.in_(fetch_event_ids),
    )
    result = await session.execute(
        select(Organization)
        .where(
            Organization.source == _SOURCE,
            Organization.org_type == _OTHER,
            Organization.id.in_(cited_org_ids),
        )
        .order_by(Organization.source_id)
    )
    return list(result.scalars().all())


async def harvest_committee_meetings(
    session: AsyncSession,
    *,
    bienniums: list[str],
    seed_path: Path = DEFAULT_SEED_PATH,
    meeting_client: WSLClient | None = None,
    dry_run: bool = False,
) -> HarvestSummary:
    """Archive + upsert each biennium window, then freeze the deduped cohort to the seed."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    # The legislature/chamber anchors are biennium-independent; bootstrap once (any
    # biennium in range) to resolve the parent the meeting normalizer needs.
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=bienniums[0], jurisdiction_id=jurisdiction.id
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=jurisdiction.id,
        biennium=bienniums[0],
        meeting_client=meeting_client,
    )
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
    )

    upserted = 0
    window_resource_ids: list[str] = []
    for biennium in bienniums:
        resource_id = meetings_resource_id(*biennium_window(biennium))
        window_resource_ids.append(resource_id)
        # force=False: a closed window already archived is a free cache hit — never
        # re-pull immutable history.
        upserted += await runner.fetch_and_normalize(resource_id)
        logger.info("wsl_meeting_window_harvested", extra={"biennium": biennium})

    cohort = await _other_class_cohort(session, source.id, window_resource_ids)
    committees = [
        SeedCommittee(
            source_id=o.source_id,
            name=o.name,
            short_name=o.short_name,
            acronym=o.acronym,
            phone=o.phone,
        )
        for o in cohort
    ]
    content = serialize_seed(committees, bienniums=bienniums)
    if not dry_run:
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_bytes(content)
        write_sidecars(
            seed_path,
            content,
            extra={"bienniums": bienniums, "committee_count": len(committees)},
        )
    logger.info(
        "wsl_committee_seed_frozen",
        extra={"committees": len(committees), "dry_run": dry_run, "seed_path": str(seed_path)},
    )
    return HarvestSummary(
        windows=len(bienniums),
        upserted=upserted,
        committees=len(committees),
        seed_path=seed_path,
        dry_run=dry_run,
    )


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Harvest the Joint/Other committee seed (#39).")
    parser.add_argument("--from-biennium", required=True, help="e.g. 2023-24")
    parser.add_argument("--to-biennium", required=True, help="e.g. 2025-26")
    parser.add_argument("--seed-path", type=Path, default=DEFAULT_SEED_PATH)
    parser.add_argument("--dry-run", action="store_true", help="harvest but do not write the seed")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2
    try:
        bienniums = bienniums_in_range(args.from_biennium, args.to_biennium)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session, session.begin():
            summary = await harvest_committee_meetings(
                session,
                bienniums=bienniums,
                seed_path=args.seed_path,
                dry_run=args.dry_run,
            )
    except Exception:
        logger.exception("wsl_committee_harvest_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Committee harvest: windows={summary.windows} upserted={summary.upserted} "
        f"committees={summary.committees} "
        f"seed={'(dry-run, not written)' if summary.dry_run else summary.seed_path}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
