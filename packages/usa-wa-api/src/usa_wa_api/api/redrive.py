"""Operator-friendly re-drive surface for dead-lettered (UNAVAILABLE) outbox work.

``POST /sync/redrive`` wraps :meth:`SyncEngine.redrive_unavailable` — the
DB/REPL-only recovery hook shipped under #5 — with an operator-callable HTTP
route: optional ``entity_type`` / age scoping, a non-mutating ``dry_run``
preview, and the ``X-Operator-Token`` auth gate (this route mutates state).

The engine method resets *every* UNAVAILABLE row unconditionally, so it is used
as-is only for the unscoped re-drive. When a scope filter is supplied the same
``UNAVAILABLE → PENDING`` reset is applied through a filtered ``UPDATE`` here,
mirroring the engine's value set (attempts zeroed, ``last_error`` cleared,
``next_attempt_at`` set to now) without modifying the engine.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.models import (
    STATUS_PENDING,
    STATUS_UNAVAILABLE,
    OutboxEntry,
)
from usa_wa_api.api.deps import get_db_session, require_operator

logger = get_logger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_operator)])


def _scope_filters(entity_type: str | None, older_than_seconds: int | None, now: datetime):
    """Build the WHERE predicates selecting the UNAVAILABLE rows in scope.

    Always pins ``status == UNAVAILABLE`` (the only re-drivable terminal pile),
    then narrows by entity type and/or age when those filters are given.
    """
    filters = [OutboxEntry.status == STATUS_UNAVAILABLE]
    if entity_type is not None:
        filters.append(OutboxEntry.entity_type == entity_type)
    if older_than_seconds is not None:
        filters.append(OutboxEntry.created_at <= now - timedelta(seconds=older_than_seconds))
    return filters


async def perform_redrive(
    session: AsyncSession,
    *,
    entity_type: str | None = None,
    older_than_seconds: int | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    """Re-drive scope-matched UNAVAILABLE outbox entries back to PENDING.

    Shared core behind the HTTP route and the CLI. ``dry_run`` returns the
    matched count without mutating. The unscoped case defers to the shipped
    :meth:`SyncEngine.redrive_unavailable`; scoped cases apply the same value set
    through a filtered ``UPDATE`` (the engine method takes no scope params). Does
    not commit — the caller owns the transaction. Returns ``matched`` /
    ``redriven`` counts, the echoed filters, and the ``dry_run`` flag.
    """
    now = now or datetime.now(UTC)
    filters = _scope_filters(entity_type, older_than_seconds, now)
    scoped = entity_type is not None or older_than_seconds is not None

    matched = (
        await session.execute(select(func.count()).select_from(OutboxEntry).where(*filters))
    ).scalar_one()

    redriven = 0
    if not dry_run and matched:
        if scoped:
            result = await session.execute(
                update(OutboxEntry)
                .where(*filters)
                .values(
                    status=STATUS_PENDING,
                    attempts=0,
                    next_attempt_at=now,
                    last_error=None,
                )
                .execution_options(synchronize_session=False)
            )
            redriven = result.rowcount
            logger.info(
                "powermap_outbox_redriven",
                extra={"count": redriven, "entity_type": entity_type},
            )
        else:
            # Unscoped: defer to the shipped engine method verbatim. The method
            # only touches ``session``, so a registry-less / clientless engine is
            # a safe, intentional shim — it is never used for any read/write path.
            engine = SyncEngine(descriptors=(), client=None)
            redriven = await engine.redrive_unavailable(session, now=now)

    return {
        "matched": matched,
        "redriven": redriven,
        "dry_run": dry_run,
        "entity_type": entity_type,
        "older_than_seconds": older_than_seconds,
    }


@router.post("/redrive")
async def redrive(
    session: AsyncSession = Depends(get_db_session),
    entity_type: str | None = Query(
        default=None, description="Only re-drive entries of this entity type."
    ),
    older_than_seconds: int | None = Query(
        default=None,
        ge=0,
        description="Only re-drive entries created at least this many seconds ago.",
    ),
    dry_run: bool = Query(
        default=False,
        description="Preview the matched count without mutating any rows.",
    ),
) -> dict:
    """Re-drive dead-lettered (UNAVAILABLE) outbox entries back to PENDING.

    Operator action once the cause is cleared (PM recovered, credential
    re-scoped). Optionally scoped by ``entity_type`` and/or age; ``dry_run=true``
    returns the matched count and mutates nothing. Returns the matched count, the
    number actually re-driven (``0`` for a dry run), the echoed filters, and the
    ``dry_run`` flag.
    """
    return await perform_redrive(
        session,
        entity_type=entity_type,
        older_than_seconds=older_than_seconds,
        dry_run=dry_run,
    )
