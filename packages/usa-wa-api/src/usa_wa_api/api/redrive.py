"""Operator-friendly re-drive surface for dead-lettered (UNAVAILABLE) outbox work.

``POST /sync/redrive`` wraps :meth:`SyncEngine.redrive_unavailable` and
:meth:`SyncEngine.count_unavailable` — the DB/REPL recovery hooks — with an
operator-callable HTTP route: optional ``entity_type`` / age scoping, a
non-mutating ``dry_run`` preview, and the ``X-Operator-Token`` auth gate (this
route mutates state).

Both the matched-count preview and the ``UNAVAILABLE → PENDING`` flip are owned
by the engine, so scope predicates and reset value-set live in exactly one
place. This module only adapts the operator-friendly ``older_than_seconds`` int
to the engine's ``timedelta`` and reports the counts.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.engine import SyncEngine
from usa_wa_api.api.deps import get_db_session, require_operator

logger = get_logger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_operator)])


async def perform_redrive(
    session: AsyncSession,
    *,
    entity_type: str | None = None,
    older_than_seconds: int | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    """Re-drive scope-matched UNAVAILABLE outbox entries back to PENDING.

    Shared core behind the HTTP route and the CLI. ``dry_run`` returns the
    counts without mutating. ``limit`` caps the flip (oldest-first) while
    ``matched`` reports the full in-scope pile; ``would_redrive`` is the count a
    real call with these exact params would flip (``min(matched, limit)``), so a
    dry run previews the capped effect rather than the whole pile. Both the count
    and the flip defer to the engine (:meth:`SyncEngine.count_unavailable` /
    :meth:`redrive_unavailable`), so scope and reset semantics are never
    duplicated here. The clientless, registry-less engine is a safe, intentional
    shim — these two methods only touch ``session`` and never exercise any
    read/write path. Does not commit — the caller owns the transaction. Returns
    ``matched`` / ``would_redrive`` / ``redriven`` counts, the echoed filters, and
    the ``dry_run`` flag.
    """
    now = now or datetime.now(UTC)
    older_than = timedelta(seconds=older_than_seconds) if older_than_seconds is not None else None
    engine = SyncEngine(descriptors=(), client=None)

    matched = await engine.count_unavailable(
        session, now=now, entity_type=entity_type, older_than=older_than
    )
    would_redrive = min(matched, limit) if limit is not None else matched

    redriven = 0
    if not dry_run and matched:
        redriven = await engine.redrive_unavailable(
            session, now=now, entity_type=entity_type, older_than=older_than, limit=limit
        )

    return {
        "matched": matched,
        "would_redrive": would_redrive,
        "redriven": redriven,
        "dry_run": dry_run,
        "entity_type": entity_type,
        "older_than_seconds": older_than_seconds,
        "limit": limit,
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
    limit: int | None = Query(
        default=None,
        ge=1,
        description="Cap the number of entries re-driven (oldest first).",
    ),
    dry_run: bool = Query(
        default=False,
        description="Preview the matched count without mutating any rows.",
    ),
) -> dict:
    """Re-drive dead-lettered (UNAVAILABLE) outbox entries back to PENDING.

    Operator action once the cause is cleared (PM recovered, credential
    re-scoped). Optionally scoped by ``entity_type`` and/or age and capped by
    ``limit`` (oldest first); ``dry_run=true`` mutates nothing. Returns the
    ``matched`` pile size, ``would_redrive`` (what a real call with these params
    would flip — useful to preview a ``limit``), the number actually re-driven
    (``0`` for a dry run), the echoed filters, and the ``dry_run`` flag.
    """
    return await perform_redrive(
        session,
        entity_type=entity_type,
        older_than_seconds=older_than_seconds,
        limit=limit,
        dry_run=dry_run,
    )
