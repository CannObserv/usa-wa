"""One-shot heal for the chronic LWW assignment-clock ping-pong (#102).

A 2026-07-06 span backfill bumped ~4,300 anchored assignment rows' local ``updated_at`` ahead of
PM's clock. The sidecar's LWW then reads local-newer every reconcile and re-POSTs an *identical*
observation forever — PM no-ops it without advancing its own clock, so the skew never heals and PM
eventually 429s the flood (see #102). ``_upsert_assignment``'s SQLAlchemy dirty-tracking means the
daily span rebuild does *not* re-bump unchanged rows, so the skew is a one-time backlog, not a
runaway — a single heal converges it.

This CLI heals the skew: for each anchored assignment whose local ``updated_at`` is strictly newer
than PM's **and whose observation would not change PM** (the churn signature), adopt PM's
``updated_at`` onto the local row — LWW parity, so the next reconcile stops re-enqueuing it. It
adopts only the **clock**, never PM's field values (unlike ``heal_committee_curation``): for
assignments WE are the authority. A row whose observation genuinely differs from PM's record (a
real pending change) is **left untouched** so the reconcile still pushes it — the heal must never
silently drop a pending update. Idempotent; a no-op on rows already at parity.

Local ``updated_at`` write on a canonical table (assignments carry no provenance) → app role.
Read-only against PM. No operator token (shell = the trust boundary). ``--dry-run`` previews;
exit ``0`` clean · ``2`` auth · ``3`` empty-cohort abort.

    python -m usa_wa_sync_powermap.heal_assignment_clocks --dry-run
    python -m usa_wa_sync_powermap.heal_assignment_clocks
"""

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Assignment
from clearinghouse_domain_legislative.queries import live_only
from clearinghouse_sync_powermap.client import DeliveryBlockedError
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import AssignmentDescriptor
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

#: Exit code for a guardrail abort (empty cohort).
EXIT_ABORTED = 3


async def _anchored_cohort(session: AsyncSession) -> list[Assignment]:
    """Live assignments already anchored to PM (the heal targets)."""
    return list(
        (
            await session.execute(
                live_only(
                    select(Assignment).where(Assignment.pm_assignment_id.is_not(None)),
                    Assignment,
                )
            )
        )
        .scalars()
        .all()
    )


async def heal_assignment_clocks(session: AsyncSession, descriptor: Any, client: Any) -> dict:
    """Adopt PM's clock onto anchored assignments whose local clock is spuriously ahead.

    For each anchored assignment: re-fetch PM's record; if local ``updated_at`` is strictly newer
    than PM's *and* the observation would not change PM (``observation_matches_record``), adopt
    PM's ``updated_at`` (clock only) so LWW sees parity. A row already at/behind parity, or one
    whose observation genuinely differs (a real pending change), is left untouched. Empty cohort
    aborts. Executes in the caller's transaction; does not commit.
    """
    cohort = await _anchored_cohort(session)
    if not cohort:
        return {
            "checked": 0,
            "healed": 0,
            "at_parity": 0,
            "pending_change": 0,
            "skipped_missing_pm": 0,
            "aborted": "empty_cohort",
        }

    healed = at_parity = pending_change = skipped_missing = 0
    for row in cohort:
        pm_id = descriptor.anchor_value(row)
        record = await descriptor.fetch_record(client, pm_id)
        if record is None:
            skipped_missing += 1
            logger.warning("heal_pm_missing", extra={"source_id": row.source_id})
            continue
        lu_local = descriptor.last_updated(row)
        lu_pm = descriptor.last_updated(record)
        if lu_local is None or lu_pm is None or not lu_local > lu_pm:
            at_parity += 1  # local not ahead of PM — LWW already lets PM win; nothing to heal
            continue
        if not await descriptor.local_newer_is_noop(session, row, record):
            # Local is newer AND the payload differs → a genuine pending update. Leave it so the
            # reconcile pushes it; adopting PM's clock here would drop the change (LWW would then
            # let PM's older record win and overwrite local). Same test the apply_record gate uses.
            pending_change += 1
            logger.info("heal_pending_change_left", extra={"source_id": row.source_id})
            continue
        descriptor.set_last_updated(row, lu_pm)
        healed += 1
        logger.info("heal_clock_adopted", extra={"source_id": row.source_id})

    await session.flush()
    return {
        "checked": len(cohort),
        "healed": healed,
        "at_parity": at_parity,
        "pending_change": pending_change,
        "skipped_missing_pm": skipped_missing,
        "aborted": None,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.heal_assignment_clocks",
        description="Adopt PM's clock onto LWW-skewed anchored assignments to stop churn (#102).",
    )
    parser.add_argument("--dry-run", action="store_true", help="preview without committing")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — cannot read from Power Map.")
    async with get_session_factory()() as session:
        client = build_pm_client(settings)
        try:
            result = await heal_assignment_clocks(session, AssignmentDescriptor(), client)
            if args.dry_run:
                await session.rollback()
                result = {**result, "dry_run": True}
            else:
                await session.commit()
            return result
        finally:
            await client.aclose()


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except DeliveryBlockedError as exc:
        print(json.dumps({"error": f"delivery blocked: {exc}"}))
        return 2
    print(json.dumps(result, indent=2, default=str))
    return EXIT_ABORTED if result.get("aborted") else 0


if __name__ == "__main__":
    sys.exit(main())
