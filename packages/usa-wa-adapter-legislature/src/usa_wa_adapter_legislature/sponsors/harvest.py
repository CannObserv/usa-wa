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

    python -m usa_wa_adapter_legislature.sponsors.harvest \\
        [--from-biennium 1991-92] [--to-biennium 2025-26] [--pause-seconds 1] [--dry-run]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_domain_legislative.terms import biennium_for_date, bienniums_in_range
from usa_wa_adapter_legislature.adapter import SPONSORS_RESOURCE_PREFIX, WALegislatureAdapter
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.provisioning import get_or_create_source
from usa_wa_adapter_legislature.sponsors.probe_identity import DEFAULT_HISTORY_FLOOR
from usa_wa_adapter_legislature.transport import WSLClient, configure_wsl_rate_limit
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Default inter-request pace (seconds) applied to the central WSL limiter for the sweep.
DEFAULT_PAUSE_SECONDS = 1.0

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "wsl-sponsor-harvest"


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


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the sweep's own flags to the harness's shared parser."""
    parser.add_argument(
        "--from-biennium",
        default=DEFAULT_HISTORY_FLOOR,
        help=f"e.g. 1991-92 (default {DEFAULT_HISTORY_FLOOR}, the WSL GetSponsors floor)",
    )
    parser.add_argument("--to-biennium", default=None, help="default: current from date")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=None,
        help=(
            "min interval between WSL requests (sets the central limiter); unset leaves the "
            f"value seeded from USA_WA_WSL_MIN_REQUEST_INTERVAL (default {DEFAULT_PAUSE_SECONDS}) "
            "in place"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-fetch + re-materialize even on a fresh cache hit",
    )


async def _harvest_job(ctx: JobContext) -> HarvestSummary:
    """Harness handler: resolve the range, pace the limiter, sweep.

    Returns the summary unchanged — the harness reads any dataclass as ``ok`` with those
    counters, so the ledger row carries ``windows``/``upserted``/``dry_run`` verbatim.
    """
    args = ctx.args
    to_biennium = args.to_biennium or biennium_for_date(datetime.now(UTC).date())
    bienniums = bienniums_in_range(args.from_biennium, to_biennium)
    # Central pacing for the whole sweep — but only when the operator asked (#169). An
    # unconditional call let the flag's own default silently overwrite the env-seeded interval.
    if args.pause_seconds is not None:
        configure_wsl_rate_limit(args.pause_seconds)
    return await harvest_sponsors(
        ctx.require_session(),
        bienniums=bienniums,
        sponsor_client=WSLClient("SponsorService"),
        dry_run=ctx.dry_run,
        force=args.force,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the roster sweep. Exit ``0`` clean · ``1`` failed · ``2`` config (#179b)."""
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.sponsors.harvest",
        description="Harvest historical member rosters (Persons only, #77 Phase A).",
        extra_args=_add_args,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
