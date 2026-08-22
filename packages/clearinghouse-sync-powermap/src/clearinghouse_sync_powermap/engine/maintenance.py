"""Outbox maintenance the drain cannot do for itself.

The drain settles one entry at a time and never looks across rows. Some corrections are
inherently cross-row — "this rejection was already re-attempted under a newer entry" is a
statement about a *pair* — so they live here and run as one-shot operator tools rather than
inside a cycle.
"""

from __future__ import annotations

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.models import (
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
    OutboxEntry,
)

logger = get_logger(__name__)


def _stale_rejection_predicate():
    """``REJECTED`` rows that an open ``PENDING`` entry for the same row has replaced.

    Only ``PENDING`` counts as a replacement: ``DELIVERED`` is some other delivery's settled
    history and ``UNAVAILABLE`` is itself unsettled, so neither says "this was re-attempted".
    The sibling is matched on the ``(entity_type, local_id)`` pair — the key the outbox
    itself uses, and a partial unique index guarantees at most one open entry per pair.
    """
    sibling = OutboxEntry.__table__.alias("sibling")
    return (OutboxEntry.status == STATUS_REJECTED) & exists(
        select(sibling.c.id).where(
            sibling.c.entity_type == OutboxEntry.entity_type,
            sibling.c.local_id == OutboxEntry.local_id,
            sibling.c.status == STATUS_PENDING,
        )
    )


async def supersede_stale_rejections(session: AsyncSession, *, dry_run: bool = False) -> int:
    """Mark superseded rejections ``SUPERSEDED``; return how many (would have) moved.

    Idempotent — a second run finds nothing, because the rows it moved are no longer
    ``REJECTED``. ``dry_run`` counts without writing: this is a bulk status flip over a
    production backlog, and the count is the thing worth checking before it runs.
    """
    predicate = _stale_rejection_predicate()
    if dry_run:
        rows = (await session.execute(select(OutboxEntry.id).where(predicate))).all()
        logger.info("outbox_supersede_preview", extra={"stale_rejections": len(rows)})
        return len(rows)
    result = await session.execute(
        update(OutboxEntry).where(predicate).values(status=STATUS_SUPERSEDED)
    )
    moved = result.rowcount or 0
    logger.info("outbox_superseded", extra={"stale_rejections": moved})
    return moved
