"""python -m usa_wa_sync_powermap.supersede_rejections CLI surface (#258).

The maintenance query itself is covered in the portable
``clearinghouse-sync-powermap`` suite; this pins the harness wiring — that the handler
takes the harness session, honours ``--dry-run``, and reports the count as a counter.
"""

import pytest
from ulid import ULID

from clearinghouse_core.job import JobContext
from clearinghouse_sync_powermap.models import (
    OP_CREATE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
    OutboxEntry,
)
from usa_wa_sync_powermap import supersede_rejections as cli

pytestmark = pytest.mark.db


def _ctx(db_session, *, dry_run: bool) -> JobContext:
    import argparse

    return JobContext(
        name=cli.JOB_SLUG,
        args=argparse.Namespace(),
        session=db_session,
        session_factory=None,
        dry_run=dry_run,
    )


async def _replaced_pair(db_session):
    local_id = ULID()
    rejected = OutboxEntry(
        entity_type="person", local_id=local_id, op=OP_CREATE, status=STATUS_REJECTED
    )
    db_session.add(rejected)
    db_session.add(
        OutboxEntry(entity_type="person", local_id=local_id, op=OP_CREATE, status=STATUS_PENDING)
    )
    await db_session.flush()
    return rejected


async def test_the_handler_supersedes_and_counts(db_session):
    rejected = await _replaced_pair(db_session)

    result = await cli._supersede_job(_ctx(db_session, dry_run=False))

    assert result.counters == {"superseded": 1}
    await db_session.refresh(rejected)
    assert rejected.status == STATUS_SUPERSEDED


async def test_dry_run_counts_without_writing(db_session):
    rejected = await _replaced_pair(db_session)

    result = await cli._supersede_job(_ctx(db_session, dry_run=True))

    assert result.counters == {"superseded": 1}
    await db_session.refresh(rejected)
    assert rejected.status == STATUS_REJECTED
