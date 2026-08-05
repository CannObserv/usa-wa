"""One-shot producer retraction of spurious anchored assignments (#144 Phase 2).

power-map#391 shipped the producer retraction verb — ``op:"retract"`` on
``AssignmentObservationRequest`` (v0.20.0). This CLI is the #144 Phase 2 tool: given a curated set
of local assignment ``source_id``s, it retires each artifact tenure usa-wa produced (the Wynne LD39
Senate 2001-02 chamber-conflation, its paired party span) through the sanctioned ``/observations``
channel — no orphan, no ``/admin/`` route.

For each ``(source, source_id)`` it resolves the anchored :class:`Assignment` and POSTs the
id-addressed retraction ``{"identifier_type":"pm_assignment_id","identifier_value":<ulid>,
"op":"retract"}`` to ``/api/v1/assignments/observations``. ``op`` rides the request model's
``additional_properties`` (the #111 pattern the ``unapplied`` field already uses — no client
regen). On a ``retracted`` disposition it tombstones the local row (``archived_at`` — the
reversible lifecycle axis mirroring PM's archive; ``_seat_scope`` excludes archived rows so the
``--sweep-biennia`` audit clears immediately). An already-``archived_at`` target (a completed prior
run) is an idempotent ``already_retracted`` (no re-POST); an unanchored / not-found ``source_id`` is
skipped-and-counted; an unexpected disposition does **not** tombstone (surfaced for the operator).

**Retraction is terminal** — power-map#391 deliberately did not ship a reversible
``archived:false``; un-retract is an admin-only PM operation
(``POST /admin/role-assignments/{id}/unarchive/``). This CLI must never build retry logic against
un-retract. PM's anti-resurrection (both create doors attach to the archived twin) + the #144
Phase 1 derivation exclusion keep the phantom span from ever returning.

Local ``archived_at`` write on a canonical table → app role. No operator token (shell = the trust
boundary, like the migrate/heal one-shots). ``--dry-run`` resolves + previews the targets WITHOUT
POSTing (a retract POST is an irreversible PM mutation a local rollback can't undo). Exit ``0``
clean (incl. an idempotent re-run) · ``1`` a target left unsettled (not-found / unanchored /
refused) · ``2`` auth.

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
from clearinghouse_sync_powermap.client import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_RETRACTED,
    DeliveryBlockedError,
    RetryableClientError,
)
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

#: The assignment observation channel (power-map#391 routes ``op:"retract"`` here).
OBSERVE_PATH = "/api/v1/assignments/observations"

#: The producer source these artifact assignments belong to (the seat/party spans this CLI retires
#: are all ``usa_wa_legislature``). Scopes the resolve to the full natural key ``(source,
#: source_id)`` — a same-``source_id`` row under a different source can't be mis-picked.
DEFAULT_SOURCE = "usa_wa_legislature"

#: Dispositions that confirm a retract succeeded → tombstone locally. ``retracted`` is the first
#: retract of a live tenure; ``auto-attached`` is PM's **already-archived no-op** on a re-retract
#: (power-map#391 checks it before provenance so a re-emit stays quiet) — idempotent, also a
#: success. Anything else (e.g. ``rejected``) is surfaced un-tombstoned.
_RETRACT_SUCCESS = frozenset({DISPOSITION_RETRACTED, DISPOSITION_AUTO_ATTACHED})

#: Foreground backoff schedule (seconds) on a transient 429/5xx, mirroring
#: ``heal_assignment_clocks`` — PM's 429 limit is live (#85), so a throttled POST retries rather
#: than crashing the run.
_BACKOFF_SECONDS = (1, 2, 4, 8)


async def _resolve(session: AsyncSession, source: str, source_id: str) -> Assignment | None:
    """The assignment for the full natural key ``(source, source_id)`` (unique), or None.

    Deliberately **not** ``live_only``: an already-``archived_at`` row (a completed prior retract)
    must still resolve so a re-run recognises it as already-retracted rather than ``not_found``. A
    terminally ``deleted_at`` row is excluded (it is gone, nothing to retract)."""
    return (
        await session.execute(
            select(Assignment).where(
                Assignment.source == source,
                Assignment.source_id == source_id,
                Assignment.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _post_with_backoff(client: Any, payload: dict, *, sleep=asyncio.sleep) -> Any:
    """POST the observation, retrying a transient :class:`RetryableClientError` (429/5xx) on the
    bounded :data:`_BACKOFF_SECONDS` schedule (the PM client surfaces but does not retry it). A
    persistent outage re-raises after the budget — the run is idempotent, so a re-run resumes."""
    for delay in _BACKOFF_SECONDS:
        try:
            return await client.post_observation(OBSERVE_PATH, payload)
        except RetryableClientError:
            logger.warning("retract_pm_retry", extra={"backoff_s": delay})
            await sleep(delay)
    return await client.post_observation(OBSERVE_PATH, payload)


async def retract_assignments(
    session: AsyncSession,
    client: Any,
    source_ids: list[str],
    *,
    source: str = DEFAULT_SOURCE,
    dry_run: bool = False,
) -> dict:
    """Retract each ``(source, source_id)``'s anchored assignment on PM and tombstone it locally.

    POSTs the id-addressed ``op:"retract"`` payload; on a :data:`_RETRACT_SUCCESS` disposition sets
    ``archived_at`` (the reversible tombstone mirroring PM's archive). An already-``archived_at``
    target is recognised as ``already_retracted`` (idempotent — no re-POST); a not-found or
    unanchored ``source_id`` is counted and skipped; any other disposition is counted ``unexpected``
    and left un-tombstoned. Executes in the caller's transaction; does not commit.

    ``dry_run`` resolves + validates the targets and counts ``would_retract`` but **never POSTs** —
    a retract POST is an irreversible PM mutation a local rollback cannot undo, so a dry-run must
    not touch PM at all (unlike a read-then-local-write heal, whose rollback is truly dry).
    """
    retracted = already_retracted = would_retract = not_found = not_anchored = unexpected = 0
    now = datetime.now(UTC)
    for source_id in source_ids:
        row = await _resolve(session, source, source_id)
        if row is None:
            not_found += 1
            logger.warning("retract_source_id_not_found", extra={"source_id": source_id})
            continue
        if row.pm_assignment_id is None:
            not_anchored += 1
            logger.warning("retract_source_id_unanchored", extra={"source_id": source_id})
            continue
        if row.archived_at is not None:
            already_retracted += 1
            logger.info("assignment_already_retracted", extra={"source_id": source_id})
            continue
        if dry_run:
            would_retract += 1
            logger.info(
                "retract_dry_run_would_retract",
                extra={"source_id": source_id, "pm_assignment_id": str(row.pm_assignment_id)},
            )
            continue
        payload = {
            "identifier_type": "pm_assignment_id",
            "identifier_value": str(row.pm_assignment_id),
            "op": "retract",
        }
        result = await _post_with_backoff(client, payload)
        if result.disposition in _RETRACT_SUCCESS:
            row.archived_at = now
            retracted += 1
            logger.info(
                "assignment_retracted",
                extra={
                    "source_id": source_id,
                    "pm_assignment_id": str(row.pm_assignment_id),
                    "disposition": result.disposition,
                },
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
        "already_retracted": already_retracted,
        "would_retract": would_retract,
        "not_found": not_found,
        "not_anchored": not_anchored,
        "unexpected": unexpected,
    }


def exit_code(result: dict) -> int:
    """``1`` when any requested target was left **unsettled** — ``not_found`` (a typo'd id or a row
    that never existed), ``not_anchored`` (nothing to retract on PM), or ``unexpected`` (PM refused)
    — else ``0``. A clean run and an idempotent re-run (all ``retracted``/``already_retracted``/
    ``would_retract``) both exit ``0``, so automation sees non-zero only on a real failure/typo."""
    unsettled = result["not_found"] + result["not_anchored"] + result["unexpected"]
    return 1 if unsettled else 0


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
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"producer source of the assignments (default {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve + preview targets WITHOUT POSTing the retraction to PM",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — cannot reach Power Map.")
    async with get_session_factory()() as session:
        client = build_pm_client(settings)
        try:
            result = await retract_assignments(
                session, client, args.source_ids, source=args.source, dry_run=args.dry_run
            )
            if args.dry_run:
                # No PM POST and no local write happened; rollback is belt-and-suspenders.
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
    return exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
