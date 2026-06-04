"""Engine write-path tests — sweep + outbox worker (engine step 3)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import ObservationResult
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_NEW,
    DISPOSITION_REJECTED,
    OP_CREATE,
    STATUS_DELIVERED,
    STATUS_PENDING,
    STATUS_REJECTED,
    OutboxEntry,
)
from clearinghouse_sync_powermap.testing import FakeClient, FakeDescriptor, FakeEntity

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)


def _raise_conn_error(payload):
    raise ConnectionError("PM unreachable")


async def _add_entity(session, *, source_id, name="x", anchor=None):
    row = FakeEntity(source="wsl", source_id=source_id, name=name, pm_fake_id=anchor)
    session.add(row)
    await session.flush()
    return row


async def test_sweep_enqueues_unanchored(db_session, fake_descriptor):
    await _add_entity(db_session, source_id="1")
    await _add_entity(db_session, source_id="2")
    await _add_entity(db_session, source_id="3", anchor=ULID())  # already anchored
    engine = SyncEngine([fake_descriptor], FakeClient())

    count = await engine.sweep_unanchored(db_session, fake_descriptor)

    assert count == 2
    entries = (await db_session.execute(select(OutboxEntry))).scalars().all()
    assert {e.op for e in entries} == {OP_CREATE}
    assert all(e.status == STATUS_PENDING for e in entries)


async def test_sweep_is_idempotent(db_session, fake_descriptor):
    await _add_entity(db_session, source_id="1")
    engine = SyncEngine([fake_descriptor], FakeClient())

    assert await engine.sweep_unanchored(db_session, fake_descriptor) == 1
    assert await engine.sweep_unanchored(db_session, fake_descriptor) == 0  # open entry exists


async def test_drain_anchors_on_new(db_session, fake_descriptor):
    row = await _add_entity(db_session, source_id="1")
    pm_id = ULID()
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert len(touched) == 1
    assert touched[0].status == STATUS_DELIVERED
    assert touched[0].last_disposition == DISPOSITION_NEW
    assert row.pm_fake_id == pm_id
    assert client.posted == [
        ("/api/v1/fakes/observations", {"source": "wsl", "source_id": "1", "name": "x"})
    ]


async def test_drain_anchors_on_auto_attached(db_session, fake_descriptor):
    row = await _add_entity(db_session, source_id="1")
    pm_id = ULID()
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    await engine.drain_outbox(db_session, now=NOW)

    assert row.pm_fake_id == pm_id


async def test_drain_rejected_marks_terminal(db_session, fake_descriptor):
    row = await _add_entity(db_session, source_id="1")
    client = FakeClient(
        observation_result=ObservationResult(DISPOSITION_REJECTED, None, {"error": "dupe"})
    )
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched[0].status == STATUS_REJECTED
    assert "dupe" in touched[0].last_error
    assert row.pm_fake_id is None  # unresolved, awaits operator


async def test_drain_transient_error_backs_off(db_session, fake_descriptor):
    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=_raise_conn_error)
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    touched = await engine.drain_outbox(db_session, now=NOW)

    entry = touched[0]
    assert entry.status == STATUS_PENDING  # still queued
    assert entry.attempts == 1
    assert entry.next_attempt_at == NOW + timedelta(seconds=60)
    assert "PM unreachable" in entry.last_error


async def test_drain_respects_next_attempt_at(db_session, fake_descriptor):
    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    entry.next_attempt_at = NOW + timedelta(hours=1)  # not yet due
    await db_session.flush()

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched == []
    assert client.posted == []


async def test_drain_skips_dormant_type(db_session, fake_descriptor):
    """A write-disabled descriptor's entries are left untouched, not spun on."""

    class DormantDescriptor(FakeDescriptor):
        entity_type = "fake_dormant"
        write_enabled = False

    dormant = DormantDescriptor()
    db_session.add(OutboxEntry(entity_type="fake_dormant", local_id=ULID(), op=OP_CREATE))
    await db_session.flush()
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    engine = SyncEngine([dormant], client)

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched == []
    assert client.posted == []
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert entry.status == STATUS_PENDING
