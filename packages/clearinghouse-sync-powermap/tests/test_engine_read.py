"""Engine read-path tests — reconcile, feed, LWW (engine step 4)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import (
    ChangeItem,
    ChangePage,
    EntityPage,
    ObservationResult,
    RetryableClientError,
)
from clearinghouse_sync_powermap.engine import (
    APPLY_KEPT_LOCAL,
    APPLY_SKIPPED,
    APPLY_UPDATED,
    CHANGES_STREAM,
    MAX_RECONCILE_PAGES,
    SyncEngine,
    enrich_fingerprint,
)
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_REJECTED,
    OP_ENRICH,
    OP_UPDATE,
    STATUS_PENDING,
    EnrichFingerprint,
    OutboxEntry,
    SyncState,
)
from clearinghouse_sync_powermap.testing import FakeClient, FakeDescriptor, FakeEntity

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)
#: Far-future clock for drain calls, so an outbox entry (whose next_attempt_at
#: server-defaults to the real wall clock at insert) is always past-due.
FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


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

    applied = await engine.process_feed(db_session, now=NOW)

    assert applied == 1
    assert (await db_session.execute(select(FakeEntity))).scalar_one().name == "FromFeed"
    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == CHANGES_STREAM))
    ).scalar_one()
    # Integer seq cursor is stored as its string form.
    assert state.cursor == "42"


async def test_process_feed_retries_transient_get_changes(db_session, fake_descriptor):
    """usa-wa#89: a 429 on the changes-feed read pauses + resumes rather than aborting
    the tick. The feed is the real-time path; a bare 429 there used to fail the whole
    cycle, leaving the subscription cadence unstamped → re-crawl → re-trip the limiter."""
    pm_id = ULID()
    record = _record("1", "FromFeed", pm_id=pm_id)
    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")

    class FlakyFeedClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._left = 2

        async def get_changes(self, after, limit=100):
            if self._left > 0:
                self._left -= 1
                raise RetryableClientError("PM 429", retry_after=1.5)
            return await super().get_changes(after, limit=limit)

    client = FlakyFeedClient(
        changes_pages=[ChangePage(items=[item], next_after=42)],
        entities={pm_id: record},
    )
    sleeps, sleep = _sleep_recorder()
    engine = SyncEngine([fake_descriptor], client, sleep=sleep)

    applied = await engine.process_feed(db_session, now=NOW)

    assert applied == 1
    assert (await db_session.execute(select(FakeEntity))).scalar_one().name == "FromFeed"
    assert sleeps == [1.5, 1.5]  # honored Retry-After across both transient failures


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

    await engine.process_feed(db_session, now=NOW)

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

    await engine.process_feed(db_session, now=NOW)

    assert client.seen_after == 0


async def test_process_feed_empty_feed_does_not_create_state_row(db_session, fake_descriptor):
    """usa-wa#89 CR: an empty feed (no next_after) has no cursor to persist, so it does
    not materialise a SyncState row — the get-or-create is gated on an advancing cursor."""
    client = FakeClient(changes_pages=[ChangePage(items=[], next_after=None)])
    engine = SyncEngine([fake_descriptor], client)

    applied = await engine.process_feed(db_session, now=NOW)

    assert applied == 0
    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == CHANGES_STREAM))
    ).first()
    assert state is None  # no empty row created


async def test_process_feed_skips_deletes(db_session, fake_descriptor):
    item = ChangeItem(entity_type="fake", entity_id=ULID(), changed_at=NOW, change_kind="deleted")
    client = FakeClient(changes_pages=[ChangePage(items=[item], next_after=9)])
    engine = SyncEngine([fake_descriptor], client)

    applied = await engine.process_feed(db_session, now=NOW)

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


async def test_anchored_cohort_commits_per_page_when_hook_supplied(db_session):
    """With a commit hook the cohort backstop commits after each page, so a large
    cohort never holds one transaction across every PM round-trip (#13 CR). Batch
    size 1 over 3 anchored rows → 3 pages → 3 commits."""
    ids = []
    for i in range(3):
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
    engine = SyncEngine([descriptor], client, sweep_batch_size=1)
    commits = 0

    async def fake_commit():
        nonlocal commits
        commits += 1

    applied = await engine.reconcile(db_session, descriptor, now=NOW, commit=fake_commit)

    assert applied == 3
    assert commits == 3  # one commit per page


async def test_anchored_cohort_no_commit_hook_stays_single_transaction(db_session):
    """No commit hook → the backstop never commits mid-pass (legacy boundary)."""
    pm_id = ULID()
    await _add_anchored(db_session, source_id="x", name="X", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortDescriptor()
    client = FakeClient(
        entities={pm_id: _record("x", "X", pm_id=pm_id, updated_at="2000-01-01T00:00:00Z")}
    )
    engine = SyncEngine([descriptor], client)

    # Must not raise (no commit callback invoked) and must apply the row.
    assert await engine.reconcile(db_session, descriptor, now=NOW) == 1


async def test_anchored_cohort_stamps_last_reconcile_at(db_session):
    descriptor = CohortDescriptor()
    engine = SyncEngine([descriptor], FakeClient())

    await engine.reconcile(db_session, descriptor, now=NOW)

    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == "reconcile:fake"))
    ).scalar_one()
    assert state.last_reconcile_at == NOW


async def test_anchored_cohort_clears_cursor_after_full_pass(db_session):
    """#94: a completed reconcile leaves the keyset checkpoint NULL, so the cadence gate —
    not a stale cursor — governs when the next pass runs."""
    pm_id = ULID()
    await _add_anchored(db_session, source_id="x", name="X", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortDescriptor()
    client = FakeClient(
        entities={pm_id: _record("x", "X", pm_id=pm_id, updated_at="2000-01-01T00:00:00Z")}
    )
    engine = SyncEngine([descriptor], client, sweep_batch_size=1)

    async def fake_commit():
        pass

    await engine.reconcile(db_session, descriptor, now=NOW, commit=fake_commit)

    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == "reconcile:fake"))
    ).scalar_one()
    assert state.cursor is None  # checkpoint cleared on completion


async def test_anchored_cohort_resumes_from_persisted_cursor(db_session):
    """#94: a persisted cursor makes the pass skip rows already processed (id <= cursor), so
    an interrupted reconcile resumes where it stopped instead of re-scanning from the top."""
    rows = []
    for i in range(3):
        pm_id = ULID()
        row = await _add_anchored(
            db_session, source_id=str(i), name=f"N{i}", pm_id=pm_id, updated_at=NOW
        )
        rows.append((row, pm_id))
    rows.sort(key=lambda rp: rp[0].id)  # keyset order is by primary key
    # Pre-seed the checkpoint at the FIRST row's id — a prior pass got that far then stopped.
    db_session.add(
        SyncState(stream="reconcile:fake", last_reconcile_at=NOW, cursor=str(rows[0][0].id))
    )
    await db_session.flush()

    descriptor = CohortDescriptor()
    client = FakeClient(
        entities={
            pm_id: _record(r.source_id, r.name, pm_id=pm_id, updated_at="2000-01-01T00:00:00Z")
            for r, pm_id in rows
        }
    )
    engine = SyncEngine([descriptor], client)

    async def fake_commit():
        pass

    applied = await engine.reconcile(db_session, descriptor, now=NOW, commit=fake_commit)

    fetched = {pm_id for _path, pm_id in client.fetched}
    assert fetched == {rows[1][1], rows[2][1]}  # rows[0] skipped (at/below the cursor)
    assert applied == 2


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


# --- anchored-cohort re-enrich (trigger gap, #34) ----------------------------


class CohortEnrichDescriptor(CohortDescriptor):
    """A cohort producer that also enriches: when the anchored-cohort backstop
    re-fetches OUR row and PM's record lacks an identifier we hold (``needs_enrich``,
    #34 trigger gap) or the carry payload we hold drifts (#34 detection gap), the
    reconcile must re-enqueue an ENRICH so the change self-heals. ``needs_enrich`` is
    True iff PM carries no identifiers at all; the ``names`` carry field tracks
    ``row.name`` so a name change drifts the enrich payload."""

    enrich_identifier_type = "pm_fake_id"

    async def needs_enrich(self, record, row):  # noqa: ARG002
        return not record.get("identifiers")

    async def to_observation(self, session, row):  # noqa: ARG002
        return {
            "identifier_type": "fake_real_id",
            "identifier_value": row.source_id,
            "names": [{"name": row.name, "name_type": "legal"}],
        }


async def _seed_fingerprint(session, descriptor, row):
    """Seed the row's current enrich-payload fingerprint, modelling a row already
    enriched on its current carry payload (so neither #34 trigger fires)."""
    payload = await descriptor.to_enrich_observation(session, row)
    session.add(
        EnrichFingerprint(
            entity_type=descriptor.entity_type,
            local_id=row.id,
            payload_hash=enrich_fingerprint(payload),
        )
    )
    await session.flush()


# PM newer than the local clock (the reconcile steady state — local adopted PM's
# clock on the prior pass), so apply_record takes the PM-wins branch and never
# enqueues an UPDATE. This isolates the re-enrich trigger from the LWW write-back.
_PM_NEWER = "2050-01-01T00:00:00Z"


def _record_with_identifier(source_id, name, *, pm_id, updated_at=_PM_NEWER):
    rec = _record(source_id, name, pm_id=pm_id, updated_at=updated_at)
    rec["identifiers"] = [{"type_slug": "pm_fake_id", "value": str(pm_id)}]
    return rec


async def test_anchored_cohort_enqueues_enrich_when_identifier_missing(db_session):
    """An anchored row whose PM record lacks our identifier → the cohort backstop
    enqueues an ENRICH (closes the #34 trigger gap: identifier-level changes such as
    the #33 legislature anchor-type switch self-heal on the next reconcile)."""
    pm_id = ULID()
    await _add_anchored(db_session, source_id="x", name="X", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortEnrichDescriptor()
    client = FakeClient(entities={pm_id: _record("x", "X", pm_id=pm_id, updated_at=_PM_NEWER)})
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)

    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert entry.op == OP_ENRICH


async def test_anchored_cohort_no_enrich_when_identifier_present_and_fingerprint_current(
    db_session,
):
    """PM holds our identifier (needs_enrich False) AND the row's current enrich
    payload matches its stored fingerprint (no drift) → no ENRICH. The steady-state
    convergence guarantee: a fully-propagated row is a reconcile no-op."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortEnrichDescriptor()
    await _seed_fingerprint(db_session, descriptor, row)
    client = FakeClient(entities={pm_id: _record_with_identifier("x", "X", pm_id=pm_id)})
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)

    assert (await db_session.execute(select(OutboxEntry))).scalars().all() == []


async def test_anchored_cohort_enrich_is_idempotent_across_cycles(db_session):
    """Re-running the cohort backstop while the ENRICH is still PENDING must not
    mint a second entry — the _enqueue blocking-status guard dedups."""
    pm_id = ULID()
    await _add_anchored(db_session, source_id="x", name="X", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortEnrichDescriptor()
    client = FakeClient(entities={pm_id: _record("x", "X", pm_id=pm_id, updated_at=_PM_NEWER)})
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)
    await engine.reconcile(db_session, descriptor, now=NOW)

    entries = (await db_session.execute(select(OutboxEntry))).scalars().all()
    assert len(entries) == 1
    assert entries[0].op == OP_ENRICH


# --- carry-field drift re-enrich (detection gap, #34) ------------------------


def test_enrich_fingerprint_is_stable_and_content_addressed():
    """Equal payloads hash equally regardless of key order; a changed carry field
    (acronym shape fix, added contact label) changes the hash."""
    a = {"identifier_type": "pm_org_id", "names": [{"name": "X"}]}
    b = {"names": [{"name": "X"}], "identifier_type": "pm_org_id"}  # reordered keys
    assert enrich_fingerprint(a) == enrich_fingerprint(b)
    changed = {"identifier_type": "pm_org_id", "names": [{"name": "Y"}]}
    assert enrich_fingerprint(a) != enrich_fingerprint(changed)
    added = {**a, "org_acronyms": [{"acronym": "X"}]}
    assert enrich_fingerprint(a) != enrich_fingerprint(added)


def test_enrich_fingerprint_is_list_order_insensitive():
    """Carry fields are evidence sets — equal evidence in a different order hashes
    equally, so a descriptor that emits a list from a set/dict never thrashes (#34)."""
    a = {"names": [{"name": "X"}, {"name": "Y"}]}
    b = {"names": [{"name": "Y"}, {"name": "X"}]}
    assert enrich_fingerprint(a) == enrich_fingerprint(b)


async def test_anchored_cohort_reenriches_on_carry_drift(db_session):
    """PM already holds our identifier, but the carry payload we hold drifted from the
    stored fingerprint (a shape fix / added field, #31) → the cohort backstop enqueues
    an ENRICH stamped with the new hash, even though needs_enrich is False."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="x", name="Old", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortEnrichDescriptor()
    await _seed_fingerprint(db_session, descriptor, row)  # stamp at name "Old"
    row.name = "New"  # carry payload (names) now drifts from the stamp
    await db_session.flush()
    client = FakeClient(entities={pm_id: _record_with_identifier("x", "New", pm_id=pm_id)})
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)

    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert entry.op == OP_ENRICH
    expected = enrich_fingerprint(await descriptor.to_enrich_observation(db_session, row))
    assert entry.payload_hash == expected


async def test_anchored_cohort_upgrades_blocking_update_to_enrich(db_session):
    """A locally-newer anchored row that ALSO needs enrich: apply_record queues an
    OP_UPDATE (keyed by our real identifier, which PM lacks → duplicate risk), so the
    reconcile upgrades that open UPDATE to an ENRICH (attach-by-pm_id) rather than
    letting the dedup guard silently drop the corrective ENRICH (#34 finding #1)."""
    pm_id = ULID()
    # Local NEWER than PM (NOW vs ancient record) → LWW keeps local + enqueues an
    # OP_UPDATE; PM record carries NO identifier → needs_enrich True.
    await _add_anchored(db_session, source_id="x", name="X", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortEnrichDescriptor()
    client = FakeClient(
        entities={pm_id: _record("x", "X", pm_id=pm_id, updated_at="2000-01-01T00:00:00Z")}
    )
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)

    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()  # exactly one open
    assert entry.op == OP_ENRICH  # upgraded from the blocking UPDATE
    assert entry.payload_hash is not None


async def test_reenrich_converges_after_delivery(db_session):
    """Full loop: drift → ENRICH enqueued → delivered (fingerprint stamped) → the next
    reconcile sees the payload match its fingerprint and enqueues nothing. No loop."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortEnrichDescriptor()
    record = _record_with_identifier("x", "X", pm_id=pm_id)
    client = FakeClient(
        entities={pm_id: record},
        observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}),
    )
    engine = SyncEngine([descriptor], client)

    # First reconcile: no fingerprint yet → drift → ENRICH enqueued.
    await engine.reconcile(db_session, descriptor, now=NOW)
    assert (await db_session.execute(select(OutboxEntry))).scalar_one().op == OP_ENRICH

    # Deliver it → fingerprint stamped.
    await engine.drain_outbox(db_session, now=FUTURE)
    fp = (await db_session.execute(select(EnrichFingerprint))).scalar_one()
    assert fp.local_id == row.id

    # Second reconcile: payload matches the fingerprint → nothing new enqueued.
    await engine.reconcile(db_session, descriptor, now=NOW)
    open_entries = (
        (await db_session.execute(select(OutboxEntry).where(OutboxEntry.status == STATUS_PENDING)))
        .scalars()
        .all()
    )
    assert open_entries == []


async def test_reenrich_fingerprint_updates_on_redelivery(db_session):
    """A second drift+delivery updates the existing fingerprint row in place (not a
    second row), so the stamp always reflects the latest settled payload."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="x", name="A", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortEnrichDescriptor()
    client = FakeClient(
        entities={pm_id: _record_with_identifier("x", "A", pm_id=pm_id)},
        observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}),
    )
    engine = SyncEngine([descriptor], client)

    # First cycle: enrich payload at name "A" → delivered → fingerprint stamped.
    await engine.reconcile(db_session, descriptor, now=NOW)
    await engine.drain_outbox(db_session, now=FUTURE)

    # Drift the carry payload (the engine compares hashes regardless of field
    # provenance), then a second cycle re-enriches and updates the stamp in place.
    client._entities[pm_id] = _record_with_identifier("x", "B", pm_id=pm_id)
    await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)
    await engine.drain_outbox(db_session, now=FUTURE)

    fp = (await db_session.execute(select(EnrichFingerprint))).scalar_one()  # exactly one row
    expected = enrich_fingerprint(await descriptor.to_enrich_observation(db_session, row))
    assert fp.payload_hash == expected


async def test_enrich_rejection_stamps_fingerprint_to_avoid_replay(db_session):
    """A rejected ENRICH still stamps the fingerprint (#34): PM gave a terminal verdict
    on this exact payload, so the reconcile must not re-post the identical payload every
    cycle. The drift trigger re-arms only when the payload changes."""
    pm_id = ULID()
    await _add_anchored(db_session, source_id="x", name="X", pm_id=pm_id, updated_at=NOW)
    descriptor = CohortEnrichDescriptor()
    record = _record_with_identifier("x", "X", pm_id=pm_id)
    client = FakeClient(
        entities={pm_id: record},
        observation_result=ObservationResult(DISPOSITION_REJECTED, None, {"reason": "bad"}),
    )
    engine = SyncEngine([descriptor], client)
    await engine.reconcile(db_session, descriptor, now=NOW)

    await engine.drain_outbox(db_session, now=FUTURE)

    fp = (await db_session.execute(select(EnrichFingerprint))).scalar_one()
    assert fp.payload_hash is not None
    # Re-running the reconcile must NOT enqueue a fresh ENRICH (payload unchanged).
    await engine.reconcile(db_session, descriptor, now=NOW)
    pending = (
        (await db_session.execute(select(OutboxEntry).where(OutboxEntry.status == STATUS_PENDING)))
        .scalars()
        .all()
    )
    assert pending == []


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


# --- merge-orphan anchor self-heal (usa-wa#31 / power-map#235) ----------------


class RematchCohortDescriptor(CohortEnrichDescriptor):
    """A cohort producer that can re-resolve a dead anchor to its PM merge-winner.

    ``rematch_result`` is the winner id (or None for a genuine delete) the
    identifier-only :meth:`rematch_anchor` returns — set per-test."""

    supports_rematch = True


async def test_dead_anchor_reanchors_to_winner_and_enqueues_enrich(db_session):
    """A 404 on an anchored row's re-fetch (PM merged it away) re-resolves the winner
    by identifier, re-points the anchor, and re-enqueues an ENRICH so the carry fields
    re-push to the winner."""
    loser, winner = ULID(), ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    descriptor = RematchCohortDescriptor()
    descriptor.rematch_result = winner
    client = FakeClient(
        entities={winner: _record("x", "Winner", pm_id=winner, updated_at=_PM_NEWER)}
    )
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)

    assert row.pm_fake_id == winner  # re-anchored to the surviving winner
    assert row.deleted_at is None  # not deleted
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert entry.op == OP_ENRICH  # carry fields re-pushed to the winner


async def test_dead_anchor_retires_when_no_winner(db_session):
    """A dead anchor with no surviving identifier winner is a genuine delete → retire
    locally (tombstone), leaving the anchor untouched."""
    loser = ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    descriptor = RematchCohortDescriptor()
    descriptor.rematch_result = None  # identifier miss → genuine delete
    client = FakeClient(entities={})  # loser 404s
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)

    assert row.deleted_at == NOW  # deleted locally
    assert row.pm_fake_id == loser  # anchor left as-is (tombstoned, not re-pointed)


async def test_dead_anchor_unsupported_descriptor_leaves_row(db_session):
    """A descriptor that can't re-match (supports_rematch False) logs and leaves the
    row — never wrongly retires a possibly-merged entity it can't resolve."""
    loser = ULID()
    row = await _add_anchored(db_session, source_id="x", name="Keep", pm_id=loser, updated_at=NOW)
    descriptor = CohortDescriptor()  # supports_rematch False
    client = FakeClient(entities={})  # loser 404s
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)

    assert row.deleted_at is None
    assert row.name == "Keep"
    assert row.pm_fake_id == loser


async def test_process_feed_deleted_heals_our_anchored_row(db_session):
    """A merge `deleted` feed event (carrying merged_into) for a row we anchored routes
    to the heal: re-anchor to the named winner (the timely path; the reconcile 404 is
    the backstop). Post-power-map#235 the merge signal is the explicit merged_into."""
    loser, winner = ULID(), ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    descriptor = RematchCohortDescriptor()
    item = ChangeItem(
        entity_type="fake",
        entity_id=loser,
        changed_at=NOW,
        change_kind="deleted",
        merged_into=winner,
    )
    client = FakeClient(
        changes_pages=[ChangePage(items=[item], next_after=9)],
        entities={winner: _record("x", "Winner", pm_id=winner, updated_at=_PM_NEWER)},
    )
    engine = SyncEngine([descriptor], client)

    await engine.process_feed(db_session, now=NOW)
    await db_session.refresh(row)

    assert row.pm_fake_id == winner


async def test_process_feed_deleted_ignores_unproduced_entity(db_session):
    """A `deleted` event for an entity we never anchored is a no-op (not ours)."""
    descriptor = RematchCohortDescriptor()
    descriptor.rematch_result = ULID()
    item = ChangeItem(entity_type="fake", entity_id=ULID(), changed_at=NOW, change_kind="deleted")
    client = FakeClient(changes_pages=[ChangePage(items=[item], next_after=9)])
    engine = SyncEngine([descriptor], client)

    applied = await engine.process_feed(db_session, now=NOW)

    assert applied == 0
    assert (await db_session.execute(select(FakeEntity))).first() is None


class ArchivalCohortDescriptor(CohortDescriptor):
    """Cohort descriptor that mirrors PM ``archived_at`` on import, like the real
    org/person/role/assignment descriptors — so reconcile exercises the archived
    axis (usa-wa#42)."""

    async def upsert_from_pm(self, session, record, existing=None):
        row = await super().upsert_from_pm(session, record, existing)
        self.mirror_archival(row, record)
        await session.flush()
        return row


async def test_sweep_excludes_deleted_rows(db_session):
    """A deleted row (terminal tombstone) is never re-created by the un-anchored sweep."""
    row = await _add_entity(db_session, source_id="r", name="Deleted")  # anchor IS NULL
    row.deleted_at = NOW
    await db_session.flush()
    descriptor = CohortDescriptor()
    engine = SyncEngine([descriptor], FakeClient())

    enqueued = await engine.sweep_unanchored(db_session, descriptor)

    assert enqueued == 0
    assert (await db_session.execute(select(OutboxEntry))).scalars().all() == []


async def test_anchored_cohort_excludes_deleted_rows(db_session):
    """A deleted row (dead anchor) is not re-fetched by the anchored-cohort reconcile."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="r", name="R", pm_id=pm_id, updated_at=NOW)
    row.deleted_at = NOW
    await db_session.flush()
    descriptor = CohortDescriptor()
    client = FakeClient(entities={pm_id: _record("r", "R", pm_id=pm_id)})
    engine = SyncEngine([descriptor], client)

    applied = await engine.reconcile(db_session, descriptor, now=NOW)

    assert applied == 0
    assert client.fetched == []  # deleted row never re-fetched


async def test_anchored_cohort_refetches_archived_rows(db_session):
    """An *archived* row keeps a live anchor, so the anchored-cohort reconcile MUST
    re-fetch it (unlike a deleted row) — the backstop that recovers a dropped feed
    event (usa-wa#42)."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="a", name="A", pm_id=pm_id, updated_at=NOW)
    row.archived_at = NOW  # archived locally, anchor still live
    await db_session.flush()
    descriptor = ArchivalCohortDescriptor()
    client = FakeClient(entities={pm_id: _record("a", "A", pm_id=pm_id)})
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)

    assert pm_id in [f[1] for f in client.fetched]  # archived row IS re-fetched (live anchor)


async def test_anchored_cohort_recovers_dropped_unarchive(db_session):
    """The #42 fix: a dropped un-archive feed event is recovered by reconcile. The
    local row is still archived; PM has since un-archived (``archived_at`` null); the
    reconcile re-fetch clears the local tombstone and revives the row."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="a", name="A", pm_id=pm_id, updated_at=NOW)
    row.archived_at = NOW  # local still archived (the un-archive event was dropped)
    await db_session.flush()
    descriptor = ArchivalCohortDescriptor()
    # PM record carries no archived_at (un-archived) and a newer clock so LWW applies.
    client = FakeClient(entities={pm_id: _record("a", "A", pm_id=pm_id, updated_at=_PM_NEWER)})
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)

    assert row.archived_at is None  # tombstone cleared → row revived
    assert row.deleted_at is None


async def test_dead_anchor_many_to_one_merge_retires_duplicate(db_session):
    """Two of our rows merged into one PM winner: the first re-anchors; the second
    finds the winner already held locally → retire the orphan rather than mint a
    duplicate anchor (which would crash the next anchor-keyed local_match). (#36 CR #1)"""
    loser_a, loser_b, winner = ULID(), ULID(), ULID()
    row_a = await _add_anchored(db_session, source_id="a", name="A", pm_id=loser_a, updated_at=NOW)
    row_b = await _add_anchored(db_session, source_id="b", name="B", pm_id=loser_b, updated_at=NOW)
    descriptor = RematchCohortDescriptor()
    descriptor.rematch_result = winner  # both rows resolve to the same winner
    client = FakeClient(
        entities={winner: _record("a", "Winner", pm_id=winner, updated_at=_PM_NEWER)}
    )
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row_a)
    await db_session.refresh(row_b)

    anchors = {row_a.pm_fake_id, row_b.pm_fake_id}
    deleted = [r for r in (row_a, row_b) if r.deleted_at is not None]
    assert winner in anchors  # exactly one row re-anchored to the winner
    assert len(deleted) == 1  # the other deleted as a duplicate orphan
    assert deleted[0].pm_fake_id != winner  # the orphan never got the duplicate anchor


async def test_dead_anchor_unsupported_warns_once_across_cycles(db_session, caplog):
    """An unsupported-rematch dead anchor (person/role until power-map#235) 404s every
    reconcile, but the WARNING fires once per row per process, not every cycle (#36 CR #2)."""
    loser = ULID()
    await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    descriptor = CohortDescriptor()  # supports_rematch False
    client = FakeClient(entities={})  # loser 404s every pass
    engine = SyncEngine([descriptor], client)

    with caplog.at_level("WARNING"):
        await engine.reconcile(db_session, descriptor, now=NOW)
        await engine.reconcile(db_session, descriptor, now=NOW)

    assert sum(r.msg == "dead_anchor_unhealed" for r in caplog.records) == 1


async def test_dead_anchor_archived_then_404_promotes_to_deleted(db_session):
    """A non-rematch row that was *archived* and now 404s is a settled delete (PM
    enforces archive-before-hard-delete), so the 404 backstop promotes archived →
    deleted rather than leaving it to 404 every cycle (usa-wa#42 CR)."""
    loser = ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    row.archived_at = NOW  # archived locally; PM has since hard-deleted it
    await db_session.flush()
    descriptor = CohortDescriptor()  # supports_rematch False
    client = FakeClient(entities={})  # loser 404s
    engine = SyncEngine([descriptor], client)

    await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)

    assert row.deleted_at == NOW  # promoted to terminal tombstone
    assert row.archived_at is None  # archived axis cleared (delete supersedes)


# --- power-map#235 merged_into: generic, deterministic re-anchor (usa-wa#37) -----


async def test_process_feed_merged_into_reanchors_unsupported_descriptor(db_session):
    """A `deleted` event carrying `merged_into` re-anchors ANY entity type to the
    named winner — no identifier re-match, so even a descriptor that can't rematch
    (person/role/assignment) heals generically (usa-wa#37 / power-map#235)."""
    loser, winner = ULID(), ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    descriptor = CohortDescriptor()  # supports_rematch False — would have stayed inert
    item = ChangeItem(
        entity_type="fake",
        entity_id=loser,
        changed_at=NOW,
        change_kind="deleted",
        merged_into=winner,
    )
    client = FakeClient(
        changes_pages=[ChangePage(items=[item], next_after=9)],
        entities={winner: _record("x", "Winner", pm_id=winner, updated_at=_PM_NEWER)},
    )
    engine = SyncEngine([descriptor], client)

    await engine.process_feed(db_session, now=NOW)
    await db_session.refresh(row)

    assert row.pm_fake_id == winner  # re-anchored from the explicit winner id
    assert row.deleted_at is None


async def test_process_feed_merged_into_preferred_over_rematch(db_session):
    """When `merged_into` is present the engine trusts it and does NOT consult the
    descriptor's identifier re-match — the explicit PM signal wins over the heuristic."""
    loser, winner, wrong = ULID(), ULID(), ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    descriptor = RematchCohortDescriptor()
    descriptor.rematch_result = wrong  # would mis-anchor if consulted
    item = ChangeItem(
        entity_type="fake",
        entity_id=loser,
        changed_at=NOW,
        change_kind="deleted",
        merged_into=winner,
    )
    client = FakeClient(
        changes_pages=[ChangePage(items=[item], next_after=9)],
        entities={winner: _record("x", "Winner", pm_id=winner, updated_at=_PM_NEWER)},
    )
    engine = SyncEngine([descriptor], client)

    await engine.process_feed(db_session, now=NOW)
    await db_session.refresh(row)

    assert row.pm_fake_id == winner  # the merged_into winner, not rematch_result


async def test_process_feed_genuine_delete_retires_any_type(db_session):
    """A `deleted` event WITHOUT `merged_into` is a deterministic genuine delete: retire
    the row even for a descriptor that can't re-match (the merge/delete ambiguity that
    blocked this before power-map#235 is gone)."""
    loser = ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    descriptor = CohortDescriptor()  # supports_rematch False
    item = ChangeItem(entity_type="fake", entity_id=loser, changed_at=NOW, change_kind="deleted")
    client = FakeClient(changes_pages=[ChangePage(items=[item], next_after=9)])
    engine = SyncEngine([descriptor], client)

    await engine.process_feed(db_session, now=NOW)
    await db_session.refresh(row)

    assert row.deleted_at == NOW  # genuine delete → tombstoned
    assert row.pm_fake_id == loser  # anchor left as-is


async def test_process_feed_bare_delete_org_uses_rematch_backstop(db_session):
    """A bare `deleted` (no merged_into) on a rematch-capable descriptor (org) keeps the
    #36 identifier backstop ahead of any retire: a merge whose event lacked merged_into
    (PM gap / pre-power-map#235 backlog) still re-anchors rather than wrongly retiring an
    org permanently out of the sweep+reconcile (CR #1)."""
    loser, winner = ULID(), ULID()
    row = await _add_anchored(db_session, source_id="x", name="X", pm_id=loser, updated_at=NOW)
    descriptor = RematchCohortDescriptor()  # supports_rematch True
    descriptor.rematch_result = winner  # identifier winner survives
    item = ChangeItem(entity_type="fake", entity_id=loser, changed_at=NOW, change_kind="deleted")
    client = FakeClient(
        changes_pages=[ChangePage(items=[item], next_after=9)],
        entities={winner: _record("x", "Winner", pm_id=winner, updated_at=_PM_NEWER)},
    )
    engine = SyncEngine([descriptor], client)

    await engine.process_feed(db_session, now=NOW)
    await db_session.refresh(row)

    assert row.pm_fake_id == winner  # re-anchored via the backstop, not deleted
    assert row.deleted_at is None


class NoTombstoneDescriptor(FakeDescriptor):
    """A feed descriptor with neither re-match nor a retirement column — the defensive
    fallback path: a dead anchor it can't resolve and can't tombstone is left in place."""

    deleted_column = None
    supports_rematch = False


async def test_process_feed_bare_delete_no_tombstone_leaves_row(db_session, caplog):
    """A bare `deleted` for a descriptor that can't re-match AND has no tombstone column
    falls through to warn-and-leave — never silently drops the row (engine.py else arm)."""
    loser = ULID()
    row = await _add_anchored(db_session, source_id="x", name="Keep", pm_id=loser, updated_at=NOW)
    descriptor = NoTombstoneDescriptor()
    item = ChangeItem(entity_type="fake", entity_id=loser, changed_at=NOW, change_kind="deleted")
    client = FakeClient(changes_pages=[ChangePage(items=[item], next_after=9)])
    engine = SyncEngine([descriptor], client)

    with caplog.at_level("WARNING"):
        await engine.process_feed(db_session, now=NOW)
    await db_session.refresh(row)

    assert row.pm_fake_id == loser  # left in place
    assert row.deleted_at is None
    assert any(r.msg == "dead_anchor_unhealed" for r in caplog.records)


async def test_process_feed_merged_into_many_to_one_retires_duplicate(db_session):
    """Two of our rows merged into one winner, both surfacing via merged_into: the
    first re-anchors, the second finds the winner already held → retire the orphan
    rather than mint a duplicate anchor (the #36 guard generalizes to the hint path)."""
    loser_a, loser_b, winner = ULID(), ULID(), ULID()
    row_a = await _add_anchored(db_session, source_id="a", name="A", pm_id=loser_a, updated_at=NOW)
    row_b = await _add_anchored(db_session, source_id="b", name="B", pm_id=loser_b, updated_at=NOW)
    descriptor = CohortDescriptor()  # supports_rematch False — hint-driven only
    items = [
        ChangeItem(
            entity_type="fake",
            entity_id=loser_a,
            changed_at=NOW,
            change_kind="deleted",
            merged_into=winner,
        ),
        ChangeItem(
            entity_type="fake",
            entity_id=loser_b,
            changed_at=NOW,
            change_kind="deleted",
            merged_into=winner,
        ),
    ]
    client = FakeClient(
        changes_pages=[ChangePage(items=items, next_after=9)],
        entities={winner: _record("a", "Winner", pm_id=winner, updated_at=_PM_NEWER)},
    )
    engine = SyncEngine([descriptor], client)

    await engine.process_feed(db_session, now=NOW)
    await db_session.refresh(row_a)
    await db_session.refresh(row_b)

    anchors = {row_a.pm_fake_id, row_b.pm_fake_id}
    deleted = [r for r in (row_a, row_b) if r.deleted_at is not None]
    assert winner in anchors  # exactly one re-anchored
    assert len(deleted) == 1  # the other deleted as a duplicate orphan
    assert deleted[0].pm_fake_id != winner


async def test_anchored_cohort_requires_now(db_session):
    """The cohort backstop self-heals (retire/heal stamps) → it must be given a real
    clock; a None now is a programming error, not a silent wall-clock fallback (#36 CR #5)."""
    descriptor = CohortDescriptor()
    engine = SyncEngine([descriptor], FakeClient())

    with pytest.raises(ValueError, match="requires an explicit now"):
        await engine.reconcile(db_session, descriptor, now=None)


# --- #85: bounded transient-read retry in the anchored-cohort backstop -----------


class _FlakyClient(FakeClient):
    """FakeClient whose get_entity raises RetryableClientError N times first."""

    def __init__(self, *, failures, retry_after=None, **kwargs):
        super().__init__(**kwargs)
        self._failures = failures
        self._retry_after = retry_after

    async def get_entity(self, read_path, pm_id):
        if self._failures > 0:
            self._failures -= 1
            raise RetryableClientError("PM 429", retry_after=self._retry_after)
        return await super().get_entity(read_path, pm_id)


def _sleep_recorder():
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleeps, _sleep


async def test_anchored_cohort_retries_transient_read_honoring_retry_after(db_session):
    """A 429 inside the cohort crawl pauses (Retry-After) and resumes — it must not
    abort the reconcile (the #88 miniature #84 loop: cycle-fatal 429 → stamp rollback
    → immediate full re-crawl → re-trip the limiter)."""
    pm_id = ULID()
    row = await _add_anchored(
        db_session,
        source_id="x",
        name="StaleName",
        pm_id=pm_id,
        updated_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    descriptor = CohortDescriptor()
    client = _FlakyClient(
        failures=2,
        retry_after=3.0,
        entities={
            pm_id: _record("x", "CuratedName", pm_id=pm_id, updated_at="2026-06-07T00:00:00Z")
        },
    )
    sleeps, sleep = _sleep_recorder()
    engine = SyncEngine([descriptor], client, sleep=sleep)

    applied = await engine.reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(row)

    assert applied == 1
    assert row.name == "CuratedName"  # the crawl resumed and recovered the edit
    assert sleeps == [3.0, 3.0]  # honored Retry-After, not the fallback schedule


async def test_anchored_cohort_retry_falls_back_to_schedule(db_session):
    """No Retry-After header → the small foreground backoff schedule (1, 2, 4, 8)."""
    pm_id = ULID()
    await _add_anchored(
        db_session,
        source_id="x",
        name="A",
        pm_id=pm_id,
        updated_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    descriptor = CohortDescriptor()
    client = _FlakyClient(
        failures=3,
        entities={pm_id: _record("x", "A", pm_id=pm_id, updated_at="2000-01-01T00:00:00Z")},
    )
    sleeps, sleep = _sleep_recorder()
    engine = SyncEngine([descriptor], client, sleep=sleep)

    await engine.reconcile(db_session, descriptor, now=NOW)

    assert sleeps == [1.0, 2.0, 4.0]


async def test_anchored_cohort_read_retry_budget_exhausted_reraises(db_session):
    """A persistent 429/5xx exhausts the bounded budget and re-raises — the
    per-descriptor boundary (usa-wa#85) contains it; no infinite in-loop stall."""
    pm_id = ULID()
    await _add_anchored(
        db_session,
        source_id="x",
        name="A",
        pm_id=pm_id,
        updated_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    descriptor = CohortDescriptor()
    client = _FlakyClient(failures=99)
    sleeps, sleep = _sleep_recorder()
    engine = SyncEngine([descriptor], client, sleep=sleep)

    with pytest.raises(RetryableClientError):
        await engine.reconcile(db_session, descriptor, now=NOW)

    assert sleeps == [1.0, 2.0, 4.0, 8.0]  # the whole budget, then re-raise
