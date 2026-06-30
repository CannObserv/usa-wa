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
from clearinghouse_core.provenance import RetentionPolicy, Source
from clearinghouse_core.runner import AdapterRunner, RunSummary
from usa_wa_adapter_legislature.adapter import WALegislatureAdapter
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.meeting_windows import biennium_window, meetings_resource_id
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


def _biennium_start_year(label: str) -> int:
    """Parse the odd start year from a ``YYYY-YY`` biennium label."""
    return int(label.split("-", 1)[0])


def biennium_start_date(label: str) -> date:
    """The date a biennium begins — Jan 1 of its odd start year.

    WSL exposes no explicit committee name-change date; this biennium-start boundary
    is the documented approximation used to window a detected rename (#46).
    """
    return date(_biennium_start_year(label), 1, 1)


def previous_biennium(label: str) -> str:
    """The biennium two years before ``label`` (the rename diff's "before" side, #46)."""
    start = _biennium_start_year(label) - 2
    return f"{start}-{(start + 1) % 100:02d}"


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
        # Provenance-critical: the archived SOAP wire (#54) is a long-lived
        # tamper-evident record, not an operational cache — exempt from any
        # future RawPayload GC.
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    return row


@dataclasses.dataclass(frozen=True)
class RefreshOutcome:
    """Result of one :func:`run_refresh`: the committees summary + the additive
    meeting-discovery upsert count, so the operator sees both kinds of work."""

    committees: RunSummary
    meetings_upserted: int


async def run_refresh(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    meeting_client: WSLClient | None = None,
) -> RefreshOutcome:
    """Execute one WSL refresh cycle against the supplied session.

    Runs the committees discovery, then an **additive** current-biennium meeting-docket
    pull for the Joint/`Other` class (#39). Returns a :class:`RefreshOutcome` carrying
    the committees :class:`RunSummary` and the meeting-discovery upsert count; the meeting
    pull is best-effort — a meeting-service outage must not fail the (primary) committees
    refresh (its count is 0 on failure).

    ``meeting_client`` is injectable for tests; production defaults to a real
    ``CommitteeMeetingService`` client.
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
        meeting_client=meeting_client,
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
    meetings_upserted = await _discover_current_meeting_window(runner, biennium)
    return RefreshOutcome(committees=summary, meetings_upserted=meetings_upserted)


async def _discover_current_meeting_window(runner: AdapterRunner, biennium: str) -> int:
    """Additive Joint/`Other` discovery from the current biennium's meeting window.

    Cache-or-fetch keyed on the stable ``committee-meetings:<begin>:<end>`` id (the
    source's ``cache_ttl_days`` bounds re-fetch; the runner skips re-archiving an
    unchanged-hash payload, and #57 tracks further window-archival optimizations).
    Absence of a previously-seen body
    from the window is **not** retirement — the meeting normalizer only ever upserts
    the bodies present, never marks an absent one inactive (#39). Best-effort: a
    failure is logged and swallowed (returns 0) so the committees refresh still
    succeeds. Returns the upsert count."""
    resource_id = meetings_resource_id(*biennium_window(biennium))
    try:
        upserted = await runner.fetch_and_normalize(resource_id)
        logger.info(
            "wsl_meeting_discovery",
            extra={"biennium": biennium, "resource_id": resource_id, "upserted": upserted},
        )
        return upserted
    except Exception:
        logger.exception("wsl_meeting_discovery_failed", extra={"resource_id": resource_id})
        return 0


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
            # Surface the failure cleanly so cron/journal gets a single
            # actionable line plus the traceback in logs, and the process
            # exits 1 (operator-style) instead of dumping a bare traceback.
            logger.exception("wsl_refresh_failed")
            return 1
        committees = outcome.committees
        print(
            f"WSL refresh: committees(discovered={committees.discovered} "
            f"fetched={committees.fetched} "
            f"skipped={committees.skipped_cache_hit} "
            f"upserted={committees.upserted_entities} "
            f"errors={committees.errors}) "
            f"meetings(upserted={outcome.meetings_upserted})"
        )
        return 0 if committees.errors == 0 else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
