"""One-shot producer retraction of spurious anchored assignments (#144 Phase 2).

power-map#391 shipped the producer retraction verb — ``op:"retract"`` on
``AssignmentObservationRequest`` (v0.20.0). This CLI is the #144 Phase 2 tool: given a curated set
of local assignment ``source_id``s, it retires each artifact tenure usa-wa produced (the Wynne LD39
Senate 2001-02 chamber-conflation, its paired party span) through the sanctioned ``/observations``
channel — no orphan, no ``/admin/`` route.

For each ``source_id`` it resolves the live, anchored :class:`Assignment` and POSTs the
id-addressed retraction ``{"identifier_type":"pm_assignment_id","identifier_value":<ulid>,
"op":"retract"}`` to ``/api/v1/assignments/observations``. ``op`` rides the request model's
``additional_properties`` (the #111 pattern the ``unapplied`` field already uses — no client
regen). On a ``retracted`` disposition it tombstones the local row (``archived_at`` — the
reversible lifecycle axis mirroring PM's archive; ``_seat_scope`` excludes archived rows so the
``--sweep-biennia`` audit clears immediately). An unanchored / not-found ``source_id`` is
skipped-and-counted; an unexpected disposition does **not** tombstone (surfaced for the operator).

**Retraction is terminal** — power-map#391 deliberately did not ship a reversible
``archived:false``; un-retract is an admin-only PM operation
(``POST /admin/role-assignments/{id}/unarchive/``). This CLI must never build retry logic against
un-retract. PM's anti-resurrection (both create doors attach to the archived twin) + the #144
Phase 1 derivation exclusion keep the phantom span from ever returning.

Local ``archived_at`` write on a canonical table → app role. No operator token (shell = the trust
boundary, like the migrate/heal one-shots). ``--dry-run`` previews; exit ``0`` clean · ``2`` auth.

    python -m usa_wa_sync_powermap.retract_assignments --dry-run \
        --source-id 481:chamber-senate:39:2001-02 --source-id 481:party:republican:2001-02
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Assignment
from clearinghouse_domain_legislative.queries import live_only
from clearinghouse_sync_powermap.client import DISPOSITION_RETRACTED, DeliveryBlockedError
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

#: The assignment observation channel (power-map#391 routes ``op:"retract"`` here).
OBSERVE_PATH = "/api/v1/assignments/observations"


async def _resolve(session: AsyncSession, source_id: str) -> Assignment | None:
    """The one live assignment with this ``source_id`` (natural key is unique), or None."""
    return (
        await session.execute(
            live_only(select(Assignment).where(Assignment.source_id == source_id), Assignment)
        )
    ).scalar_one_or_none()


async def retract_assignments(session: AsyncSession, client: Any, source_ids: list[str]) -> dict:
    """Retract each ``source_id``'s live anchored assignment on PM and tombstone it locally.

    POSTs the id-addressed ``op:"retract"`` payload; on a ``retracted`` disposition sets
    ``archived_at`` (the reversible tombstone mirroring PM's archive). A not-found or unanchored
    ``source_id`` is counted and skipped; a non-``retracted`` disposition is counted ``unexpected``
    and left un-tombstoned. Executes in the caller's transaction; does not commit.
    """
    retracted = not_found = not_anchored = unexpected = 0
    now = datetime.now(UTC)
    for source_id in source_ids:
        row = await _resolve(session, source_id)
        if row is None:
            not_found += 1
            logger.warning("retract_source_id_not_found", extra={"source_id": source_id})
            continue
        if row.pm_assignment_id is None:
            not_anchored += 1
            logger.warning("retract_source_id_unanchored", extra={"source_id": source_id})
            continue
        payload = {
            "identifier_type": "pm_assignment_id",
            "identifier_value": str(row.pm_assignment_id),
            "op": "retract",
        }
        result = await client.post_observation(OBSERVE_PATH, payload)
        if result.disposition == DISPOSITION_RETRACTED:
            row.archived_at = now
            retracted += 1
            logger.info(
                "assignment_retracted",
                extra={"source_id": source_id, "pm_assignment_id": str(row.pm_assignment_id)},
            )
        else:
            unexpected += 1
            logger.warning(
                "assignment_retract_unexpected_disposition",
                extra={"source_id": source_id, "disposition": result.disposition},
            )
    await session.flush()
    return {
        "requested": len(source_ids),
        "retracted": retracted,
        "not_found": not_found,
        "not_anchored": not_anchored,
        "unexpected": unexpected,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.retract_assignments",
        description="Retract spurious anchored assignments on PM + tombstone locally (#144 Ph2).",
    )
    parser.add_argument(
        "--source-id",
        action="append",
        dest="source_ids",
        required=True,
        metavar="SOURCE_ID",
        help="local Assignment.source_id to retract (repeatable)",
    )
    parser.add_argument("--dry-run", action="store_true", help="preview without committing")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — cannot reach Power Map.")
    async with get_session_factory()() as session:
        client = build_pm_client(settings)
        try:
            result = await retract_assignments(session, client, args.source_ids)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
