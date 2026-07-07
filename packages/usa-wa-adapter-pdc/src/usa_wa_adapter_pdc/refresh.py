"""WA PDC refresh — ``python -m usa_wa_adapter_pdc.refresh``.

Daily-driven counterpart to the WSL refresh: resolves the current biennium (override with
``USA_WA_BIENNIUM``), pulls the seated House winner cohort from PDC for that biennium's
election year, matches each winner to the existing WSL :class:`Person` (using a
``GetSponsors`` roster for House districts), and materializes the ``person_wa_pdc``
identifier + House ``state_representative`` seat Assignment (#69).

Runs **after** the WSL refresh so the WSL House Persons it binds to already exist. The
adapter runs ``fill_only=True`` (#65): additive discovery that never clobbers a
PM-curated row. An optional ``USA_WA_PDC_APP_TOKEN`` raises Socrata's rate limit (sent as
``X-App-Token`` only when set; not required at this once-daily single-GET volume).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import RetentionPolicy, Source
from clearinghouse_core.runner import AdapterRunner, RunSummary
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.refresh import biennium_for_date
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_adapter_pdc.adapter import PDCAdapter, election_year_for_biennium
from usa_wa_adapter_pdc.normalize.house_positions import build_house_roster
from usa_wa_adapter_pdc.transport import PDC_BASE_URL, PDCClient

logger = get_logger(__name__)

_JURISDICTION_SLUG = "usa-wa"


async def _get_or_create_source(session: AsyncSession, jurisdiction: Jurisdiction) -> Source:
    existing = (
        await session.execute(select(Source).where(Source.slug == "usa_wa_pdc"))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA Public Disclosure Commission",
        slug="usa_wa_pdc",
        kind="rest",
        base_url=PDC_BASE_URL,
        reliability=1.0,
        cache_ttl_days=1,
        # The archived SODA JSON (#54) is a long-lived provenance record, not an
        # operational cache — exempt from any future RawPayload GC.
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    return row


async def run_refresh(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    sponsor_client: WSLClient | None = None,
    pdc_client: PDCClient | None = None,
) -> RunSummary:
    """Execute one PDC refresh cycle against the supplied session.

    Pulls the WSL ``GetSponsors`` roster (for House member → district), then drives the
    :class:`PDCAdapter` through the runner (``fill_only=True``). ``sponsor_client`` /
    ``pdc_client`` are injectable for tests; production defaults to the real clients."""
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
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=biennium, jurisdiction_id=jurisdiction.id
    )
    source = await _get_or_create_source(session, jurisdiction)

    sponsor_client = sponsor_client or WSLClient("SponsorService")
    sponsor_members = await sponsor_client.get_sponsors(biennium)
    house_roster = build_house_roster(sponsor_members)

    adapter = PDCAdapter(
        anchors=anchors,
        biennium=biennium,
        house_roster=house_roster,
        client=pdc_client or PDCClient(app_token=os.environ.get("USA_WA_PDC_APP_TOKEN")),
        session=session,
    )
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )
    summary = await runner.refresh()
    logger.info(
        "pdc_refresh_complete",
        extra={
            "biennium": biennium,
            "election_year": election_year_for_biennium(biennium),
            "house_roster_lds": len(house_roster),
            "fetched": summary.fetched,
            "upserted": summary.upserted_entities,
            "errors": summary.errors,
        },
    )
    return summary


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
                summary = await run_refresh(session)
        except Exception:
            logger.exception("pdc_refresh_failed")
            return 1
        print(
            f"PDC refresh: house-winners(fetched={summary.fetched} "
            f"skipped={summary.skipped_cache_hit} "
            f"upserted={summary.upserted_entities} errors={summary.errors})"
        )
        return 0 if summary.errors == 0 else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
