"""WA SOS refresh — ``python -m usa_wa_adapter_sos.house.refresh`` (#101).

The daily driver of the **WSL+SOS House Position seat** (symmetric with the Senate seat, #75).
It:

1. Archives the current election's results cohort (``sos-legresults:<YYYYMMDD>``) through the
   runner's archive-only seam (#54), forced past the freshness TTL for daily determinism, and
2. Re-drives the archive-first House-Position span builder
   (:func:`usa_wa_adapter_sos.house.build.build_house_position_spans`) scoped to the current
   biennium — materializing ``usa_wa_legislature`` ``state_representative`` Position seat spans
   (the current biennium as the open end).

**Ordering.** Runs **after** the WSL refresh: the sitting House roster (who sits / LD / party) is
read archive-first from the WSL sponsor archive (``sponsors:<biennium>``, written by the WSL
refresh), and the seat binds to WSL-sourced :class:`Person`s. Independent of the PDC refresh (PDC
is identifier-only since #101). A live ``GetSponsors`` fallback covers an un-archived biennium.

This is the daily counterpart of the historical House backfill (the same builder with
``restrict_to_biennium=None``): **one builder → the #100 CR finding-1 depth mismatch cannot
recur** (a cross-2018 member builds the same deep span daily and historically).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from usa_wa_adapter_pdc.adapter import election_years_for_biennium

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_legislature.refresh import biennium_for_date
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_adapter_sos.house.build import build_house_position_spans
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_adapter_sos.results.adapter import ResultsAdapter, legresults_resource_id
from usa_wa_adapter_sos.results.transport import LegislativeExportNotFound, SOSResultsClient

logger = get_logger(__name__)

_JURISDICTION_SLUG = "usa-wa"


@dataclass(frozen=True)
class SosRefreshOutcome:
    """Counts from one SOS refresh cycle."""

    cohorts_archived: int
    house_spans: int


async def run_refresh(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    sponsor_client: WSLClient | None = None,
    member_client: WSLClient | None = None,
    sos_client: SOSResultsClient | None = None,
) -> SosRefreshOutcome:
    """Execute one SOS refresh cycle: archive the current results cohort, then re-drive the
    House-Position span builder scoped to the current biennium. ``sponsor_client`` /
    ``member_client`` / ``sos_client`` are injectable for tests."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    current = biennium_for_date(datetime.now(UTC).date())
    if biennium != current:
        logger.warning(
            "sos_refresh_noncurrent_biennium",
            extra={"biennium": biennium, "current_biennium": current},
        )

    election_years = election_years_for_biennium(biennium)
    jurisdiction = (
        await session.execute(select(Jurisdiction).where(Jurisdiction.slug == _JURISDICTION_SLUG))
    ).scalar_one()
    source = await get_or_create_results_source(session, jurisdiction)

    adapter = ResultsAdapter(election_years=election_years, client=sos_client or SOSResultsClient())
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )

    # 1. Archive every general a biennium's membership can be decided by (#106): the even seating
    #    year and the odd mid-biennium special (Nov 2025 seated Hunt + four House appointees). Each
    #    cohort archives in its OWN SAVEPOINT — an odd-year cohort 404s from January until the
    #    November election is certified, and a race-less year carries no Legislative CSV; either
    #    would otherwise fail the daily unit (and page the operator via OnFailure=). Forced past the
    #    freshness TTL for daily determinism (the dedup guard still bounds RawPayload growth on a
    #    byte-identical re-pull).
    archived = 0
    seating_year = election_years[0]  # the even seating cohort — see election_years_for_biennium
    for year in election_years:
        try:
            async with session.begin_nested():
                if await runner.archive_only(legresults_resource_id(year), force=True):
                    archived += 1
        except (httpx.HTTPError, LegislativeExportNotFound) as exc:
            # Mirror the harvest's INFO/WARNING split (#106 A3), so a routine miss isn't a daily
            # alert (this project alerts on WARNING rises, #85). The odd special cohort is EXPECTED
            # absent for most of the biennium — it 404s from January until that November's election
            # is certified, and a race-less year carries no CSV — so its miss is INFO. Only the even
            # SEATING cohort (a past election that should serve) failing is a genuine WARNING.
            level = logger.warning if year == seating_year else logger.info
            level("sos_refresh_cohort_year_skipped", extra={"year": year, "error": str(exc)})

    # 2. Re-drive the House-Position span builder scoped to the current biennium (each scoped
    #    member keeps their full cross-biennium span history; the current biennium is the open end).
    result = await build_house_position_spans(
        session,
        sponsor_client=sponsor_client,
        member_client=member_client,
        current_biennium=biennium,
        restrict_to_biennium=biennium,
    )
    logger.info(
        "sos_refresh_complete",
        extra={
            "biennium": biennium,
            "election_years": election_years,
            "cohorts_archived": archived,
            "house_spans": result.house_spans,
            "closed_stale": result.closed_stale,
            "sweep_aborted": result.sweep_aborted,
        },
    )
    return SosRefreshOutcome(cohorts_archived=archived, house_spans=result.house_spans)


async def _main() -> int:
    configure_logging()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2
    engine = create_async_engine(database_url)
    try:
        try:
            async with AsyncSession(engine) as session, session.begin():
                outcome = await run_refresh(session)
        except Exception:
            logger.exception("sos_refresh_failed")
            return 1
        print(
            f"SOS refresh: cohorts_archived={outcome.cohorts_archived} "
            f"house_spans={outcome.house_spans}"
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
