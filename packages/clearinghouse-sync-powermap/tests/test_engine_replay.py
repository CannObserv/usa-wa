"""Changes-feed replay backstop tests (usa-wa#159).

The replay re-reads a trailing window of the changes feed (``high_water − margin``)
each cycle to re-cover PM's at-least-once concurrent-commit skip (power-map#387),
replacing the O(cohort) anchored scan as the primary dropped-event backstop. This
module covers the floor arithmetic + margin validation (step 2) and the
``replay_from_floor`` engine method + horizon fall-off detection (step 3).
"""

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import ChangeItem, ChangePage
from clearinghouse_sync_powermap.engine import (
    CHANGES_STREAM,
    DEFAULT_REPLAY_MARGIN,
    REPLAY_STREAM,
    SyncEngine,
    _replay_floor,
)
from clearinghouse_sync_powermap.models import ConditionalGetState, SyncState
from clearinghouse_sync_powermap.testing import FakeClient, FakeEntity

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)


def _record(source_id, name, *, pm_id, updated_at):
    return {
        "id": str(pm_id),
        "source": "wsl",
        "source_id": source_id,
        "name": name,
        "updated_at": updated_at,
    }


async def _add_anchored(session, *, source_id, name, pm_id):
    row = FakeEntity(source="wsl", source_id=source_id, name=name, pm_fake_id=pm_id)
    session.add(row)
    await session.flush()
    return row


class ReplayClient(FakeClient):
    """Serves preset pages keyed by the ``after`` seq the replay requests, and records
    every ``after`` so a test can assert the trailing window it re-read."""

    def __init__(self, *, pages_by_after, **kwargs):
        super().__init__(**kwargs)
        self._pages_by_after = dict(pages_by_after)
        self.replay_afters: list[int | None] = []

    async def get_changes(self, after, limit=100):
        self.replay_afters.append(after)
        return self._pages_by_after.get(after, ChangePage(items=[], next_after=after))


@pytest.mark.parametrize(
    ("high_water", "margin", "expected"),
    [
        (None, DEFAULT_REPLAY_MARGIN, 0),  # fresh stream → replay whole retained window
        (5, 10_000, 0),  # margin ≥ high_water → clamp at 0, not negative
        (10_000, 10_000, 0),  # exact → 0
        (50_000, 10_000, 40_000),  # trailing window
        (50_000, 0, 50_000),  # zero margin → re-read nothing below high_water
    ],
)
def test_replay_floor_arithmetic(high_water, margin, expected):
    assert _replay_floor(high_water, margin) == expected


def test_engine_rejects_negative_replay_margin(fake_descriptor):
    with pytest.raises(ValueError, match="replay_margin must be >= 0"):
        SyncEngine([fake_descriptor], FakeClient(), replay_margin=-1)


def test_engine_accepts_zero_replay_margin(fake_descriptor):
    # 0 is the "replay off" degenerate, not an error (floor == high_water).
    SyncEngine([fake_descriptor], FakeClient(), replay_margin=0)


async def test_replay_heals_skipped_commit_row(db_session, fake_descriptor):
    """The core usa-wa#159 case: a seq the live feed skipped (concurrent-commit skip)
    is re-delivered by the trailing re-read from ``high_water − margin`` and heals the
    stale local row — without advancing the live feed cursor."""
    pm_id = ULID()
    await _add_anchored(db_session, source_id="1", name="Stale", pm_id=pm_id)
    db_session.add(SyncState(stream=CHANGES_STREAM, cursor="50000"))  # live high-water
    await db_session.flush()

    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")
    client = ReplayClient(
        pages_by_after={40000: ChangePage(items=[item], next_after=45000)},
        entities={pm_id: _record("1", "Healed", pm_id=pm_id, updated_at="2099-01-01T00:00:00Z")},
    )
    engine = SyncEngine([fake_descriptor], client)  # default margin 10_000

    result = await engine.replay_from_floor(db_session, now=NOW)

    # Floor = 50000 − 10000; first read starts there.
    assert result.floor == 40000
    assert client.replay_afters[0] == 40000
    assert result.applied == 1 and result.healed == 1 and result.fell_off is False
    assert (await db_session.execute(select(FakeEntity))).scalar_one().name == "Healed"
    # Live feed cursor untouched; replay stream stamped its own cadence.
    live = await db_session.scalar(
        select(SyncState.cursor).where(SyncState.stream == CHANGES_STREAM)
    )
    assert live == "50000"
    replay = (
        await db_session.execute(select(SyncState).where(SyncState.stream == REPLAY_STREAM))
    ).scalar_one()
    assert replay.last_reconcile_at == NOW


async def test_replay_is_idempotent_on_current_rows(db_session, fake_descriptor):
    """Re-reading an already-current row is an LWW no-op: processed, but not healed."""
    pm_id = ULID()
    await _add_anchored(db_session, source_id="1", name="Fresh", pm_id=pm_id)
    db_session.add(SyncState(stream=CHANGES_STREAM, cursor="50000"))
    await db_session.flush()

    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")
    client = ReplayClient(
        pages_by_after={40000: ChangePage(items=[item], next_after=45000)},
        # PM record OLDER than the local row → local wins → no change.
        entities={pm_id: _record("1", "StalePM", pm_id=pm_id, updated_at="2000-01-01T00:00:00Z")},
    )
    engine = SyncEngine([fake_descriptor], client)

    result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.applied == 1 and result.healed == 0
    assert (await db_session.execute(select(FakeEntity))).scalar_one().name == "Fresh"


async def test_replay_converged_window_reports_zero_healed(db_session, fake_descriptor):
    """usa-wa#212 headline: a replayed item whose row is at LWW parity with PM — the
    converged steady state every applied item settles into, since ``adopt_remote_clock``
    mirrors PM's clock — is processed but NOT healed. Previously the tie fell into the
    PM-wins branch and was misreported as ``updated``, so ``replay_healed`` re-counted
    the whole window every pass (~3,290 phantom heals) and could never reach zero."""
    pm_id = ULID()
    ts = "2026-01-01T00:00:00Z"
    row = FakeEntity(
        source="wsl",
        source_id="1",
        name="Same",
        pm_fake_id=pm_id,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(SyncState(stream=CHANGES_STREAM, cursor="50000"))
    await db_session.flush()

    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")
    client = ReplayClient(
        pages_by_after={40000: ChangePage(items=[item], next_after=45000)},
        # The exact record already applied: same values, same clock (parity).
        entities={pm_id: _record("1", "Same", pm_id=pm_id, updated_at=ts)},
    )
    engine = SyncEngine([fake_descriptor], client)

    result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.applied == 1 and result.healed == 0
    await db_session.refresh(row)
    assert row.name == "Same"
    assert row.updated_at == datetime(2026, 1, 1, tzinfo=UTC)


async def test_replay_flags_horizon_fall_off(db_session, fake_descriptor, caplog):
    """A floor below PM's oldest-retained min_seq (power-map#388) means the pruned
    [floor, min_seq) slice can't be replayed → fell_off True + a warning, so the caller
    falls back to a full cohort scan for that gap."""
    pm_id = ULID()
    await _add_anchored(db_session, source_id="1", name="Stale", pm_id=pm_id)
    db_session.add(SyncState(stream=CHANGES_STREAM, cursor="50000"))
    await db_session.flush()

    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")
    client = ReplayClient(
        pages_by_after={40000: ChangePage(items=[item], next_after=45000, min_seq=48000)},
        entities={pm_id: _record("1", "Healed", pm_id=pm_id, updated_at="2099-01-01T00:00:00Z")},
    )
    engine = SyncEngine([fake_descriptor], client)

    with caplog.at_level(logging.WARNING):
        result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.fell_off is True
    assert "powermap_replay_horizon_fell_off" in caplog.text


async def test_replay_skips_when_feed_unbootstrapped(db_session, fake_descriptor):
    """usa-wa#159 CR-1: with no live changes_feed cursor (fresh/empty deploy, high_water
    0), replay skips entirely — it must NOT read the feed (which would re-read the whole
    retained window duplicating the live bootstrap) nor trip a spurious fall-off. It still
    stamps REPLAY_STREAM so the cadence applies."""
    item = ChangeItem(entity_type="fake", entity_id=ULID(), changed_at=NOW, change_kind="updated")
    # A page that WOULD trip fall-off (floor 0 < min_seq 5000) if replay read it.
    client = ReplayClient(
        pages_by_after={0: ChangePage(items=[item], next_after=100, min_seq=5000)},
    )
    engine = SyncEngine([fake_descriptor], client)  # no CHANGES_STREAM row → high_water 0

    result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.high_water == 0 and result.floor == 0
    assert result.applied == 0 and result.healed == 0 and result.fell_off is False
    assert client.replay_afters == []  # never queried PM
    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == REPLAY_STREAM))
    ).scalar_one()
    assert state.last_reconcile_at == NOW  # stamped → cadence applies uniformly


# --- usa-wa#160 residual: conditional GET on the replay fetch path ---------------
#
# The gating decision (issue #160, second half): ``_apply_feed_page`` is shared by the
# live feed and this replay, and only replay re-reads items it has already applied — so
# only replay can earn a 304. The live feed is left byte-identical (a feed item *means*
# PM changed the entity → a conditional GET there is a guaranteed 200 plus two wasted
# local lookups). These tests pin both sides of that split.


def _replay_page(pm_id, *, after=40000, next_after=45000):
    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")
    return {after: ChangePage(items=[item], next_after=next_after)}


async def _bootstrap_high_water(session, cursor="50000"):
    session.add(SyncState(stream=CHANGES_STREAM, cursor=cursor))
    await session.flush()


async def test_replay_sends_stored_etag_and_304_skips_apply(db_session, fake_descriptor):
    """usa-wa#160: a replayed item whose stored ETag still matches costs a 304 — no body,
    no apply — and counts as skipped, not applied."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="1", name="Local", pm_id=pm_id)
    db_session.add(
        ConditionalGetState(entity_type="fake", local_id=row.id, detail_etag='"stored-1"')
    )
    await _bootstrap_high_water(db_session)

    client = ReplayClient(
        pages_by_after=_replay_page(pm_id),
        # PM would win on the clock if the body were fetched — the 304 must pre-empt it.
        entities={pm_id: _record("1", "PMName", pm_id=pm_id, updated_at="2099-01-01T00:00:00Z")},
        not_modified_ids={pm_id},
    )
    engine = SyncEngine([fake_descriptor], client)

    result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.applied == 0 and result.healed == 0
    await db_session.refresh(row)
    assert row.name == "Local"  # no apply on a 304
    assert client.conditional_fetched[0][2] == '"stored-1"'  # sent the stored validator
    assert engine.conditional_get_stats == (1, 0)  # (skipped, fetched)


async def test_replay_200_applies_and_stores_fresh_etag(db_session, fake_descriptor):
    """No stored validator → an unconditional-equivalent conditional read; the 200 applies
    and persists PM's fresh ETag so the next trailing pass can 304."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="1", name="Stale", pm_id=pm_id)
    await _bootstrap_high_water(db_session)

    client = ReplayClient(
        pages_by_after=_replay_page(pm_id),
        entities={pm_id: _record("1", "Healed", pm_id=pm_id, updated_at="2099-01-01T00:00:00Z")},
        entity_etags={pm_id: '"fresh-9"'},
    )
    engine = SyncEngine([fake_descriptor], client)

    result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.applied == 1 and result.healed == 1
    assert client.conditional_fetched[0][2] is None  # nothing stored yet
    await db_session.refresh(row)
    assert row.name == "Healed"
    stored = await db_session.scalar(
        select(ConditionalGetState.detail_etag).where(ConditionalGetState.local_id == row.id)
    )
    assert stored == '"fresh-9"'
    assert engine.conditional_get_stats == (0, 1)


async def test_replay_stores_etag_for_a_newly_inserted_row(db_session, fake_descriptor):
    """An item we hold no anchor for yet (a first sighting inside the window) has no local
    id to key the store on *before* the fetch — resolve it after the insert so the row does
    not stay unconditional forever."""
    pm_id = ULID()
    await _bootstrap_high_water(db_session)

    client = ReplayClient(
        pages_by_after=_replay_page(pm_id),
        entities={pm_id: _record("1", "Fresh", pm_id=pm_id, updated_at="2099-01-01T00:00:00Z")},
        entity_etags={pm_id: '"fresh-1"'},
    )
    engine = SyncEngine([fake_descriptor], client)

    result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.applied == 1
    row = (await db_session.execute(select(FakeEntity))).scalar_one()
    stored = await db_session.scalar(
        select(ConditionalGetState.detail_etag).where(ConditionalGetState.local_id == row.id)
    )
    assert stored == '"fresh-1"'


async def test_replay_404_still_skips_the_item(db_session, fake_descriptor):
    """A conditional 404 (PM record gone) must behave exactly as the unconditional one did:
    the item is skipped, the local row untouched, and nothing is stored."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="1", name="Local", pm_id=pm_id)
    await _bootstrap_high_water(db_session)

    client = ReplayClient(pages_by_after=_replay_page(pm_id))  # no entity → record None
    engine = SyncEngine([fake_descriptor], client)

    result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.applied == 0 and result.healed == 0
    await db_session.refresh(row)
    assert row.name == "Local"
    assert (await db_session.execute(select(ConditionalGetState))).first() is None
    assert engine.conditional_get_stats == (0, 0)  # neither skipped nor fetched


async def test_replay_deleted_item_never_reaches_the_conditional_fetch(db_session, fake_descriptor):
    """The delete/merge/heal branch runs *before* the fetch and must stay that way — a
    ``deleted`` item costs no conditional PM read at all."""
    pm_id = ULID()
    winner = ULID()
    row = await _add_anchored(db_session, source_id="1", name="Loser", pm_id=pm_id)
    await _bootstrap_high_water(db_session)

    item = ChangeItem(
        entity_type="fake",
        entity_id=pm_id,
        changed_at=NOW,
        change_kind="deleted",
        merged_into=winner,
    )
    client = ReplayClient(pages_by_after={40000: ChangePage(items=[item], next_after=45000)})
    engine = SyncEngine([fake_descriptor], client)

    await engine.replay_from_floor(db_session, now=NOW)

    await db_session.refresh(row)
    assert row.pm_fake_id == winner  # re-anchored by the heal path
    assert client.conditional_fetched == []  # the delete branch short-circuited first


async def test_replay_kill_switch_uses_unconditional_fetch(db_session, fake_descriptor):
    """``conditional_get_enabled=False`` → the plain fetch on replay too, no ETag store."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="1", name="Stale", pm_id=pm_id)
    await _bootstrap_high_water(db_session)

    client = ReplayClient(
        pages_by_after=_replay_page(pm_id),
        entities={pm_id: _record("1", "Healed", pm_id=pm_id, updated_at="2099-01-01T00:00:00Z")},
        entity_etags={pm_id: '"fresh-9"'},
    )
    engine = SyncEngine([fake_descriptor], client, conditional_get_enabled=False)

    result = await engine.replay_from_floor(db_session, now=NOW)

    assert result.applied == 1
    await db_session.refresh(row)
    assert row.name == "Healed"
    assert client.conditional_fetched == []
    assert (await db_session.execute(select(ConditionalGetState))).first() is None
    assert engine.conditional_get_stats == (0, 0)


async def test_live_feed_fetch_stays_unconditional(db_session, fake_descriptor):
    """The gating decision, pinned: the live changes feed does NOT go conditional. A feed
    item means PM changed the entity, so a conditional GET there is a guaranteed 200 plus a
    wasted anchor + ETag lookup per item."""
    pm_id = ULID()
    row = await _add_anchored(db_session, source_id="1", name="Stale", pm_id=pm_id)
    db_session.add(
        ConditionalGetState(entity_type="fake", local_id=row.id, detail_etag='"stored-1"')
    )
    await db_session.flush()

    item = ChangeItem(entity_type="fake", entity_id=pm_id, changed_at=NOW, change_kind="updated")
    client = FakeClient(
        changes_pages=[ChangePage(items=[item], next_after=42)],
        entities={pm_id: _record("1", "FromFeed", pm_id=pm_id, updated_at="2099-01-01T00:00:00Z")},
        not_modified_ids={pm_id},  # would 304 if the feed sent the stored validator
    )
    engine = SyncEngine([fake_descriptor], client)

    applied = await engine.process_feed(db_session, now=NOW)

    assert applied == 1  # full body applied, no 304 short-circuit
    await db_session.refresh(row)
    assert row.name == "FromFeed"
    assert client.conditional_fetched == []
    assert engine.conditional_get_stats == (0, 0)
