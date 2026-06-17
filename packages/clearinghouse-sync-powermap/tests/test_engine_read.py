"""Engine read-path tests — reconcile, feed, LWW (engine step 4)."""

from datetime import UTC, datetime

from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import ChangeItem, ChangePage, EntityPage
from clearinghouse_sync_powermap.engine import (
    APPLY_KEPT_LOCAL,
    APPLY_SKIPPED,
    APPLY_UPDATED,
    CHANGES_STREAM,
    MAX_RECONCILE_PAGES,
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


async def test_apply_record_adopts_remote_clock(db_session, fake_descriptor):
    """Engine hoist: after upsert, the row's LWW clock mirrors PM's updated_at
    (not now()), so the next reconcile sees parity — no spurious write-back.
    Generic over descriptors; covers insert and the PM-wins overwrite."""
    engine = SyncEngine([fake_descriptor], FakeClient())

    await engine.apply_record(
        db_session, fake_descriptor, _record("1", "Alpha", updated_at="2040-03-02T01:00:00Z")
    )
    row = (
        await db_session.execute(select(FakeEntity).where(FakeEntity.source_id == "1"))
    ).scalar_one()
    assert row.updated_at == datetime(2040, 3, 2, 1, 0, tzinfo=UTC)

    # PM-wins overwrite carries a newer PM clock → adopted, not re-stamped now().
    await engine.apply_record(
        db_session, fake_descriptor, _record("1", "Beta", updated_at="2041-04-03T02:00:00Z")
    )
    await db_session.flush()  # persist the adoption, then reload to prove it stuck
    await db_session.refresh(row)
    assert row.name == "Beta"
    assert row.updated_at == datetime(2041, 4, 3, 2, 0, tzinfo=UTC)


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


async def test_lww_local_newer_captures_pm_anchor(db_session, fake_descriptor):
    """Keeping the newer local row still captures the PM anchor we just learned."""
    await _add_entity(db_session, source_id="1", name="FreshLocal")  # unanchored
    engine = SyncEngine([fake_descriptor], FakeClient())
    pm_id = ULID()

    await engine.apply_record(
        db_session,
        fake_descriptor,
        _record("1", "StalePM", pm_id=pm_id, updated_at="2000-01-01T00:00:00Z"),
    )

    row = (await db_session.execute(select(FakeEntity))).scalar_one()
    assert row.name == "FreshLocal"  # local field kept
    assert row.pm_fake_id == pm_id  # but anchor captured


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


async def test_reconcile_noop_when_disabled(db_session):
    """A ``reconcile_mode="none"`` descriptor skips the full-list backstop entirely —
    even with records waiting (usa-wa#13)."""

    class NoReconcile(FakeDescriptor):
        reconcile_mode = "none"

    descriptor = NoReconcile()
    client = FakeClient(entity_pages=[EntityPage([_record("1", "X")], None)])
    engine = SyncEngine([descriptor], client)

    assert await engine.reconcile(db_session, descriptor, now=None) == 0
    assert (await db_session.execute(select(FakeEntity))).first() is None


async def test_apply_record_skips_when_update_only_descriptor_declines(db_session):
    """An update-only descriptor whose upsert returns None (record never produced
    locally) yields APPLY_SKIPPED — not a misreported APPLY_INSERTED."""

    class UpdateOnlyDescriptor(FakeDescriptor):
        async def upsert_from_pm(self, session, record, existing=None):  # noqa: ARG002
            return None  # never mirror an unknown record

    descriptor = UpdateOnlyDescriptor()
    engine = SyncEngine([descriptor], FakeClient())

    outcome = await engine.apply_record(db_session, descriptor, _record("ghost", "Ghost"))

    assert outcome == APPLY_SKIPPED
    assert (await db_session.execute(select(FakeEntity))).first() is None


async def test_process_feed_applies_and_advances_cursor(db_session, fake_descriptor):
    pm_id = ULID()
    record = _record("1", "FromFeed", pm_id=pm_id)
    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")
    client = FakeClient(
        changes_pages=[ChangePage(items=[item], next_after=42)],
        entities={pm_id: record},
    )
    engine = SyncEngine([fake_descriptor], client)

    applied = await engine.process_feed(db_session)

    assert applied == 1
    assert (await db_session.execute(select(FakeEntity))).scalar_one().name == "FromFeed"
    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == CHANGES_STREAM))
    ).scalar_one()
    # Integer seq cursor is stored as its string form.
    assert state.cursor == "42"


async def test_process_feed_reads_stored_integer_cursor_as_after(db_session, fake_descriptor):
    """A previously-stored integer cursor is passed back as the ``after`` seq."""

    class CapturingClient(FakeClient):
        seen_after: int | None = None

        async def get_changes(self, after, limit=100):
            self.seen_after = after
            return ChangePage(items=[], next_after=after)

    db_session.add(SyncState(stream=CHANGES_STREAM, cursor="7"))
    await db_session.flush()
    client = CapturingClient()
    engine = SyncEngine([fake_descriptor], client)

    await engine.process_feed(db_session)

    assert client.seen_after == 7


async def test_process_feed_resets_stale_timestamp_cursor_to_zero(db_session, fake_descriptor):
    """A leftover timestamp cursor from the pre-#203 scheme is not a valid ``after``;
    the engine resets it to 0 ("from the start") rather than crashing the feed."""

    class CapturingClient(FakeClient):
        seen_after: int | None = -1

        async def get_changes(self, after, limit=100):
            self.seen_after = after
            return ChangePage(items=[], next_after=None)

    db_session.add(SyncState(stream=CHANGES_STREAM, cursor="2026-06-04T00:00:00Z"))
    await db_session.flush()
    client = CapturingClient()
    engine = SyncEngine([fake_descriptor], client)

    await engine.process_feed(db_session)

    assert client.seen_after == 0


async def test_process_feed_skips_deletes(db_session, fake_descriptor):
    item = ChangeItem(entity_type="fake", entity_id=ULID(), changed_at=NOW, change_kind="deleted")
    client = FakeClient(changes_pages=[ChangePage(items=[item], next_after=9)])
    engine = SyncEngine([fake_descriptor], client)

    applied = await engine.process_feed(db_session)

    assert applied == 0
    assert (await db_session.execute(select(FakeEntity))).first() is None


# --- read path: anchored-cohort reconcile backstop (usa-wa#13) ---------------


class CohortDescriptor(FakeDescriptor):
    """A cohort-only producer: the bounded anchored-cohort backstop, not full-list."""

    reconcile_mode = "anchored_cohort"


async def _add_anchored(session, *, source_id, name, pm_id, updated_at):
    row = FakeEntity(source="wsl", source_id=source_id, name=name)
    row.pm_fake_id = pm_id
    row.updated_at = updated_at
    session.add(row)
    await session.flush()
    return row


async def test_anchored_cohort_fetches_only_anchored_rows(db_session):
    """The anchored-cohort backstop GETs only rows whose anchor IS NOT NULL — never
    the un-anchored rows, and never PM's global list (no list_entities call)."""
    anchored_a = ULID()
    anchored_b = ULID()
    await _add_anchored(db_session, source_id="a", name="A", pm_id=anchored_a, updated_at=NOW)
    await _add_anchored(db_session, source_id="b", name="B", pm_id=anchored_b, updated_at=NOW)
    await _add_entity(db_session, source_id="u", name="Unanchored")  # anchor IS NULL

    descriptor = CohortDescriptor()
    client = FakeClient(
        entities={
            anchored_a: _record("a", "A", pm_id=anchored_a, updated_at="2000-01-01T00:00:00Z"),
            anchored_b: _record("b", "B", pm_id=anchored_b, updated_at="2000-01-01T00:00:00Z"),
        }
    )
    engine = SyncEngine([descriptor], client)

    applied = await engine.reconcile(db_session, descriptor, now=NOW)

    assert applied == 2
    fetched_ids = {pm_id for _path, pm_id in client.fetched}
    assert fetched_ids == {anchored_a, anchored_b}
    # PM's global list endpoint must never be paged for a cohort producer.
    assert client._entity_pages == []  # untouched (list_entities never popped)


async def test_anchored_cohort_recovers_dropped_feed_edit_via_lww(db_session):
    """A curation edit whose feed event was dropped is recovered on the next cohort
    pass: PM's newer record overwrites the stale local row under LWW."""
    pm_id = ULID()
    # Local row is stale: old clock, old name (its feed bump never arrived).
    row = await _add_anchored(
        db_session,
        source_id="x",
        name="StaleName",
        pm_id=pm_id,
        updated_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    descriptor = CohortDescriptor()
    client = FakeClient(
        entities={
            pm_id: _record("x", "CuratedName", pm_id=pm_id, updated_at="2026-06-07T00:00:00Z")
        }
    )
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)

    assert row.name == "CuratedName"


async def test_anchored_cohort_paginates_in_bounded_batches(db_session):
    """The cohort query is keyset-paged (bounded), so it terminates and re-fetches
    every anchored row even when the cohort exceeds one batch."""
    ids = []
    for i in range(5):
        pm_id = ULID()
        ids.append(pm_id)
        await _add_anchored(db_session, source_id=str(i), name=f"N{i}", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortDescriptor()
    client = FakeClient(
        entities={
            pm_id: _record(str(i), f"N{i}", pm_id=pm_id, updated_at="2000-01-01T00:00:00Z")
            for i, pm_id in enumerate(ids)
        }
    )
    # Force multiple pages: batch size 2 over 5 anchored rows → 3 pages.
    engine = SyncEngine([descriptor], client, sweep_batch_size=2)

    applied = await engine.reconcile(db_session, descriptor, now=NOW)

    assert applied == 5
    assert {pm_id for _path, pm_id in client.fetched} == set(ids)


async def test_anchored_cohort_stamps_last_reconcile_at(db_session):
    descriptor = CohortDescriptor()
    engine = SyncEngine([descriptor], FakeClient())

    await engine.reconcile(db_session, descriptor, now=NOW)

    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == "reconcile:fake"))
    ).scalar_one()
    assert state.last_reconcile_at == NOW


async def test_anchored_cohort_skips_missing_pm_record(db_session):
    """A 404 on re-fetch (PM record gone between anchor and pass) is skipped, not
    fatal — the row is left as-is and the pass continues."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="x", name="Keep", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortDescriptor()
    client = FakeClient(entities={})  # get_entity returns None
    engine = SyncEngine([descriptor], client)

    applied = await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)

    assert applied == 0
    assert row.name == "Keep"
    assert client.fetched == [("/api/v1/fakes", pm_id)]


# --- full_list reconcile page bound (sibling firehose guard, #6) -------------


async def test_full_list_reconcile_bounded_pages(db_session, fake_descriptor, caplog):
    """A misbehaving PM that always returns a non-None cursor must not spin the
    full_list reconcile forever — the page bound trips and breaks with a warning."""

    class NeverEndingClient(FakeClient):
        async def list_entities(self, read_path, params=None):
            # Always advertise more pages (one record each) → would loop forever.
            return EntityPage(records=[_record("1", "Spin")], cursor="more")

    engine = SyncEngine([fake_descriptor], NeverEndingClient())

    with caplog.at_level("WARNING"):
        applied = await engine.reconcile(db_session, fake_descriptor, now=NOW)

    assert applied == MAX_RECONCILE_PAGES  # one record per page, bounded
    assert any(r.msg == "reconcile_pagination_bound_exceeded" for r in caplog.records)
