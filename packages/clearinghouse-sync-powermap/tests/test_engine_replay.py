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
from clearinghouse_sync_powermap.models import SyncState
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
