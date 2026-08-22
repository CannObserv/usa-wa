"""A REJECTED entry a later PENDING entry replaced is not an operator's to-do (usa-wa#258).

REJECTED is terminal on purpose — a blind retry just repeats the rejection, so the engine
excludes it from re-enqueue and the sidecar alerts when the count *rises*. That design has a
gap: when the underlying defect is fixed in code and the cohort is re-enqueued, the old
rejections stay REJECTED forever. The pile then holds the count static, which is exactly the
state the rise-alert reads as "nothing new to see", and a genuinely new rejection hides in it.

usa-wa's own case: 1,788 rejections across two waves (#255 null identifier_type, #257
unknown_identifier_type), every one of them already re-enqueued as PENDING.
"""

from __future__ import annotations

import pytest
from ulid import ULID

from clearinghouse_sync_powermap.engine.maintenance import supersede_stale_rejections
from clearinghouse_sync_powermap.models import (
    OP_CREATE,
    STATUS_DELIVERED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
    STATUS_UNAVAILABLE,
    OutboxEntry,
)

pytestmark = pytest.mark.db


async def _entry(session, *, status, local_id=None, entity_type="person", op=OP_CREATE):
    entry = OutboxEntry(entity_type=entity_type, local_id=local_id or ULID(), op=op, status=status)
    session.add(entry)
    await session.flush()
    return entry


async def test_a_rejection_with_an_open_pending_sibling_is_superseded(db_session):
    local_id = ULID()
    rejected = await _entry(db_session, status=STATUS_REJECTED, local_id=local_id)
    await _entry(db_session, status=STATUS_PENDING, local_id=local_id)

    assert await supersede_stale_rejections(db_session) == 1
    await db_session.refresh(rejected)
    assert rejected.status == STATUS_SUPERSEDED


async def test_a_rejection_with_no_replacement_is_left_alone(db_session):
    """The whole point of the REJECTED backlog: these still need a data fix."""
    rejected = await _entry(db_session, status=STATUS_REJECTED)

    assert await supersede_stale_rejections(db_session) == 0
    await db_session.refresh(rejected)
    assert rejected.status == STATUS_REJECTED


async def test_the_sibling_must_match_on_entity_type_as_well_as_id(db_session):
    """``local_id`` is a ULID and unique in practice, but the outbox keys on the PAIR — a
    same-id row of another entity type is a different delivery, not a replacement."""
    local_id = ULID()
    rejected = await _entry(db_session, status=STATUS_REJECTED, local_id=local_id)
    await _entry(db_session, status=STATUS_PENDING, local_id=local_id, entity_type="assignment")

    assert await supersede_stale_rejections(db_session) == 0
    await db_session.refresh(rejected)
    assert rejected.status == STATUS_REJECTED


@pytest.mark.parametrize("sibling", [STATUS_DELIVERED, STATUS_UNAVAILABLE])
async def test_only_an_OPEN_sibling_supersedes(db_session, sibling):
    """DELIVERED is a *different* row's settled history and UNAVAILABLE is itself unsettled;
    neither means "this rejection has been re-attempted". Only PENDING does."""
    local_id = ULID()
    rejected = await _entry(db_session, status=STATUS_REJECTED, local_id=local_id)
    await _entry(db_session, status=sibling, local_id=local_id)

    assert await supersede_stale_rejections(db_session) == 0
    await db_session.refresh(rejected)
    assert rejected.status == STATUS_REJECTED


async def test_it_is_idempotent(db_session):
    local_id = ULID()
    await _entry(db_session, status=STATUS_REJECTED, local_id=local_id)
    await _entry(db_session, status=STATUS_PENDING, local_id=local_id)

    assert await supersede_stale_rejections(db_session) == 1
    assert await supersede_stale_rejections(db_session) == 0


async def test_dry_run_reports_without_writing(db_session):
    """A bulk status flip over a production backlog gets a preview."""
    local_id = ULID()
    rejected = await _entry(db_session, status=STATUS_REJECTED, local_id=local_id)
    await _entry(db_session, status=STATUS_PENDING, local_id=local_id)

    assert await supersede_stale_rejections(db_session, dry_run=True) == 1
    await db_session.refresh(rejected)
    assert rejected.status == STATUS_REJECTED
