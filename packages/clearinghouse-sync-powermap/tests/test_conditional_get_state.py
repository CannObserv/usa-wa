"""ConditionalGetState model (usa-wa#160): the per-row PM ETag store the reconcile
sends as If-None-Match to earn a cheap 304 instead of a full-body re-fetch."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from clearinghouse_sync_powermap.models import ConditionalGetState


async def test_conditional_get_state_roundtrip(db_session):
    lid = ULID()
    db_session.add(
        ConditionalGetState(
            entity_type="person", local_id=lid, detail_etag='"abc-1"', events_etag='"abc-events-1"'
        )
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(ConditionalGetState).where(ConditionalGetState.local_id == lid)
        )
    ).scalar_one()
    assert row.detail_etag == '"abc-1"' and row.events_etag == '"abc-events-1"'


async def test_conditional_get_state_unique_per_row(db_session):
    lid = ULID()
    db_session.add(ConditionalGetState(entity_type="person", local_id=lid, detail_etag='"x"'))
    await db_session.flush()
    db_session.add(ConditionalGetState(entity_type="person", local_id=lid, detail_etag='"y"'))
    with pytest.raises(IntegrityError):
        await db_session.flush()
