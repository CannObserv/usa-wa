"""Engine read-path tests — reconcile, feed, LWW (engine step 4)."""

from datetime import UTC, datetime

from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import ChangeItem, ChangePage, EntityPage
from clearinghouse_sync_powermap.engine import (
    APPLY_KEPT_LOCAL,
    APPLY_UPDATED,
    CHANGES_STREAM,
    SyncEngine,
)
from clearinghouse_sync_powermap.models import OP_UPDATE, STATUS_PENDING, OutboxEntry, SyncState
from clearinghouse_sync_powermap.testing import FakeClient, FakeDescriptor, FakeEntity

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)


def _record(source_id, name, *, pm_id=None, updated_at="2050-01-01T00:00:00Z"):
    return {
        "id": str(pm_id or ULID()),
        "source": "wsl",
        "source_id": source_id,
        "name": name,
        "updated_at": updated_at,
    }


async def _add_entity(session, *, source_id, name):
    row = FakeEntity(source="wsl", source_id=source_id, name=name)
    session.add(row)
    await session.flush()
    return row


async def test_reconcile_inserts_new_records(db_session, fake_descriptor):
    pages = [EntityPage(records=[_record("1", "Alpha"), _record("2", "Beta")], cursor=None)]
    engine = SyncEngine([fake_descriptor], FakeClient(entity_pages=pages))

    applied = await engine.reconcile(db_session, fake_descriptor)

    assert applied == 2
    rows = (await db_session.execute(select(FakeEntity))).scalars().all()
    assert {r.name for r in rows} == {"Alpha", "Beta"}
    assert all(r.pm_fake_id is not None for r in rows)


async def test_reconcile_paginates(db_session, fake_descriptor):
    pages = [
        EntityPage(records=[_record("1", "Alpha")], cursor="c1"),
        EntityPage(records=[_record("2", "Beta")], cursor=None),
    ]
    engine = SyncEngine([fake_descriptor], FakeClient(entity_pages=pages))

    applied = await engine.reconcile(db_session, fake_descriptor)

    assert applied == 2


async def test_reconcile_stamps_last_reconcile_at(db_session, fake_descriptor):
    engine = SyncEngine([fake_descriptor], FakeClient(entity_pages=[EntityPage([], None)]))

    await engine.reconcile(db_session, fake_descriptor, now=NOW)

    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == "reconcile:fake"))
    ).scalar_one()
    assert state.last_reconcile_at == NOW


async def test_lww_pm_newer_overwrites(db_session, fake_descriptor):
    await _add_entity(db_session, source_id="1", name="OldLocal")
    engine = SyncEngine([fake_descriptor], FakeClient())

    # PM record dated far in the future → PM wins.
    outcome = await engine.apply_record(
        db_session, fake_descriptor, _record("1", "NewPM", updated_at="2099-01-01T00:00:00Z")
    )

    assert outcome == APPLY_UPDATED
    row = (await db_session.execute(select(FakeEntity))).scalar_one()
    assert row.name == "NewPM"


async def test_lww_local_newer_keeps_local_and_enqueues_update(db_session, fake_descriptor):
    await _add_entity(db_session, source_id="1", name="FreshLocal")
    engine = SyncEngine([fake_descriptor], FakeClient())

    # PM record is ancient → local is newer.
    outcome = await engine.apply_record(
        db_session, fake_descriptor, _record("1", "StalePM", updated_at="2000-01-01T00:00:00Z")
    )

    assert outcome == APPLY_KEPT_LOCAL
    row = (await db_session.execute(select(FakeEntity))).scalar_one()
    assert row.name == "FreshLocal"  # not overwritten
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert entry.op == OP_UPDATE
    assert entry.status == STATUS_PENDING


async def test_lww_local_newer_no_enqueue_when_write_disabled(db_session):
    class ReadOnlyDescriptor(FakeDescriptor):
        write_enabled = False

    descriptor = ReadOnlyDescriptor()
    await _add_entity(db_session, source_id="1", name="FreshLocal")
    engine = SyncEngine([descriptor], FakeClient())

    outcome = await engine.apply_record(
        db_session, descriptor, _record("1", "StalePM", updated_at="2000-01-01T00:00:00Z")
    )

    assert outcome == APPLY_KEPT_LOCAL
    assert (await db_session.execute(select(OutboxEntry))).first() is None  # nothing to push


async def test_process_feed_applies_and_advances_cursor(db_session, fake_descriptor):
    pm_id = ULID()
    record = _record("1", "FromFeed", pm_id=pm_id)
    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")
    client = FakeClient(
        changes_pages=[ChangePage(items=[item], cursor="cursor-2")],
        entities={pm_id: record},
    )
    engine = SyncEngine([fake_descriptor], client)

    applied = await engine.process_feed(db_session)

    assert applied == 1
    assert (await db_session.execute(select(FakeEntity))).scalar_one().name == "FromFeed"
    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == CHANGES_STREAM))
    ).scalar_one()
    assert state.cursor == "cursor-2"


async def test_process_feed_skips_deletes(db_session, fake_descriptor):
    item = ChangeItem(entity_type="fake", entity_id=ULID(), changed_at=NOW, change_kind="deleted")
    client = FakeClient(changes_pages=[ChangePage(items=[item], cursor="c")])
    engine = SyncEngine([fake_descriptor], client)

    applied = await engine.process_feed(db_session)

    assert applied == 0
    assert (await db_session.execute(select(FakeEntity))).first() is None
