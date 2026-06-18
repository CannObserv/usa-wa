"""CLI entrypoint for one refresh cycle of the WSL adapter.

Usage:
  python -m usa_wa_adapter_legislature.refresh

Reads ``DATABASE_URL`` from the environment, computes the current biennium
(override with ``USA_WA_BIENNIUM``), resolves the ``usa-wa`` jurisdiction,
lazily creates the ``usa_wa_legislature`` Source row, bootstraps the
synthetic anchors (legislature, chambers, biennium + regular sessions),
and runs one :class:`AdapterRunner.refresh` cycle.

Designed to be invoked from cron or systemd; idempotent on re-run within
the source's cache TTL (no live SOAP call, no new rows).

Biennium computation: WA bienniums begin on odd years. Even-year dates roll
back to the prior odd year (``2026-06-18`` → ``2025-26``). Override via
``USA_WA_BIENNIUM`` for testing or early-year edge cases.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Source
from clearinghouse_core.runner import AdapterRunner, RunSummary
from usa_wa_adapter_legislature.adapter import WALegislatureAdapter
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.transport import WSL_BASE_URL, WSLClient

logger = get_logger(__name__)


def biennium_for_date(today: date) -> str:
    """Compute the WA biennium label (``YYYY-YY``) covering ``today``.

    Bienniums begin on odd years (2025-26, 2027-28, …). On an even year we
    roll back to the prior odd year.
    """
    start = today.year if today.year % 2 == 1 else today.year - 1
    end_suffix = (start + 1) % 100
    return f"{start}-{end_suffix:02d}"


async def _resolve_jurisdiction(session: AsyncSession) -> Jurisdiction:
    row = (
        await session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(
            "Jurisdiction 'usa-wa' is not seeded — run the jurisdictional IA "
            "bootstrap before invoking the WSL refresh."
        )
    return row


async def _get_or_create_source(session: AsyncSession, jurisdiction: Jurisdiction) -> Source:
    existing = (
        await session.execute(select(Source).where(Source.slug == "usa_wa_legislature"))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA State Legislature SOAP",
        slug="usa_wa_legislature",
        kind="soap",
        base_url=WSL_BASE_URL,
        reliability=1.0,
        cache_ttl_days=1,
    )
    session.add(row)
    await session.flush()
    return row


async def run_refresh(session: AsyncSession, *, biennium: str | None = None) -> RunSummary:
    """Execute one WSL refresh cycle against the supplied session.

    Returns the :class:`RunSummary` aggregated across discovered resources.
    """
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    jurisdiction = await _resolve_jurisdiction(session)
    source = await _get_or_create_source(session, jurisdiction)
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=biennium, jurisdiction_id=jurisdiction.id
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=jurisdiction.id,
        biennium=biennium,
        client=WSLClient("CommitteeService"),
    )
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
    )
    summary = await runner.refresh()
    logger.info(
        "wsl_refresh_summary",
        extra={"summary": dataclasses.asdict(summary), "biennium": biennium},
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
            # Surface the failure cleanly so cron/journal gets a single
            # actionable line plus the traceback in logs, and the process
            # exits 1 (operator-style) instead of dumping a bare traceback.
            logger.exception("wsl_refresh_failed")
            return 1
        print(
            f"WSL refresh: discovered={summary.discovered} "
            f"fetched={summary.fetched} "
            f"skipped={summary.skipped_cache_hit} "
            f"upserted={summary.upserted_entities} "
            f"errors={summary.errors}"
        )
        return 0 if summary.errors == 0 else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
