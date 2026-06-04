"""Sync-schema model tests — outbox ledger + sync-state cursor.

Deployment-agnostic: ``entity_type`` is a free string, so these tests use a
synthetic ``"widget"`` type with no real model behind it.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from clearinghouse_sync_powermap.models import (
    OP_CREATE,
    STATUS_DELIVERED,
    STATUS_PENDING,
    OutboxEntry,
    SyncState,
)


async def test_outbox_round_trip(db_session):
    """A new outbox entry persists with PENDING/0-attempt defaults."""
    local_id = ULID()
    db_session.add(OutboxEntry(entity_type="widget", local_id=local_id, op=OP_CREATE))
    await db_session.flush()

    fetched = (
        await db_session.execute(select(OutboxEntry).where(OutboxEntry.local_id == local_id))
    ).scalar_one()
    assert fetched.status == STATUS_PENDING
    assert fetched.attempts == 0
    assert fetched.next_attempt_at is not None
    assert fetched.last_disposition is None


async def test_outbox_op_check_constraint(db_session):
    """``op`` is constrained to the known vocabulary."""
    db_session.add(OutboxEntry(entity_type="widget", local_id=ULID(), op="BOGUS"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_outbox_one_open_per_row(db_session):
    """Two PENDING entries for the same (entity_type, local_id) collide."""
    local_id = ULID()
    db_session.add(OutboxEntry(entity_type="widget", local_id=local_id, op=OP_CREATE))
    await db_session.flush()
    db_session.add(OutboxEntry(entity_type="widget", local_id=local_id, op=OP_CREATE))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_outbox_settled_row_allows_new_open(db_session):
    """A DELIVERED entry does not block a fresh PENDING for the same row."""
    local_id = ULID()
    db_session.add(
        OutboxEntry(
            entity_type="widget",
            local_id=local_id,
            op=OP_CREATE,
            status=STATUS_DELIVERED,
        )
    )
    await db_session.flush()
    db_session.add(OutboxEntry(entity_type="widget", local_id=local_id, op=OP_CREATE))
    await db_session.flush()  # must not raise — partial index only covers PENDING

    rows = (
        (await db_session.execute(select(OutboxEntry).where(OutboxEntry.local_id == local_id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2


async def test_sync_state_round_trip(db_session):
    """Sync-state persists cursor + last-reconcile stamp per stream."""
    now = datetime.now(UTC)
    db_session.add(
        SyncState(stream="changes_feed", cursor="2026-06-04T00:00:00Z", last_reconcile_at=now)
    )
    await db_session.flush()

    fetched = (
        await db_session.execute(select(SyncState).where(SyncState.stream == "changes_feed"))
    ).scalar_one()
    assert fetched.cursor == "2026-06-04T00:00:00Z"
    assert fetched.last_reconcile_at is not None


async def test_sync_state_stream_unique(db_session):
    """One row per stream."""
    db_session.add(SyncState(stream="changes_feed"))
    await db_session.flush()
    db_session.add(SyncState(stream="changes_feed"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
