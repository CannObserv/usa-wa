"""Backfill harvester (#39) — sweep the meeting docket, archive wire, freeze the seed.

A one-shot CLI that, for each biennium in a configurable range, fetches the
``CommitteeMeetingService.GetCommitteeMeetings`` window through the AdapterRunner —
archiving the **pristine SOAP wire** (``RawPayload``, hashed, archival retention, #54)
and upserting the Joint/`Other` ``org_type='other'`` rows — then **freezes the deduped
durable cohort** to the checked-in seed (`committees.seed`) with `seed_manifest` sidecars.

This is *not* the daily loop: closed windows are immutable, so the runner's cache-or-fetch
fetches each once and a re-run is a free cache hit (frugality — WSL is a vital upstream).
The daily refresh handles only the current window (see `refresh.py`); this handles history.

    python -m usa_wa_adapter_legislature.meetings.harvest \\
        --from-biennium 2023-24 --to-biennium 2025-26 [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.job import EXIT_CONFIG, JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import Citation, FetchEvent
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_core.seed_manifest import write_sidecars
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.terms import bienniums_in_range
from usa_wa_adapter_legislature.adapter import WALegislatureAdapter
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.committees.seed import (
    DEFAULT_SEED_PATH,
    SeedCommittee,
    serialize_seed,
)
from usa_wa_adapter_legislature.meetings.windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.provisioning import get_or_create_source
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "wsl-committee-meeting-harvest"

_SOURCE = "usa_wa_legislature"
_OTHER = "other"


@dataclass(frozen=True)
class HarvestSummary:
    """Outcome of one :func:`harvest` run."""

    windows: int
    upserted: int
    committees: int
    seed_path: Path
    dry_run: bool


def _write_seed(seed_path: Path, content: bytes, extra: dict) -> None:
    """Create the seed's directory, write its bytes, write its sidecars.

    Synchronous on purpose: :func:`_freeze_seed` hands it to a worker thread. Grouping
    the three steps means one hop rather than three, and it sweeps in the ``mkdir`` and
    the sidecar writes — blocking too, but invisible to ruff's ``ASYNC`` rules.
    """
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_bytes(content)
    write_sidecars(seed_path, content, extra=extra)


async def _freeze_seed(seed_path: Path, content: bytes, extra: dict) -> None:
    """Freeze the seed without blocking the event loop (#196).

    The ``--dry-run`` guard stays in the caller: this is reached only on a run that is
    meant to write, so the flag's narrow meaning ("harvest but do not write the seed")
    is unchanged.
    """
    await asyncio.to_thread(_write_seed, seed_path, content, extra)


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


async def harvest(
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
        await _freeze_seed(
            seed_path,
            content,
            {"bienniums": bienniums, "committee_count": len(committees)},
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


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the sweep's own flags to the harness's shared parser."""
    parser.add_argument("--from-biennium", required=True, help="e.g. 2023-24")
    parser.add_argument("--to-biennium", required=True, help="e.g. 2025-26")
    parser.add_argument("--seed-path", type=Path, default=DEFAULT_SEED_PATH)


async def _harvest_job(ctx: JobContext) -> HarvestSummary | JobResult:
    """Harness handler, owning its own transaction (``commit=False``).

    **``--dry-run`` here means "do not write the seed file", not "roll back".** The
    archive writes always committed, through the explicit ``session.begin()`` kept
    below; letting the harness roll them back on ``--dry-run`` would silently start
    discarding archived wire behind an unchanged exit code.
    """
    try:
        bienniums = bienniums_in_range(ctx.args.from_biennium, ctx.args.to_biennium)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return JobResult.failed({"error": str(exc)}, exit_code=EXIT_CONFIG)
    session = ctx.require_session()
    async with session.begin():
        return await harvest(
            session,
            bienniums=bienniums,
            seed_path=ctx.args.seed_path,
            dry_run=ctx.dry_run,
        )


def main(argv: list[str] | None = None) -> int:
    """Harvest the Joint/Other seed. Exit ``0`` clean · ``1`` failed · ``2`` config/range."""
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.meetings.harvest",
        description="Harvest the Joint/Other committee seed (#39).",
        extra_args=_add_args,
        commit=False,
        # The one job whose --dry-run is real but NARROWER than a rollback (CR #196
        # finding 56). It declared this exact string itself before #179b; the sweep took
        # its parser away and left it advertising the generic "roll back instead of
        # committing", which is false here — the archive writes commit either way.
        dry_run_help="harvest but do not write the seed",
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
