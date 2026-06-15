"""Sidecar round-trip + cycle-isolation tests (step 8).

The write round-trip is the MVP increment end to end: a locally-minted,
un-anchored jurisdiction is swept into the outbox, observed to PM, and anchored
from the disposition — all in one ``tick``. Uses the savepointed ``db_session``
(real Postgres) + the in-memory FakeClient (no network).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_sync_powermap.client import (
    ChangeItem,
    ChangePage,
    DiscoveredEntity,
    ObservationResult,
)
from clearinghouse_sync_powermap.engine import APPLY_KEPT_LOCAL, SyncEngine
from clearinghouse_sync_powermap.models import (
    DISPOSITION_NEW,
    STATUS_DELIVERED,
    OutboxEntry,
    SyncState,
)
from clearinghouse_sync_powermap.subscriptions import DiscoverySpec, SubscriptionReconciler
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_sync_powermap.descriptors import (
    JurisdictionDescriptor,
    OrganizationDescriptor,
    RoleDescriptor,
)
from usa_wa_sync_powermap.sidecar import SUBSCRIPTIONS_STREAM, Sidecar

NOW = datetime(2099, 1, 1, tzinfo=UTC)

SPEC = DiscoverySpec(root_type="jurisdiction", root_id="usa-wa", follow=["lineage"])


def _sidecar(client):
    descriptor = JurisdictionDescriptor()
    engine = SyncEngine([descriptor], client)
    # session_factory unused by tick(); run_cycle is covered separately.
    return Sidecar(engine, [descriptor], session_factory=lambda: None), descriptor


def _sidecar_with_reconciler(client, *, cadence=timedelta(hours=1)):
    descriptor = JurisdictionDescriptor()
    engine = SyncEngine([descriptor], client)
    reconciler = SubscriptionReconciler(client, engine, SPEC)
    sidecar = Sidecar(
        engine,
        [descriptor],
        session_factory=lambda: None,
        reconciler=reconciler,
        subscription_backstop_cadence=cadence,
    )
    return sidecar, descriptor


@pytest.fixture
async def state_type(db_session) -> JurisdictionType:
    jt = JurisdictionType(slug="state", display_name="State")
    db_session.add(jt)
    await db_session.flush()
    return jt


async def test_tick_write_roundtrip_anchors_jurisdiction(db_session, state_type):
    """Un-anchored local jurisdiction → swept → observed → anchored, in one tick."""
    row = Jurisdiction(
        slug="usa-wa", name="Washington", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add(row)
    await db_session.flush()
    pm_id = ULID()
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, pm_id, {}))
    sidecar, _ = _sidecar(client)

    await sidecar.tick(db_session, now=NOW)

    assert row.pm_jurisdiction_id == pm_id
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert entry.status == STATUS_DELIVERED
    observe_path, payload = client.posted[0]
    assert observe_path == "/api/v1/jurisdictions/observations"
    assert payload["identifier_type"] == "jur_slug"
    assert payload["identifier_value"] == "usa-wa"


async def test_tick_read_feed_upserts_from_pm(db_session, state_type):
    """A PM jurisdiction change off the subscription-filtered feed is cached + anchored
    (the full-list reconcile backstop is retired — usa-wa#10)."""
    pm_id = ULID()
    record = {
        "id": str(pm_id),
        "slug": "usa-wa-county-king",
        "name": "King County",
        "type": {"id": str(ULID()), "slug": "state", "display_name": "State"},
        "recorded_at": "2022-01-01T00:00:00Z",
        "valid_from": "2022-01-01T00:00:00Z",
        "valid_until": None,
        "superseded_at": None,
        "updated_at": "2026-06-07T00:00:00Z",
    }
    item = ChangeItem(
        entity_type="jurisdiction", entity_id=pm_id, changed_at=NOW, change_kind="updated"
    )
    client = FakeClient(
        changes_pages=[ChangePage(items=[item], next_after=5)],
        entities={pm_id: record},
    )
    sidecar, _ = _sidecar(client)

    await sidecar.tick(db_session, now=NOW)

    cached = (
        await db_session.execute(
            select(Jurisdiction).where(Jurisdiction.slug == "usa-wa-county-king")
        )
    ).scalar_one()
    assert cached.name == "King County"
    assert cached.pm_jurisdiction_id == pm_id


async def test_reconciled_jurisdiction_does_not_reenqueue_writeback(db_session, state_type):
    """Regression (go-live 403 loop): a PM-imported jurisdiction, re-read on the
    next reconcile, must NOT be judged locally-newer and pushed back to PM. With
    PM's updated_at preserved locally, LWW sees parity → PM wins → no outbox."""
    descriptor = JurisdictionDescriptor()
    engine = SyncEngine([descriptor], FakeClient())
    record = {
        "id": str(ULID()),
        "slug": "usa-wa",
        "name": "Washington",
        "type": {"id": str(ULID()), "slug": "state", "display_name": "State"},
        "recorded_at": "2022-01-01T00:00:00Z",
        "valid_from": "2022-01-01T00:00:00Z",
        "valid_until": None,
        "superseded_at": None,
        "updated_at": "2026-06-01T00:00:00Z",
    }

    await engine.apply_record(db_session, descriptor, record)  # first reconcile: import
    outcome = await engine.apply_record(db_session, descriptor, record)  # next reconcile

    assert outcome != APPLY_KEPT_LOCAL
    entries = (await db_session.execute(select(OutboxEntry))).scalars().all()
    assert entries == []


# --- run_cycle isolation (CR #13) ----------------------------------------------


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


async def test_run_cycle_commits_on_success():
    session = _FakeSession()
    sidecar = Sidecar(engine=None, descriptors=[], session_factory=lambda: session)

    async def _ok(s, *, now):
        return None

    sidecar.tick = _ok
    await sidecar.run_cycle()

    assert session.committed and not session.rolled_back


async def test_run_cycle_isolates_and_rolls_back_on_error():
    session = _FakeSession()
    sidecar = Sidecar(engine=None, descriptors=[], session_factory=lambda: session)

    async def _boom(s, *, now):
        raise RuntimeError("poison cycle")

    sidecar.tick = _boom
    await sidecar.run_cycle()  # must NOT raise

    assert session.rolled_back and not session.committed


async def test_full_list_reconcile_retired_for_all_descriptors(db_session):
    """usa-wa#10: the unfiltered full-list reconcile backstop is retired for every
    descriptor — jurisdictions now ride the subscription-filtered feed + discovery,
    producers were already feed-only."""
    sidecar, _ = _sidecar(FakeClient())
    assert await sidecar._reconcile_due(db_session, JurisdictionDescriptor(), NOW) is False
    assert await sidecar._reconcile_due(db_session, OrganizationDescriptor(), NOW) is False
    assert await sidecar._reconcile_due(db_session, RoleDescriptor(), NOW) is False


# --- subscription backstop ------------------------------------------------------


async def test_backstop_runs_before_feed_and_registers_new(db_session, state_type):
    """The in-loop backstop discovers + registers a new WA jurisdiction, and the feed
    pull that follows in the same tick delivers its record into the cache."""
    pm_id = ULID()
    record = {
        "id": str(pm_id),
        "slug": "usa-wa-county-pierce",
        "name": "Pierce County",
        "type": {"id": str(ULID()), "slug": "state", "display_name": "State"},
        "recorded_at": "2022-01-01T00:00:00Z",
        "valid_from": "2022-01-01T00:00:00Z",
        "valid_until": None,
        "superseded_at": None,
        "updated_at": "2026-06-07T00:00:00Z",
    }
    client = FakeClient(
        discovered=[
            DiscoveredEntity(
                entity_type="jurisdiction", entity_id=pm_id, display_name="Pierce", hops_from_root=1
            )
        ],
        subscribed=[],
        entities={pm_id: record},
    )
    sidecar, _ = _sidecar_with_reconciler(client)

    await sidecar.tick(db_session, now=NOW)

    assert client.added == [[pm_id]]  # backstop registered the discovered id
    # And the cache holds it (backfilled by the reconciler, then feed is a no-op).
    cached = (
        await db_session.execute(
            select(Jurisdiction).where(Jurisdiction.slug == "usa-wa-county-pierce")
        )
    ).scalar_one()
    assert cached.pm_jurisdiction_id == pm_id
    # The backstop stamped its stream so it waits a cadence before re-running.
    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == SUBSCRIPTIONS_STREAM))
    ).scalar_one()
    assert state.last_reconcile_at == NOW


async def test_backstop_respects_cadence(db_session):
    """A second tick within the cadence does not re-run discovery."""
    client = FakeClient(discovered=[], subscribed=[])
    sidecar, _ = _sidecar_with_reconciler(client, cadence=timedelta(hours=1))

    await sidecar.tick(db_session, now=NOW)
    await sidecar.tick(db_session, now=NOW + timedelta(minutes=30))

    assert len(client.discover_calls) == 1  # only the first tick ran the backstop

    await sidecar.tick(db_session, now=NOW + timedelta(hours=2))
    assert len(client.discover_calls) == 2  # cadence elapsed → ran again


async def test_tick_without_reconciler_skips_backstop(db_session, state_type):
    """A sidecar built without a reconciler (e.g. legacy wiring) never runs the
    backstop and does not touch the subscriptions stream."""
    sidecar, _ = _sidecar(FakeClient())

    await sidecar.tick(db_session, now=NOW)

    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == SUBSCRIPTIONS_STREAM))
    ).scalar_one_or_none()
    assert state is None
