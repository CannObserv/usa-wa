"""WA PDC refresh — ``python -m usa_wa_facts_seats.pdc.refresh``.

Daily counterpart to the WSL refresh, **identifier-only since #101**. It:

1. Archives every PDC winner cohort the current biennium's membership can be decided by (#121)
   — both House generals (even seating + odd mid-biennium special) and the three staggered/
   special ``senate-winners:<Y>`` cohorts — through the runner's archive-only seam (#54), each
   in its own SAVEPOINT (a transient Socrata failure skips one cohort, not the daily unit), and
2. Re-drives the archive-first identifier builder (:func:`build_pdc_spans`) scoped to the current
   biennium — emitting the ``person_wa_pdc`` cross-source identifier links (House winners + the
   #74 movers + the #75 Senate cohort), era-matched.

**The House Position seat is no longer PDC's (#101).** It is built by the WSL+SOS builder
(:func:`usa_wa_facts_seats.house.build.build_house_position_spans`,
``usa_wa_legislature``-sourced, symmetric with the Senate seat), driven daily by the SOS refresh.
PDC is demoted to the identifier link only — which removes the #100 CR finding-1 two-builder
depth mismatch (this refresh no longer rebuilds a shallow ``usa_wa_pdc`` House span for a sweep
to close). The era roster comes archive-first from the WSL sponsor archive (``sponsors:<biennium>``,
written by the WSL refresh, which runs first); a live ``GetSponsors`` fallback covers an
un-archived biennium. Runs **after** the WSL refresh so the Persons it binds to exist. An
optional ``USA_WA_PDC_APP_TOKEN`` raises Socrata's rate limit.
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

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_adapter_pdc.adapter import (
    HOUSE_WINNERS_RESOURCE_PREFIX,
    SENATE_WINNERS_RESOURCE_PREFIX,
    PDCAdapter,
)
from usa_wa_adapter_pdc.provisioning import get_or_create_source
from usa_wa_adapter_pdc.transport import PDCClient
from usa_wa_common.elections import election_years_for_biennium, senate_election_years_for_biennium
from usa_wa_facts_seats.pdc.build_pdc_spans import build_pdc_spans

logger = get_logger(__name__)

_JURISDICTION_SLUG = "usa-wa"


@dataclass(frozen=True)
class PdcRefreshOutcome:
    """Counts from one PDC refresh cycle (identifier-only since #101)."""

    cohorts_archived: int
    identifiers: int


async def run_refresh(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    sponsor_client: WSLClient | None = None,
    pdc_client: PDCClient | None = None,
) -> PdcRefreshOutcome:
    """Execute one PDC refresh cycle: archive the current cohorts, then re-drive the span
    builder scoped to the current biennium. ``sponsor_client`` / ``pdc_client`` are injectable
    for tests."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    current = biennium_for_date(datetime.now(UTC).date())
    if biennium != current:
        logger.warning(
            "pdc_refresh_noncurrent_biennium",
            extra={"biennium": biennium, "current_biennium": current},
        )

    jurisdiction = (
        await session.execute(select(Jurisdiction).where(Jurisdiction.slug == _JURISDICTION_SLUG))
    ).scalar_one()
    source = await get_or_create_source(session, jurisdiction)

    adapter = PDCAdapter(
        biennium=biennium,
        client=pdc_client or PDCClient(app_token=os.environ.get("USA_WA_PDC_APP_TOKEN")),
    )
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )

    # 1. Archive every cohort the biennium's membership can be decided by (#121): both House
    #    generals (even seating + odd mid-biennium special) and the three Senate cohorts
    #    (staggered evens + the odd special). Forced past the freshness TTL for daily
    #    determinism (the dedup guard still bounds RawPayload growth on a byte-identical
    #    re-pull). Each cohort archives in its OWN SAVEPOINT (the #106 A4 pattern): a raceless
    #    year is a *success* here (SODA returns an empty row set, not a 404), so the guard only
    #    covers a transient Socrata failure — which must not fail the whole daily unit while
    #    the other cohorts and the identifier re-drive can still complete.
    house_years = election_years_for_biennium(biennium)
    senate_years = senate_election_years_for_biennium(biennium)
    resource_ids = [f"{HOUSE_WINNERS_RESOURCE_PREFIX}{y}" for y in house_years]
    resource_ids += [f"{SENATE_WINNERS_RESOURCE_PREFIX}{y}" for y in senate_years]
    archived = 0
    for resource_id in resource_ids:
        try:
            async with session.begin_nested():
                if await runner.archive_only(resource_id, force=True):
                    archived += 1
        except httpx.HTTPError as exc:
            logger.warning(
                "pdc_refresh_cohort_skipped", extra={"resource_id": resource_id, "error": str(exc)}
            )

    # 2. Re-drive the identifier builder scoped to the current biennium (#101: identifier-only —
    #    the House Position seat is the WSL+SOS builder's, driven by the SOS refresh; PDC emits
    #    only the person_wa_pdc cross-links here).
    result = await build_pdc_spans(
        session,
        sponsor_client=sponsor_client,
        restrict_to_biennium=biennium,
    )
    outcome = PdcRefreshOutcome(cohorts_archived=archived, identifiers=result.identifiers)
    logger.info(
        "pdc_refresh_complete",
        extra={
            "biennium": biennium,
            "house_years": house_years,
            "senate_years": senate_years,
            "cohorts_archived": archived,
            "identifiers": result.identifiers,
        },
    )
    return outcome


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
            logger.exception("pdc_refresh_failed")
            return 1
        print(
            f"PDC refresh: cohorts_archived={outcome.cohorts_archived} "
            f"identifiers={outcome.identifiers}"
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
