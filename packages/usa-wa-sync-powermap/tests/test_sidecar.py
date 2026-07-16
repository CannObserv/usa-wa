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
from clearinghouse_domain_legislative.identity import Organization
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


# --- outbox commit boundary (#8) -----------------------------------------------


async def test_tick_commits_outbox_per_entry(db_session, state_type):
    """#8: when tick is driven with a commit hook, the outbox drain commits per
    delivered entry (default chunk size 1) so a slow PM never holds one open
    transaction across every round-trip."""
    for slug in ("usa-wa-a", "usa-wa-b", "usa-wa-c"):
        db_session.add(
            Jurisdiction(slug=slug, name=slug, type_id=state_type.id, recorded_at=datetime.now(UTC))
        )
    await db_session.flush()
    # A distinct PM id per delivery — three jurisdictions cannot share one anchor
    # (the #86 one-row-per-anchor guard would park the duplicates).
    client = FakeClient(
        observation_result=lambda _payload: ObservationResult(DISPOSITION_NEW, ULID(), {})
    )
    sidecar, _ = _sidecar(client)
    commits = 0

    async def _commit() -> None:
        nonlocal commits
        commits += 1
        await db_session.commit()

    await sidecar.tick(db_session, now=NOW, commit=_commit)

    delivered = (await db_session.execute(select(OutboxEntry))).scalars().all()
    assert len(delivered) == 3
    assert all(e.status == STATUS_DELIVERED for e in delivered)
    # 1 sweep-batch commit (#92, the 3 rows are one keyset batch) + 3 per-entry drain
    # commits — the drain still commits once per delivered entry, not once for the whole drain.
    assert commits == 4


async def test_tick_uses_configured_commit_chunk_size(db_session, state_type):
    """The chunk size is configurable: 4 entries at chunk_size=2 → 2 commit points."""
    for slug in ("usa-wa-a", "usa-wa-b", "usa-wa-c", "usa-wa-d"):
        db_session.add(
            Jurisdiction(slug=slug, name=slug, type_id=state_type.id, recorded_at=datetime.now(UTC))
        )
    await db_session.flush()
    # Distinct PM id per delivery (see the per-entry test) — four jurisdictions can't
    # share one anchor under the #86 guard.
    client = FakeClient(
        observation_result=lambda _payload: ObservationResult(DISPOSITION_NEW, ULID(), {})
    )
    descriptor = JurisdictionDescriptor()
    engine = SyncEngine([descriptor], client)
    sidecar = Sidecar(
        engine, [descriptor], session_factory=lambda: None, outbox_commit_chunk_size=2
    )
    commits = 0

    async def _commit() -> None:
        nonlocal commits
        commits += 1
        await db_session.commit()

    await sidecar.tick(db_session, now=NOW, commit=_commit)

    # 1 sweep-batch commit (#92, 4 rows in one keyset batch) + ceil(4/2)=2 drain commits.
    assert commits == 3


async def test_tick_drains_against_a_fresh_clock(db_session, state_type):
    """#93: entries enqueued during the tick are delivered the SAME cycle even when the
    passed cycle ``now`` predates their ``next_attempt_at`` — the drain re-reads the clock,
    so a slow bulk sweep doesn't defer every freshly-enqueued entry a whole cycle."""
    for slug in ("usa-wa-a", "usa-wa-b"):
        db_session.add(
            Jurisdiction(slug=slug, name=slug, type_id=state_type.id, recorded_at=datetime.now(UTC))
        )
    await db_session.flush()
    client = FakeClient(
        observation_result=lambda _payload: ObservationResult(DISPOSITION_NEW, ULID(), {})
    )
    sidecar, _ = _sidecar(client)

    async def _commit() -> None:
        await db_session.commit()

    # A cycle ``now`` far in the PAST — earlier than the rows' server-default
    # ``next_attempt_at``. Draining against this stale ``now`` would find nothing due; the
    # fresh-clock drain (real now, after the sweep) delivers them this cycle.
    past = datetime(2000, 1, 1, tzinfo=UTC)
    await sidecar.tick(db_session, now=past, commit=_commit)

    delivered = (await db_session.execute(select(OutboxEntry))).scalars().all()
    assert len(delivered) == 2
    assert all(e.status == STATUS_DELIVERED for e in delivered)


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

    async def _ok(s, *, now, commit):
        return None

    sidecar.tick = _ok
    await sidecar.run_cycle()

    assert session.committed and not session.rolled_back


async def test_run_cycle_isolates_and_rolls_back_on_error():
    session = _FakeSession()
    sidecar = Sidecar(engine=None, descriptors=[], session_factory=lambda: session)

    async def _boom(s, *, now, commit):
        raise RuntimeError("poison cycle")

    sidecar.tick = _boom
    await sidecar.run_cycle()  # must NOT raise

    assert session.rolled_back and not session.committed


async def test_catalog_sync_runs_first_cycle_then_gated_by_cadence():
    """The role-type catalog sync (power-map#268) runs on the first cycle so seats can
    flow after startup, then is skipped until ``catalog_sync_cadence`` elapses."""
    calls: list[datetime] = []

    async def _catalog(session):
        calls.append(NOW)

    t0 = datetime(2026, 7, 5, tzinfo=UTC)
    times = iter([t0, t0 + timedelta(minutes=10), t0 + timedelta(hours=2)])
    sidecar = Sidecar(
        engine=None,
        descriptors=[],
        session_factory=lambda: _FakeSession(),
        catalog_sync=_catalog,
        catalog_sync_cadence=timedelta(hours=1),
        clock=lambda: next(times),
    )
    sidecar.tick = lambda s, *, now, commit: _noop()

    await sidecar.run_cycle()  # t0 → runs (first)
    await sidecar.run_cycle()  # +10m → within cadence, skipped
    await sidecar.run_cycle()  # +2h → cadence elapsed, runs
    assert len(calls) == 2


async def test_catalog_sync_failure_is_isolated_and_retries():
    """A catalog-fetch failure is swallowed (never crashes the cycle) and leaves the
    cadence unstamped so the next cycle retries."""
    attempts = {"n": 0}

    async def _catalog(session):
        attempts["n"] += 1
        raise RuntimeError("PM down")

    sidecar = Sidecar(
        engine=None,
        descriptors=[],
        session_factory=lambda: _FakeSession(),
        catalog_sync=_catalog,
        clock=lambda: NOW,
    )
    sidecar.tick = lambda s, *, now, commit: _noop()

    await sidecar.run_cycle()  # must NOT raise
    await sidecar.run_cycle()
    assert attempts["n"] == 2  # unstamped after failure → retried


async def _noop() -> None:
    return None


async def test_jurisdiction_reconcile_skipped(db_session):
    """usa-wa#10/#13: jurisdictions have ``reconcile_mode="none"`` — the WA subtree is
    driven by the subscription-filtered feed + discovery, not any reconcile. So the
    sidecar never schedules a reconcile for them."""
    sidecar, _ = _sidecar(FakeClient())
    assert await sidecar._reconcile_due(db_session, JurisdictionDescriptor(), NOW) is False


async def test_anchored_cohort_producers_are_reconcile_due(db_session):
    """usa-wa#13: the cohort producers run the bounded anchored-cohort backstop, so
    they ARE reconcile-due (first run, no prior stamp) — unlike the retired full-list
    firehose. The backstop re-fetches only their anchored rows."""
    sidecar, _ = _sidecar(FakeClient())
    assert await sidecar._reconcile_due(db_session, OrganizationDescriptor(), NOW) is True
    assert await sidecar._reconcile_due(db_session, RoleDescriptor(), NOW) is True


async def test_reconcile_due_resumes_when_cursor_set_within_cadence(db_session):
    """#94: a fresh stamp normally means not-due within the cadence — but a set cursor is an
    interrupted pass that must resume now, so it's due regardless of the stamp."""
    sidecar, _ = _sidecar(FakeClient())
    org = OrganizationDescriptor()
    # A just-stamped stream: by cadence alone this is NOT due (now == last_reconcile_at).
    db_session.add(SyncState(stream=f"reconcile:{org.entity_type}", last_reconcile_at=NOW))
    await db_session.flush()
    assert await sidecar._reconcile_due(db_session, org, NOW) is False

    # Set a keyset checkpoint → an interrupted pass → due now, cadence notwithstanding.
    state = (
        await db_session.execute(
            select(SyncState).where(SyncState.stream == f"reconcile:{org.entity_type}")
        )
    ).scalar_one()
    state.cursor = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    await db_session.flush()
    assert await sidecar._reconcile_due(db_session, org, NOW) is True


async def test_run_descriptor_reconcile_recovers_dropped_edit(db_session):
    """End-to-end through the per-descriptor reconcile seam (#85): an anchored org
    whose feed bump was dropped (stale local name + old clock) is recovered by the
    cohort backstop, which re-fetches only its anchored row by id and applies PM's
    newer record under LWW."""
    pm_id = ULID()
    org = Organization(
        source="usa_wa_legislature",
        source_id="comm-1",
        name="StaleName",
        org_type="committee",
        pm_organization_id=pm_id,
    )
    org.updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    db_session.add(org)
    await db_session.flush()

    descriptor = OrganizationDescriptor()
    record = {"id": str(pm_id), "name": "CuratedName", "updated_at": "2026-06-07T00:00:00Z"}
    engine = SyncEngine([descriptor], FakeClient(entities={pm_id: record}))
    sidecar = Sidecar(engine, [descriptor], session_factory=lambda: None)

    ran = await sidecar.run_descriptor_reconcile(db_session, descriptor, now=NOW)
    await db_session.refresh(org)

    assert ran is True
    assert org.name == "CuratedName"
    # The reconcile stamped its stream, so the cadence gate sees the run.
    state = (
        await db_session.execute(
            select(SyncState).where(SyncState.stream == "reconcile:organization")
        )
    ).scalar_one()
    assert state.last_reconcile_at == NOW


async def test_tick_does_not_run_reconciles(db_session):
    """#85 fix 1: tick owns only feed/sweep/drain — reconciles run per-descriptor in
    their own sessions via run_cycle, so a poison reconcile can't roll back the feed
    cursor or starve the drain. The dropped edit is NOT recovered by tick alone."""
    pm_id = ULID()
    org = Organization(
        source="usa_wa_legislature",
        source_id="comm-1",
        name="StaleName",
        org_type="committee",
        pm_organization_id=pm_id,
    )
    org.updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    db_session.add(org)
    await db_session.flush()

    descriptor = OrganizationDescriptor()
    record = {"id": str(pm_id), "name": "CuratedName", "updated_at": "2026-06-07T00:00:00Z"}
    engine = SyncEngine([descriptor], FakeClient(entities={pm_id: record}))
    sidecar = Sidecar(engine, [descriptor], session_factory=lambda: None)

    await sidecar.tick(db_session, now=NOW)
    await db_session.refresh(org)

    assert org.name == "StaleName"


# --- subscription backstop ------------------------------------------------------


async def test_backstop_registers_and_backfills_new(db_session, state_type):
    """The backstop discovers + registers a new WA jurisdiction and backfills it."""
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

    ran = await sidecar.run_subscription_backstop(db_session, now=NOW)

    assert ran is True
    assert client.added == [[pm_id]]  # backstop registered the discovered id
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
    """A second run within the cadence does not re-run discovery."""
    client = FakeClient(discovered=[], subscribed=[])
    sidecar, _ = _sidecar_with_reconciler(client, cadence=timedelta(hours=1))

    assert await sidecar.run_subscription_backstop(db_session, now=NOW) is True
    within = await sidecar.run_subscription_backstop(db_session, now=NOW + timedelta(minutes=30))
    assert within is False
    assert len(client.discover_calls) == 1  # only the first run ran the backstop

    elapsed = await sidecar.run_subscription_backstop(db_session, now=NOW + timedelta(hours=2))
    assert elapsed is True
    assert len(client.discover_calls) == 2  # cadence elapsed → ran again


async def test_backstop_noop_without_reconciler(db_session):
    """A sidecar built without a reconciler never runs the backstop or touches the
    subscriptions stream."""
    sidecar, _ = _sidecar(FakeClient())

    ran = await sidecar.run_subscription_backstop(db_session, now=NOW)

    assert ran is False
    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == SUBSCRIPTIONS_STREAM))
    ).scalar_one_or_none()
    assert state is None


async def test_tick_does_not_run_backstop(db_session, state_type):
    """tick() owns only feed/sweep/drain — the backstop is isolated to run_cycle."""
    client = FakeClient(
        discovered=[DiscoveredEntity("jurisdiction", ULID(), "X", 1)], subscribed=[]
    )
    sidecar, _ = _sidecar_with_reconciler(client)

    await sidecar.tick(db_session, now=NOW)

    assert client.discover_calls == []  # tick never invoked discovery
    state = (
        await db_session.execute(select(SyncState).where(SyncState.stream == SUBSCRIPTIONS_STREAM))
    ).scalar_one_or_none()
    assert state is None


async def test_run_cycle_isolates_backstop_failure_from_tick():
    """A backstop failure is contained in its own session/boundary; run_cycle still
    runs the main tick (feed/drain) so a discovery outage does not starve the feed."""
    session = _FakeSession()
    sidecar = Sidecar(
        engine=None, descriptors=[], session_factory=lambda: session, reconciler=object()
    )
    tick_ran = {"value": False}

    async def _boom_backstop(s, *, now):
        raise RuntimeError("discover endpoint down")

    async def _ok_tick(s, *, now, commit):
        tick_ran["value"] = True

    sidecar.run_subscription_backstop = _boom_backstop
    sidecar.tick = _ok_tick

    await sidecar.run_cycle()  # must NOT raise

    assert tick_ran["value"] is True  # main tick still ran despite backstop failure


async def test_run_backstop_commits_on_success():
    """The success path commits the backstop's own session."""
    session = _FakeSession()
    sidecar = Sidecar(
        engine=None, descriptors=[], session_factory=lambda: session, reconciler=object()
    )

    async def _ran(s, *, now):
        return True

    sidecar.run_subscription_backstop = _ran

    await sidecar._run_backstop(NOW)

    assert session.committed


async def test_run_backstop_contains_session_acquire_failure():
    """A failure to even acquire the session (e.g. pool exhausted) is contained in
    _run_backstop and never propagates to crash run_forever (CR round 2 item 6)."""

    def _boom_factory():
        raise RuntimeError("pool exhausted")

    sidecar = Sidecar(
        engine=None, descriptors=[], session_factory=_boom_factory, reconciler=object()
    )

    await sidecar._run_backstop(NOW)  # must NOT raise


# --- #85: per-descriptor reconcile isolation + cycle verdict ---------------------


class _Descriptor:
    """Minimal stand-in — only ``entity_type`` is read by the isolation loop."""

    def __init__(self, entity_type: str) -> None:
        self.entity_type = entity_type


async def test_poison_reconcile_is_isolated_and_others_still_run():
    """#84's core amplification: one descriptor's reconcile raising must not stop the
    other descriptors' reconciles (each has its own session) nor the main tick."""
    sessions: list[_FakeSession] = []

    def _factory() -> _FakeSession:
        s = _FakeSession()
        sessions.append(s)
        return s

    a, b = _Descriptor("assignment"), _Descriptor("person")
    sidecar = Sidecar(engine=None, descriptors=[a, b], session_factory=_factory)
    ran: list[str] = []

    async def _reconcile(session, descriptor, *, now):
        if descriptor is a:
            raise RuntimeError("MultipleResultsFound: poison entity")
        ran.append(descriptor.entity_type)
        return True

    async def _ok_tick(s, *, now, commit):
        ran.append("tick")

    sidecar.run_descriptor_reconcile = _reconcile
    sidecar.tick = _ok_tick

    ok = await sidecar.run_cycle()  # must NOT raise

    assert ran == ["person", "tick"]  # b's reconcile and the tick both ran
    assert ok is False  # ...but the cycle verdict reports the failure (backoff signal)
    # One session per descriptor + one for the tick + one for the cycle summary —
    # a poison session is never shared.
    assert len(sessions) == 4


async def test_run_cycle_verdict_true_when_clean():
    sidecar = Sidecar(engine=None, descriptors=[], session_factory=lambda: _FakeSession())

    async def _ok(s, *, now, commit):
        return None

    sidecar.tick = _ok
    assert await sidecar.run_cycle() is True


async def test_run_cycle_verdict_false_on_tick_failure():
    sidecar = Sidecar(engine=None, descriptors=[], session_factory=lambda: _FakeSession())

    async def _boom(s, *, now, commit):
        raise RuntimeError("tick poison")

    sidecar.tick = _boom
    assert await sidecar.run_cycle() is False


async def test_run_cycle_verdict_false_on_backstop_failure():
    """A contained backstop failure still fails the cycle verdict — isolation must not
    silently defeat the backoff/streak signal (#85 interaction)."""
    sidecar = Sidecar(
        engine=None,
        descriptors=[],
        session_factory=lambda: _FakeSession(),
        reconciler=object(),
    )

    async def _boom_backstop(s, *, now):
        raise RuntimeError("discover endpoint down")

    async def _ok_tick(s, *, now, commit):
        return None

    sidecar.run_subscription_backstop = _boom_backstop
    sidecar.tick = _ok_tick
    assert await sidecar.run_cycle() is False


async def test_run_cycle_verdict_false_on_catalog_sync_failure():
    async def _boom_catalog(session):
        raise RuntimeError("PM down")

    sidecar = Sidecar(
        engine=None,
        descriptors=[],
        session_factory=lambda: _FakeSession(),
        catalog_sync=_boom_catalog,
    )

    async def _ok_tick(s, *, now, commit):
        return None

    sidecar.tick = _ok_tick
    assert await sidecar.run_cycle() is False


# --- #85: exponential backoff on consecutive cycle failures ----------------------


class _StopLoop(Exception):
    """Breaks run_forever after the scripted outcomes are consumed."""


def _scripted_sidecar(outcomes: list[bool]) -> tuple[Sidecar, list[float]]:
    sidecar = Sidecar(engine=None, descriptors=[], session_factory=lambda: _FakeSession())
    script = iter(outcomes)

    async def _cycle() -> bool:
        try:
            return next(script)
        except StopIteration:
            raise _StopLoop from None

    sidecar.run_cycle = _cycle
    sleeps: list[float] = []
    return sidecar, sleeps


async def _run_scripted(sidecar: Sidecar, sleeps: list[float]) -> None:
    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with pytest.raises(_StopLoop):
        await sidecar.run_forever(sleep=_sleep)


async def test_run_forever_backs_off_on_consecutive_failures_and_resets():
    """Failures sleep the retry.backoff schedule (60s base, doubling); a success
    resets to the poll cadence."""
    sidecar, sleeps = _scripted_sidecar([False, False, False, True, False])
    sidecar._feed_poll_seconds = 1.0

    await _run_scripted(sidecar, sleeps)

    assert sleeps == [60.0, 120.0, 240.0, 1.0, 60.0]


async def test_run_forever_backoff_never_below_poll_cadence():
    """With a poll cadence above the early backoff steps, the sleep is the max of
    the two — backoff slows the loop down, never speeds it up."""
    sidecar, sleeps = _scripted_sidecar([False, False])
    sidecar._feed_poll_seconds = 90.0

    await _run_scripted(sidecar, sleeps)

    assert sleeps == [90.0, 120.0]


async def test_run_forever_backoff_caps_at_one_hour():
    sidecar, sleeps = _scripted_sidecar([False] * 9)
    sidecar._feed_poll_seconds = 1.0

    await _run_scripted(sidecar, sleeps)

    assert sleeps[-1] == 3600.0
    assert max(sleeps) == 3600.0


# --- #85: failure-streak alerting -------------------------------------------------


def _alerting_sidecar(
    outcomes: list[bool], *, threshold: int = 3
) -> tuple[Sidecar, list[tuple[str, str]], list[float]]:
    alerts: list[tuple[str, str]] = []

    async def _alert(subject: str, body: str) -> None:
        alerts.append((subject, body))

    sidecar = Sidecar(
        engine=None,
        descriptors=[],
        session_factory=lambda: _FakeSession(),
        alert=_alert,
        failure_alert_threshold=threshold,
    )
    script = iter(outcomes)

    async def _cycle() -> bool:
        try:
            return next(script)
        except StopIteration:
            raise _StopLoop from None

    sidecar.run_cycle = _cycle
    sleeps: list[float] = []
    return sidecar, alerts, sleeps


async def test_alert_fires_once_at_streak_threshold():
    """One email at streak == N; the continuing streak does not re-send."""
    sidecar, alerts, sleeps = _alerting_sidecar([False] * 5, threshold=3)

    await _run_scripted(sidecar, sleeps)

    assert len(alerts) == 1
    subject, _body = alerts[0]
    assert "3" in subject  # streak size is triageable from the subject line


async def test_alert_rearms_after_recovery():
    """Success resets the streak; a fresh streak reaching N alerts again."""
    sidecar, alerts, sleeps = _alerting_sidecar(
        [False, False, False, True, False, False, False], threshold=3
    )

    await _run_scripted(sidecar, sleeps)

    assert len(alerts) == 2


async def test_alert_below_threshold_never_fires():
    sidecar, alerts, sleeps = _alerting_sidecar([False, False, True, False, False], threshold=3)

    await _run_scripted(sidecar, sleeps)

    assert alerts == []


async def test_alert_send_failure_is_swallowed():
    """A failing alert send must never crash the loop it is watching."""
    sidecar = Sidecar(
        engine=None,
        descriptors=[],
        session_factory=lambda: _FakeSession(),
        alert=None,
        failure_alert_threshold=1,
    )

    async def _boom_alert(subject: str, body: str) -> None:
        raise RuntimeError("gateway down")

    sidecar._alert = _boom_alert
    script = iter([False, False])

    async def _cycle() -> bool:
        try:
            return next(script)
        except StopIteration:
            raise _StopLoop from None

    sidecar.run_cycle = _cycle
    sleeps: list[float] = []

    await _run_scripted(sidecar, sleeps)  # must NOT raise beyond _StopLoop

    assert len(sleeps) == 2


async def test_alert_body_carries_last_cycle_errors():
    """The alert body embeds the collected component errors so the operator can
    triage without the journal (#49 philosophy)."""
    alerts: list[tuple[str, str]] = []

    async def _alert(subject: str, body: str) -> None:
        alerts.append((subject, body))

    sidecar = Sidecar(
        engine=None,
        descriptors=[_Descriptor("assignment")],
        session_factory=lambda: _FakeSession(),
        alert=_alert,
        failure_alert_threshold=1,
    )

    async def _reconcile(session, descriptor, *, now):
        raise RuntimeError("MultipleResultsFound: poison entity")

    async def _ok_tick(s, *, now, commit):
        return None

    sidecar.run_descriptor_reconcile = _reconcile
    sidecar.tick = _ok_tick

    stop = iter([True])

    async def _sleep(seconds: float) -> None:
        try:
            next(stop)
            raise _StopLoop
        except StopIteration:
            raise _StopLoop from None

    with pytest.raises(_StopLoop):
        await sidecar.run_forever(sleep=_sleep)

    assert len(alerts) == 1
    _subject, body = alerts[0]
    assert "MultipleResultsFound" in body
    assert "assignment" in body  # which component failed is in the body


# --- #85: per-entry rejection visibility ------------------------------------------


async def _add_rejected(session, *, reason: str) -> None:
    from clearinghouse_sync_powermap.models import OP_CREATE, STATUS_REJECTED

    entry = OutboxEntry(entity_type="person", local_id=ULID(), op=OP_CREATE, status=STATUS_REJECTED)
    entry.last_error = reason
    session.add(entry)
    await session.flush()


def _summary_sidecar(alerts: list[tuple[str, str]] | None = None) -> Sidecar:
    async def _alert(subject: str, body: str) -> None:
        if alerts is not None:
            alerts.append((subject, body))

    return Sidecar(
        engine=None,
        descriptors=[],
        session_factory=lambda: None,
        alert=_alert if alerts is not None else None,
    )


async def test_cycle_summary_logs_backlog_and_reasons(db_session, caplog):
    """#84 postmortem: 12 identifier_conflict rejections sat unnoticed from Jul 8 —
    each was a lone logger.error at park time. The cycle summary re-surfaces the
    standing REJECTED pile (with reasons) every cycle."""
    await _add_rejected(db_session, reason="identifier_conflict")
    await _add_rejected(db_session, reason="identifier_conflict")
    sidecar = _summary_sidecar()

    with caplog.at_level("INFO"):
        await sidecar.report_cycle_summary(db_session, now=NOW)

    record = next(r for r in caplog.records if r.message == "sidecar_cycle_summary")
    assert record.rejected == 2
    assert record.rejected_reasons == {"identifier_conflict": 2}


async def test_rejected_rise_alerts_once_and_rearms_on_new_rise(db_session):
    """A REJECTED count rise emails the operator once; a static pile does not
    re-spam; a further rise alerts again."""
    alerts: list[tuple[str, str]] = []
    sidecar = _summary_sidecar(alerts)

    await _add_rejected(db_session, reason="identifier_conflict")
    await sidecar.report_cycle_summary(db_session, now=NOW)  # 0 → 1: alert
    await sidecar.report_cycle_summary(db_session, now=NOW)  # static: no repeat
    assert len(alerts) == 1
    assert "identifier_conflict" in alerts[0][1]

    await _add_rejected(db_session, reason="qualifier_required")
    await sidecar.report_cycle_summary(db_session, now=NOW)  # 1 → 2: alert again
    assert len(alerts) == 2
    assert "qualifier_required" in alerts[1][1]


async def test_rejected_alert_skipped_when_no_alert_wired(db_session, caplog):
    """No alert callable → the rise is still logged (never crashes)."""
    sidecar = _summary_sidecar(alerts=None)
    await _add_rejected(db_session, reason="identifier_conflict")

    with caplog.at_level("INFO"):
        await sidecar.report_cycle_summary(db_session, now=NOW)  # must NOT raise

    assert any(r.message == "sidecar_cycle_summary" for r in caplog.records)


async def test_run_cycle_summary_failure_never_fails_verdict():
    """The summary is observability, not work — its failure must not flip the
    verdict (that would put reporting in the backoff/alert path)."""
    sidecar = Sidecar(engine=None, descriptors=[], session_factory=lambda: _FakeSession())

    async def _ok(s, *, now, commit):
        return None

    async def _boom_summary(session, *, now):
        raise RuntimeError("summary query failed")

    sidecar.tick = _ok
    sidecar.report_cycle_summary = _boom_summary

    assert await sidecar.run_cycle() is True
