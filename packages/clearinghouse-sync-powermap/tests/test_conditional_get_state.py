"""ConditionalGetState model (usa-wa#160): the per-row PM ETag store the reconcile
sends as If-None-Match to earn a cheap 304 instead of a full-body re-fetch."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from clearinghouse_sync_powermap.models import ConditionalGetState


async def test_conditional_get_state_roundtrip(db_session):
    lid = ULID()
    db_session.add(ConditionalGetState(entity_type="person", local_id=lid, detail_etag='"abc-1"'))
    await db_session.flush()

    row = (
        await db_session.execute(
            select(ConditionalGetState).where(ConditionalGetState.local_id == lid)
        )
    ).scalar_one()
    assert row.detail_etag == '"abc-1"'


async def test_conditional_get_state_unique_per_row(db_session):
    lid = ULID()
    db_session.add(ConditionalGetState(entity_type="person", local_id=lid, detail_etag='"x"'))
    await db_session.flush()
    db_session.add(ConditionalGetState(entity_type="person", local_id=lid, detail_etag='"y"'))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_conditional_get_state_stores_row_watermark(db_session):
    """usa-wa#247: alongside the validator, the store records the local row's clock as of
    that full fetch. The reconcile compares the row's current clock against it to tell a
    locally-edited row (which must bypass the 304 so the LWW local-newer branch can run)
    from a quiet one. Nullable: a validator stored before #247 has no watermark, and the
    reconcile treats unknown as advanced."""
    lid = ULID()
    stamp = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    db_session.add(
        ConditionalGetState(
            entity_type="role_assignment", local_id=lid, detail_etag='"abc-1"', row_updated_at=stamp
        )
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(ConditionalGetState).where(ConditionalGetState.local_id == lid)
        )
    ).scalar_one()
    assert row.row_updated_at == stamp


async def test_conditional_get_state_row_watermark_defaults_null(db_session):
    """A row written by the pre-#247 store carries no watermark — the column is nullable so
    the migration needs no backfill, and NULL reads as 'unknown, verify' at the reconcile."""
    lid = ULID()
    db_session.add(ConditionalGetState(entity_type="person", local_id=lid, detail_etag='"x"'))
    await db_session.flush()

    row = (
        await db_session.execute(
            select(ConditionalGetState).where(ConditionalGetState.local_id == lid)
        )
    ).scalar_one()
    assert row.row_updated_at is None
