"""Engine write-path tests — sweep + outbox worker (engine step 3)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import (
    DeliveryBlockedError,
    ObservationResult,
    PayloadRejectedError,
    RetryableClientError,
)
from clearinghouse_sync_powermap.engine import SyncEngine, outbox_backlog
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_NEW,
    DISPOSITION_REJECTED,
    OP_CREATE,
    STATUS_DELIVERED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    OutboxEntry,
)
from clearinghouse_sync_powermap.testing import FakeClient, FakeDescriptor, FakeEntity

# Far-future so it always exceeds the outbox rows' ``server_default now()`` insert
# stamp (otherwise a drain `now` earlier than wall-clock-at-insert sees nothing due).
NOW = datetime(2099, 1, 1, tzinfo=UTC)


def _raise_conn_error(payload):
    raise ConnectionError("PM unreachable")


async def _add_entity(session, *, source_id, name="x", anchor=None):
    row = FakeEntity(source="wsl", source_id=source_id, name=name, pm_fake_id=anchor)
    session.add(row)
    await session.flush()
    return row


def _sleep_recorder():
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleeps, _sleep


class _Flaky429Descriptor(FakeDescriptor):
    """``pm_match`` raises a 429 the first ``failures`` calls, then resolves to
    no-match (→ CREATE) — the #92 bulk-ingest burst that used to abort the tick."""

    def __init__(self, *, failures: int) -> None:
        self._failures = failures
        self.match_calls = 0

    async def pm_match(self, client, session, row):  # noqa: ARG002
        self.match_calls += 1
        if self._failures > 0:
            self._failures -= 1
            raise RetryableClientError("PM 429", retry_after=0.5)
        return None


class _CountingDescriptor(FakeDescriptor):
    """Counts ``pm_match`` calls so a test can assert a queued row is not re-searched."""

    def __init__(self) -> None:
        self.match_calls = 0

    async def pm_match(self, client, session, row):  # noqa: ARG002
        self.match_calls += 1
        return None


async def test_sweep_skips_rows_with_open_outbox_entry(db_session):
    """#93: a row that already has a PENDING outbox entry is excluded from the sweep —
    not re-``pm_match``ed — so a bulk ingest doesn't re-search queued rows every cycle."""
    await _add_entity(db_session, source_id="1")
    descriptor = _CountingDescriptor()
    engine = SyncEngine([descriptor], FakeClient())

    assert await engine.sweep_unanchored(db_session, descriptor) == 1  # first: enqueues a CREATE
    assert descriptor.match_calls == 1

    # Second sweep: the row is still anchor-NULL (create not delivered) but now carries a
    # PENDING entry → excluded from the query, so pm_match is NOT called again.
    assert await engine.sweep_unanchored(db_session, descriptor) == 0
    assert descriptor.match_calls == 1  # unchanged — no wasted re-search


async def test_sweep_match_429_pauses_and_resumes(db_session):
    """#92: a 429 during the sweep's ``pm_match`` pauses-and-resumes (honoring
    Retry-After) instead of aborting the tick — else a first bulk ingest, one search
    per un-anchored row, trips PM's limit and makes zero durable progress."""
    await _add_entity(db_session, source_id="1")
    descriptor = _Flaky429Descriptor(failures=2)
    sleeps, sleep = _sleep_recorder()
    engine = SyncEngine([descriptor], FakeClient(), sleep=sleep)

    count = await engine.sweep_unanchored(db_session, descriptor)

    assert count == 1  # resolved to a CREATE after the retries — no exception propagated
    assert sleeps == [0.5, 0.5]  # honored Retry-After on each transient 429
    assert descriptor.match_calls == 3  # 2 failures + 1 success
    assert (await db_session.execute(select(OutboxEntry))).scalars().one().op == OP_CREATE


async def test_sweep_commits_per_batch(db_session, fake_descriptor):
    """#92: with a commit hook the sweep commits per keyset batch, so a bulk ingest's
    progress persists incrementally rather than riding one all-or-nothing transaction."""
    for i in range(5):
        await _add_entity(db_session, source_id=str(i))
    commits: list[int] = []

    async def _commit() -> None:
        commits.append(1)

    engine = SyncEngine([fake_descriptor], FakeClient(), sweep_batch_size=2)

    count = await engine.sweep_unanchored(db_session, fake_descriptor, commit=_commit)

    assert count == 5
    assert len(commits) == 3  # batches of 2, 2, 1 → one commit each


async def test_sweep_no_commit_hook_is_single_transaction(db_session, fake_descriptor):
    """Without a commit hook the sweep keeps the legacy single-transaction boundary."""
    for i in range(3):
        await _add_entity(db_session, source_id=str(i))
    engine = SyncEngine([fake_descriptor], FakeClient(), sweep_batch_size=2)

    count = await engine.sweep_unanchored(db_session, fake_descriptor)  # no commit=

    assert count == 3  # still enqueues everything, just no intermediate commits


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


class _MatchingDescriptor(FakeDescriptor):
    """A descriptor whose ``pm_match`` cascade finds a pre-existing PM record."""

    def __init__(self, pm_id) -> None:
        self._pm_id = pm_id

    async def pm_match(self, client, session, row):  # noqa: ARG002
        return self._pm_id


async def test_sweep_matched_anchors_without_create(db_session):
    """PM-first: a row that pm_match resolves to an existing PM entity is anchored
    and adopts PM's canonical fields — never enqueued as a duplicate CREATE."""
    pm_id = ULID()
    row = await _add_entity(db_session, source_id="m1", name="Adapter Name")
    record = {
        "source": "wsl",
        "source_id": "m1",
        "name": "PM Canonical Name",
        "id": str(pm_id),
        "updated_at": "2030-01-01T00:00:00Z",
    }
    client = FakeClient(entities={pm_id: record, str(pm_id): record})
    descriptor = _MatchingDescriptor(pm_id)
    engine = SyncEngine([descriptor], client)

    count = await engine.sweep_unanchored(db_session, descriptor)

    assert count == 0  # matched → no CREATE
    assert (await db_session.execute(select(OutboxEntry))).scalars().all() == []
    await db_session.refresh(row)
    assert row.pm_fake_id == pm_id  # anchored to the matched PM id
    assert row.name == "PM Canonical Name"  # adopted PM's canonical name, no overwrite to PM


async def test_drain_defers_until_dependencies_ready(db_session):
    """A row whose PM prerequisites aren't anchored is deferred (kept PENDING,
    no post), then delivered once dependencies_ready flips True."""

    class _GatedDescriptor(FakeDescriptor):
        ready = False

        async def dependencies_ready(self, session, row):  # noqa: ARG002
            return self.ready

    row = await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    descriptor = _GatedDescriptor()
    engine = SyncEngine([descriptor], client)
    await engine.sweep_unanchored(db_session, descriptor)

    # Not ready → deferred: still PENDING, nothing posted, attempts not inflated.
    touched = await engine.drain_outbox(db_session, now=NOW)
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert touched == [entry]
    assert entry.status == STATUS_PENDING
    assert entry.attempts == 0
    assert entry.next_attempt_at == NOW + timedelta(seconds=60)
    assert client.posted == []

    # Ready → delivers on a later cycle (after the defer window).
    descriptor.ready = True
    later = NOW + timedelta(seconds=61)
    await engine.drain_outbox(db_session, now=later)
    assert row.pm_fake_id is not None
    assert len(client.posted) == 1


class _EnrichDescriptor(FakeDescriptor):
    """Matches an identifier-less PM record by name and enriches it (#198)."""

    enrich_identifier_type = "pm_fake_id"

    def __init__(self, pm_id, record) -> None:
        self._pm_id = pm_id
        self._record = record

    async def pm_match(self, client, session, row):  # noqa: ARG002
        return self._pm_id

    async def needs_enrich(self, record, row):  # noqa: ARG002
        return True

    async def to_observation(self, session, row):  # noqa: ARG002
        return {
            "identifier_type": "fake_real_id",
            "identifier_value": row.source_id,
            "names": [{"name": row.name, "name_type": "legal"}],
        }


async def test_sweep_matched_enqueues_enrich(db_session):
    """A name-matched, identifier-less PM record → row anchored + an ENRICH entry
    queued to push our identifier onto that PM entity."""
    pm_id = ULID()
    row = await _add_entity(db_session, source_id="e1", name="Adapter")
    record = {"source": "wsl", "source_id": "e1", "name": "PM Name", "id": str(pm_id)}
    client = FakeClient(entities={pm_id: record, str(pm_id): record})
    descriptor = _EnrichDescriptor(pm_id, record)
    engine = SyncEngine([descriptor], client)

    count = await engine.sweep_unanchored(db_session, descriptor)

    assert count == 0  # matched → no CREATE
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert entry.op == "ENRICH"
    assert row.pm_fake_id == pm_id


async def test_drain_delivers_enrich_payload_keyed_on_pm_id(db_session):
    """The ENRICH delivery re-keys to pm_*_id + the anchor and demotes our real
    identifier into additional_identifiers (power-map#198 attach-by-id)."""
    pm_id = ULID()
    await _add_entity(db_session, source_id="e1", name="Adapter")
    record = {"source": "wsl", "source_id": "e1", "name": "PM Name", "id": str(pm_id)}
    client = FakeClient(
        entities={pm_id: record, str(pm_id): record},
        observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}),
    )
    descriptor = _EnrichDescriptor(pm_id, record)
    engine = SyncEngine([descriptor], client)
    await engine.sweep_unanchored(db_session, descriptor)

    await engine.drain_outbox(db_session, now=NOW)

    path, payload = client.posted[-1]
    assert path == "/api/v1/fakes/observations"
    assert payload["identifier_type"] == "pm_fake_id"
    assert payload["identifier_value"] == str(pm_id)
    assert payload["additional_identifiers"] == [
        {"identifier_type_slug": "fake_real_id", "identifier_value": "e1"}
    ]
    # Name evidence is the row's current name — PM's canonical, adopted at match.
    assert payload["names"] == [{"name": "PM Name", "name_type": "legal"}]


async def test_sweep_skips_enrich_when_pm_has_our_identifier(db_session):
    """Matched by identifier (PM already holds it) → needs_enrich False → no ENRICH."""

    class _NoEnrich(_EnrichDescriptor):
        async def needs_enrich(self, record, row):  # noqa: ARG002
            return False

    pm_id = ULID()
    await _add_entity(db_session, source_id="e1", name="Adapter")
    record = {"source": "wsl", "source_id": "e1", "name": "PM Name", "id": str(pm_id)}
    client = FakeClient(entities={pm_id: record, str(pm_id): record})
    engine = SyncEngine([_NoEnrich(pm_id, record)], client)

    await engine.sweep_unanchored(db_session, _NoEnrich(pm_id, record))

    assert (await db_session.execute(select(OutboxEntry))).scalars().all() == []


async def test_sweep_is_idempotent(db_session, fake_descriptor):
    await _add_entity(db_session, source_id="1")
    engine = SyncEngine([fake_descriptor], FakeClient())

    assert await engine.sweep_unanchored(db_session, fake_descriptor) == 1
    assert await engine.sweep_unanchored(db_session, fake_descriptor) == 0  # open entry exists


async def test_sweep_batches_large_backlog(db_session, fake_descriptor):
    """#7: a backlog larger than the batch size is swept in keyset-paged batches —
    every unanchored row is enqueued, never all materialised at once. With a batch
    size of 2 and 5 rows, all 5 get a CREATE in a single sweep call."""
    for i in range(5):
        await _add_entity(db_session, source_id=str(i))
    engine = SyncEngine([fake_descriptor], FakeClient(), sweep_batch_size=2)

    count = await engine.sweep_unanchored(db_session, fake_descriptor)

    assert count == 5
    entries = (await db_session.execute(select(OutboxEntry))).scalars().all()
    assert len(entries) == 5
    assert {e.op for e in entries} == {OP_CREATE}


async def test_sweep_batched_terminates_on_already_enqueued(db_session, fake_descriptor):
    """Keyset paging must advance past rows that stay unanchored after processing
    (a CREATE leaves the anchor null until delivery). A re-sweep of an
    already-enqueued backlog larger than the batch size terminates and enqueues
    nothing new — no infinite loop on the still-null-anchor rows."""
    for i in range(5):
        await _add_entity(db_session, source_id=str(i))
    engine = SyncEngine([fake_descriptor], FakeClient(), sweep_batch_size=2)
    assert await engine.sweep_unanchored(db_session, fake_descriptor) == 5

    # Second sweep: every row is still anchor-null but already has an open entry.
    assert await engine.sweep_unanchored(db_session, fake_descriptor) == 0
    assert len((await db_session.execute(select(OutboxEntry))).scalars().all()) == 5


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


async def test_drain_parks_duplicate_anchor_instead_of_crashing(
    db_session, fake_descriptor, caplog
):
    """Two rows whose observations PM dedups to one assignment id (both anchored to
    the same pm_id) must NOT crash the drain via the anchor unique index (usa-wa#86):
    one row wins the anchor, the other dead-letters to the *blocking* UNAVAILABLE
    state (not REJECTED — an anchor conflict is a permanent operator-dedup case, and
    a blocking status stops the per-cycle re-sweep + rejection-rise spam)."""
    await _add_entity(db_session, source_id="1")
    await _add_entity(db_session, source_id="2")
    shared = ULID()
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, shared, {}))
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    with caplog.at_level("ERROR"):
        touched = await engine.drain_outbox(db_session, now=NOW)

    assert sorted(e.status for e in touched) == sorted([STATUS_DELIVERED, STATUS_UNAVAILABLE])
    delivered = next(e for e in touched if e.status == STATUS_DELIVERED)
    parked = next(e for e in touched if e.status == STATUS_UNAVAILABLE)
    assert (await db_session.get(FakeEntity, delivered.local_id)).pm_fake_id == shared
    # The parked row keeps NO anchor — the conflicting stamp was never applied.
    assert (await db_session.get(FakeEntity, parked.local_id)).pm_fake_id is None
    assert any(r.msg == "anchor_invariant_violation" for r in caplog.records)


async def test_sweep_pm_match_collision_does_not_crash_and_routes_to_park(db_session, caplog):
    """The sweep's PM-first adoption is guarded too (usa-wa#86): if pm_match resolves
    a pm_id another local row already anchors, the sweep declines the adopt (which
    would violate the anchor index and abort the tick) and falls through to a CREATE,
    which the drain then dead-letters to UNAVAILABLE."""
    pm_id = ULID()
    descriptor = _MatchingDescriptor(pm_id)  # pm_match always returns pm_id
    # First row adopts pm_id via the sweep's PM-first path.
    row_a = await _add_entity(db_session, source_id="a")
    entities = {pm_id: {"id": str(pm_id), "source": "wsl", "source_id": "a", "name": "x"}}
    # PM dedups any CREATE observation back to the same taken pm_id.
    client = FakeClient(
        entities=entities, observation_result=ObservationResult(DISPOSITION_NEW, pm_id, {})
    )
    engine = SyncEngine([descriptor], client)
    assert await engine.sweep_unanchored(db_session, descriptor) == 0  # adopted, no CREATE
    assert row_a.pm_fake_id == pm_id

    # Second row name-matches the same pm_id — must not crash; routes to a CREATE.
    row_b = await _add_entity(db_session, source_id="b")
    with caplog.at_level("ERROR"):
        enqueued = await engine.sweep_unanchored(db_session, descriptor)
    assert enqueued == 1  # declined the adopt, enqueued a CREATE instead
    assert row_b.pm_fake_id is None

    # The drain parks that CREATE to UNAVAILABLE (PM dedups to the taken pm_id).
    touched = await engine.drain_outbox(db_session, now=NOW)
    parked = next(e for e in touched if e.local_id == row_b.id)
    assert parked.status == STATUS_UNAVAILABLE
    assert (await db_session.get(FakeEntity, row_b.id)).pm_fake_id is None


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


async def test_drain_rejected_log_surfaces_reason(db_session, fake_descriptor, caplog):
    """PM's ``reason`` (power-map#225) is promoted to a structured log field so a
    rejection is diagnosable without bisecting ``raw`` by hand (usa-wa#33)."""
    await _add_entity(db_session, source_id="1")
    client = FakeClient(
        observation_result=ObservationResult(
            DISPOSITION_REJECTED,
            None,
            {"disposition": "rejected", "reason": "unknown_identifier_type: 'org_wa_x'"},
        )
    )
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    with caplog.at_level("ERROR"):
        await engine.drain_outbox(db_session, now=NOW)

    rejected = [r for r in caplog.records if r.msg == "powermap_observation_rejected"]
    assert [r.reason for r in rejected] == ["unknown_identifier_type: 'org_wa_x'"]


def _raise_blocked(payload):
    raise DeliveryBlockedError("PM 403")


def _raise_payload_rejected(payload):
    raise PayloadRejectedError("PM 422: name required")


async def test_drain_blocked_error_dead_letters_unavailable(db_session, fake_descriptor, caplog):
    """A permanent transport/auth rejection (e.g. 403 insufficient scope) parks the
    entry to UNAVAILABLE immediately — it does NOT propagate (which would roll back
    the whole cycle) and does NOT burn the retry budget (a 403 never recovers on
    retry; the operator fixes the key, then redrives)."""
    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=_raise_blocked)
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    with caplog.at_level("ERROR"):
        touched = await engine.drain_outbox(db_session, now=NOW)

    entry = touched[0]
    assert entry.status == STATUS_UNAVAILABLE
    assert entry.attempts == 0  # parked immediately, not retried to the cap
    assert "PM 403" in entry.last_error
    # The dead-letter event carries reason="blocked" so an operator can tell an
    # auth block apart from a transport-cap exhaustion (same event name).
    unavailable = [r for r in caplog.records if r.msg == "powermap_observation_unavailable"]
    assert [r.reason for r in unavailable] == ["blocked"]


async def test_drain_blocked_error_does_not_starve_siblings(db_session, fake_descriptor):
    """One poison entry parks itself; the drain continues and delivers the rest."""
    await _add_entity(db_session, source_id="1")  # will be blocked
    good = await _add_entity(db_session, source_id="2")  # must still deliver

    def _route(payload):
        if payload["source_id"] == "1":
            raise DeliveryBlockedError("PM 403")
        return ObservationResult(DISPOSITION_NEW, ULID(), {})

    engine = SyncEngine([fake_descriptor], FakeClient(observation_result=_route))
    await engine.sweep_unanchored(db_session, fake_descriptor)

    await engine.drain_outbox(db_session, now=NOW)

    by_local = {
        e.local_id: e for e in (await db_session.execute(select(OutboxEntry))).scalars().all()
    }
    assert by_local[good.id].status == STATUS_DELIVERED
    await db_session.refresh(good)
    assert good.pm_fake_id is not None


async def test_drain_prioritizes_dependency_roots_over_deferred_dependents(db_session):
    """Regression for #96: a dependency-ROOT entry must not be starved out of the
    drain batch by a flood of dependent entries whose ``next_attempt_at`` sorts
    earlier. In the bulk-produce incident the role roots failed once (frozen at
    ~T0+60) while thousands of dependency-blocked assignments re-deferred to
    *just before* T0, filling every ``batch_limit`` cut ahead of the roots — so
    the roots were never re-attempted for ~42 min. The drain now orders
    topologically (registry/dependency order) first, ``next_attempt_at`` second,
    so a bounded root tier is always attempted before its dependents."""

    class _RootDescriptor(FakeDescriptor):
        entity_type = "root"

    class _DepDescriptor(FakeDescriptor):
        entity_type = "dep"

        async def dependencies_ready(self, session, row):  # noqa: ARG002
            return False  # perpetually blocked → defers each cycle, never posts

    root_row = await _add_entity(db_session, source_id="root-1")
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    # Registry order is dependency-first: the root descriptor precedes the dependent.
    engine = SyncEngine([_RootDescriptor(), _DepDescriptor()], client, batch_limit=2)

    # The root failed once on the initial burst → frozen at NOW (attempts=1).
    root_entry = OutboxEntry(
        entity_type="root",
        local_id=root_row.id,
        op=OP_CREATE,
        status=STATUS_PENDING,
        attempts=1,
        next_attempt_at=NOW,
    )
    db_session.add(root_entry)
    # A flood of dependency-blocked dependents deferred to *just before* the root,
    # more than ``batch_limit`` — pure next_attempt_at ordering would exclude the root.
    for i in range(5):
        dep_row = await _add_entity(db_session, source_id=f"dep-{i}")
        db_session.add(
            OutboxEntry(
                entity_type="dep",
                local_id=dep_row.id,
                op=OP_CREATE,
                status=STATUS_PENDING,
                attempts=0,
                next_attempt_at=NOW - timedelta(seconds=1),
            )
        )
    await db_session.flush()

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert root_entry in touched  # topological priority pulled the root into the batch
    assert root_entry.status == STATUS_DELIVERED
    await db_session.refresh(root_row)
    assert root_row.pm_fake_id is not None  # actually delivered this cycle


async def test_drain_payload_rejected_marks_terminal(db_session, fake_descriptor):
    """A payload-validation rejection (e.g. PM 422) parks the entry to REJECTED — the
    re-sweepable 'fix the data' terminal state — not UNAVAILABLE, and never propagates."""
    row = await _add_entity(db_session, source_id="1")
    engine = SyncEngine([fake_descriptor], FakeClient(observation_result=_raise_payload_rejected))
    await engine.sweep_unanchored(db_session, fake_descriptor)

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched[0].status == STATUS_REJECTED
    assert "422" in touched[0].last_error
    assert row.pm_fake_id is None


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


async def test_drain_caps_attempts_to_unavailable(db_session, fake_descriptor, caplog):
    """After max_attempts transport failures, the entry goes terminal (UNAVAILABLE),
    not perpetually PENDING — so the operator backlog can see it."""
    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=_raise_conn_error)
    engine = SyncEngine([fake_descriptor], client, max_attempts=3)
    await engine.sweep_unanchored(db_session, fake_descriptor)
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    entry.attempts = 2  # one failure short of the cap
    await db_session.flush()

    with caplog.at_level("ERROR"):
        touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched[0].status == STATUS_UNAVAILABLE
    assert touched[0].attempts == 3
    assert "PM unreachable" in touched[0].last_error
    # Cap exhaustion is reason="cap_exhausted", distinct from an auth block.
    unavailable = [r for r in caplog.records if r.msg == "powermap_observation_unavailable"]
    assert [r.reason for r in unavailable] == ["cap_exhausted"]


async def test_drain_below_cap_stays_pending(db_session, fake_descriptor):
    """A failure below the cap reschedules and stays PENDING."""
    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=_raise_conn_error)
    engine = SyncEngine([fake_descriptor], client, max_attempts=3)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched[0].status == STATUS_PENDING
    assert touched[0].attempts == 1
    assert touched[0].next_attempt_at == NOW + timedelta(seconds=60)


async def test_drain_unexpected_disposition_counts_toward_cap(db_session, fake_descriptor):
    """The unexpected-disposition path shares the cap — repeated weirdness is not
    an infinite PENDING loop either."""
    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=ObservationResult("bizarre", None, {}))
    engine = SyncEngine([fake_descriptor], client, max_attempts=2)
    await engine.sweep_unanchored(db_session, fake_descriptor)
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    entry.attempts = 1  # one short of the cap
    await db_session.flush()

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched[0].status == STATUS_UNAVAILABLE
    assert "bizarre" in touched[0].last_error


async def test_sweep_does_not_reenqueue_dead_lettered_row(db_session, fake_descriptor):
    """A row whose CREATE already dead-lettered to UNAVAILABLE is NOT re-enqueued by
    the next sweep — otherwise the cap never halts retries and UNAVAILABLE rows pile
    up alongside fresh PENDING siblings (which would also break redrive's unique
    index). Re-running requires an explicit redrive."""
    row = await _add_entity(db_session, source_id="1")
    db_session.add(
        OutboxEntry(entity_type="fake", local_id=row.id, op=OP_CREATE, status=STATUS_UNAVAILABLE)
    )
    await db_session.flush()
    engine = SyncEngine([fake_descriptor], FakeClient())

    count = await engine.sweep_unanchored(db_session, fake_descriptor)

    assert count == 0
    entries = (await db_session.execute(select(OutboxEntry))).scalars().all()
    assert len(entries) == 1  # no PENDING sibling minted
    assert entries[0].status == STATUS_UNAVAILABLE


async def test_dead_letter_then_redrive_is_collision_free(db_session, fake_descriptor):
    """End-to-end: a CREATE that exhausts the cap dead-letters, survives a sweep
    without a duplicate, and redrive returns it to a single PENDING entry."""
    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=_raise_conn_error)
    engine = SyncEngine([fake_descriptor], client, max_attempts=1)
    await engine.sweep_unanchored(db_session, fake_descriptor)
    await engine.drain_outbox(db_session, now=NOW)  # one failure → UNAVAILABLE
    await engine.sweep_unanchored(db_session, fake_descriptor)  # must not dup

    count = await engine.redrive_unavailable(db_session, now=NOW)

    assert count == 1
    entries = (await db_session.execute(select(OutboxEntry))).scalars().all()
    assert len(entries) == 1
    assert entries[0].status == STATUS_PENDING
    assert entries[0].attempts == 0
    assert entries[0].last_error is None


async def _add_outbox(session, *, entity_type="fake", status=STATUS_UNAVAILABLE, created_at=None):
    entry = OutboxEntry(entity_type=entity_type, local_id=ULID(), op=OP_CREATE, status=status)
    session.add(entry)
    await session.flush()
    if created_at is not None:
        entry.created_at = created_at
        await session.flush()
    return entry


async def test_redrive_scopes_by_entity_type(db_session, fake_descriptor):
    """entity_type filter flips only matching UNAVAILABLE rows; others untouched."""
    person = await _add_outbox(db_session, entity_type="person")
    org = await _add_outbox(db_session, entity_type="organization")
    engine = SyncEngine([fake_descriptor], FakeClient())

    count = await engine.redrive_unavailable(db_session, now=NOW, entity_type="person")

    assert count == 1
    await db_session.refresh(person)
    await db_session.refresh(org)
    assert person.status == STATUS_PENDING
    assert org.status == STATUS_UNAVAILABLE


async def test_redrive_scopes_by_age(db_session, fake_descriptor):
    """older_than flips only rows created at/before now - older_than."""
    old = await _add_outbox(db_session, created_at=NOW - timedelta(hours=2))
    fresh = await _add_outbox(db_session, created_at=NOW - timedelta(seconds=1))
    engine = SyncEngine([fake_descriptor], FakeClient())

    count = await engine.redrive_unavailable(db_session, now=NOW, older_than=timedelta(hours=1))

    assert count == 1
    await db_session.refresh(old)
    await db_session.refresh(fresh)
    assert old.status == STATUS_PENDING
    assert fresh.status == STATUS_UNAVAILABLE


async def test_redrive_limit_flips_oldest_first(db_session, fake_descriptor):
    """limit caps the flip count, taking the oldest entries first."""
    oldest = await _add_outbox(db_session, created_at=NOW - timedelta(hours=3))
    middle = await _add_outbox(db_session, created_at=NOW - timedelta(hours=2))
    newest = await _add_outbox(db_session, created_at=NOW - timedelta(hours=1))
    engine = SyncEngine([fake_descriptor], FakeClient())

    count = await engine.redrive_unavailable(db_session, now=NOW, limit=2)

    assert count == 2
    for entry in (oldest, middle, newest):
        await db_session.refresh(entry)
    assert oldest.status == STATUS_PENDING
    assert middle.status == STATUS_PENDING
    assert newest.status == STATUS_UNAVAILABLE  # newest left, limit reached


async def test_redrive_combined_filters(db_session, fake_descriptor):
    """entity_type + age compose; only rows matching both flip."""
    target = await _add_outbox(
        db_session, entity_type="person", created_at=NOW - timedelta(hours=2)
    )
    wrong_type = await _add_outbox(
        db_session, entity_type="organization", created_at=NOW - timedelta(hours=2)
    )
    too_fresh = await _add_outbox(
        db_session, entity_type="person", created_at=NOW - timedelta(seconds=1)
    )
    engine = SyncEngine([fake_descriptor], FakeClient())

    count = await engine.redrive_unavailable(
        db_session, now=NOW, entity_type="person", older_than=timedelta(hours=1)
    )

    assert count == 1
    for entry in (target, wrong_type, too_fresh):
        await db_session.refresh(entry)
    assert target.status == STATUS_PENDING
    assert wrong_type.status == STATUS_UNAVAILABLE
    assert too_fresh.status == STATUS_UNAVAILABLE


async def test_redrive_leaves_rejected_untouched(db_session, fake_descriptor):
    """REJECTED is a payload refusal, never re-driven even under a broad scope."""
    rejected = await _add_outbox(db_session, status=STATUS_REJECTED)
    engine = SyncEngine([fake_descriptor], FakeClient())

    count = await engine.redrive_unavailable(db_session, now=NOW)

    assert count == 0
    await db_session.refresh(rejected)
    assert rejected.status == STATUS_REJECTED


async def test_count_unavailable_matches_scope_without_mutating(db_session, fake_descriptor):
    """count_unavailable reports the scoped match count and changes nothing."""
    person = await _add_outbox(db_session, entity_type="person")
    await _add_outbox(db_session, entity_type="organization")
    engine = SyncEngine([fake_descriptor], FakeClient())

    matched = await engine.count_unavailable(db_session, now=NOW, entity_type="person")

    assert matched == 1
    await db_session.refresh(person)
    assert person.status == STATUS_UNAVAILABLE  # count is non-mutating


async def test_drain_deferral_never_counts_toward_cap(db_session):
    """deps-not-ready deferral leaves attempts untouched, so it can never trip the
    transport-failure cap (it is not a delivery failure)."""

    class _Gated(FakeDescriptor):
        async def dependencies_ready(self, session, row):  # noqa: ARG002
            return False

    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    engine = SyncEngine([_Gated()], client, max_attempts=1)
    await engine.sweep_unanchored(db_session, _Gated())

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched[0].status == STATUS_PENDING  # not UNAVAILABLE despite max_attempts=1
    assert touched[0].attempts == 0


class _AlwaysDeferred(FakeDescriptor):
    """A descriptor whose PM prerequisite never anchors — defers every cycle."""

    async def dependencies_ready(self, session, row):  # noqa: ARG002
        return False


async def test_deferred_too_long_surfaces_stuck_event(db_session, caplog):
    """#15: a deps-not-ready entry defers forever WITHOUT incrementing attempts, so
    the dead-letter cap (which keys on attempts) can never catch it — it is the
    invisible stuck path. When such an entry has been PENDING longer than the
    deferred-stuck threshold, the deferral logs a distinct WARNING the operator can
    alert on (reuses created_at — no schema migration)."""
    row = await _add_entity(db_session, source_id="1")
    # Make the entry look long-deferred by backdating its created_at.
    await engine_sweep_then_age(db_session, row, created_at=NOW - timedelta(hours=48))
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    engine = SyncEngine([_AlwaysDeferred()], client, deferred_stuck_threshold=timedelta(hours=24))

    with caplog.at_level("WARNING"):
        touched = await engine.drain_outbox(db_session, now=NOW)

    entry = touched[0]
    assert entry.status == STATUS_PENDING  # still deferred, not a failure
    assert entry.attempts == 0  # deferral never counts an attempt
    stuck = [r for r in caplog.records if r.msg == "powermap_observation_deferred_stuck"]
    assert len(stuck) == 1
    assert stuck[0].entity_type == "fake"


def test_deferred_stuck_warning_is_throttled_per_entry(caplog):
    """A wedged entry is re-checked every cycle; the stuck WARNING fires once per id
    (then falls back to the routine INFO) rather than re-warning every cycle (#15 CR)."""
    engine = SyncEngine(
        [_AlwaysDeferred()], FakeClient(), deferred_stuck_threshold=timedelta(hours=24)
    )
    entry = OutboxEntry(
        entity_type="fake", local_id=ULID(), op=OP_CREATE, created_at=NOW - timedelta(hours=48)
    )
    entry.id = ULID()
    entry.attempts = 0

    with caplog.at_level("INFO"):
        engine._log_deferral(entry, NOW)  # first sighting → WARNING
        engine._log_deferral(entry, NOW)  # subsequent cycle → routine INFO

    stuck = [r for r in caplog.records if r.msg == "powermap_observation_deferred_stuck"]
    routine = [r for r in caplog.records if r.msg == "powermap_observation_deferred"]
    assert len(stuck) == 1
    assert len(routine) == 1


async def test_recently_deferred_does_not_surface_stuck_event(db_session, caplog):
    """A freshly-deferred entry (within the threshold) logs only the routine INFO
    deferral, not the stuck WARNING — normal in-flight waiting is not noise."""
    row = await _add_entity(db_session, source_id="1")
    await engine_sweep_then_age(db_session, row, created_at=NOW - timedelta(minutes=5))
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    engine = SyncEngine([_AlwaysDeferred()], client, deferred_stuck_threshold=timedelta(hours=24))

    with caplog.at_level("INFO"):
        await engine.drain_outbox(db_session, now=NOW)

    assert [r for r in caplog.records if r.msg == "powermap_observation_deferred_stuck"] == []
    assert [r for r in caplog.records if r.msg == "powermap_observation_deferred"]


async def engine_sweep_then_age(db_session, row, *, created_at):
    """Enqueue a CREATE for ``row`` and backdate its outbox entry's created_at."""
    entry = OutboxEntry(entity_type="fake", local_id=row.id, op=OP_CREATE, created_at=created_at)
    db_session.add(entry)
    await db_session.flush()
    return entry


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


async def test_drain_drops_entry_when_source_missing(db_session, fake_descriptor):
    """An entry whose source row is gone is deleted, not marked DELIVERED."""
    db_session.add(OutboxEntry(entity_type="fake", local_id=ULID(), op=OP_CREATE))
    await db_session.flush()
    engine = SyncEngine([fake_descriptor], FakeClient())

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert touched == []  # dropped entry is not returned
    assert (await db_session.execute(select(OutboxEntry))).first() is None  # and removed


async def test_drain_propagates_non_transient_error(db_session, fake_descriptor):
    """A non-transport error escapes (not masked as a retryable blip)."""

    def _raise_value_error(payload):
        raise ValueError("bug in payload construction")

    await _add_entity(db_session, source_id="1")
    engine = SyncEngine([fake_descriptor], FakeClient(observation_result=_raise_value_error))
    await engine.sweep_unanchored(db_session, fake_descriptor)

    with pytest.raises(ValueError, match="bug in payload"):
        await engine.drain_outbox(db_session, now=NOW)


async def test_backlog_counts_by_status(db_session, fake_descriptor):
    """backlog() exposes the operator view: terminal piles + overdue PENDING +
    oldest-pending age, so stuck entries are visible instead of buried."""
    base = datetime(2099, 1, 1, tzinfo=UTC)
    rows = [
        # status, next_attempt_at, created_at
        (STATUS_PENDING, base - timedelta(hours=1), base - timedelta(hours=3)),  # overdue
        (STATUS_PENDING, base + timedelta(hours=1), base - timedelta(hours=2)),  # not yet due
        (STATUS_REJECTED, base, base - timedelta(hours=5)),
        (STATUS_UNAVAILABLE, base, base - timedelta(hours=5)),
        (STATUS_UNAVAILABLE, base, base - timedelta(hours=5)),
        (STATUS_DELIVERED, base, base - timedelta(hours=9)),  # settled, not backlog
    ]
    for i, (status, nxt, created) in enumerate(rows):
        db_session.add(
            OutboxEntry(
                entity_type="fake",
                local_id=ULID(),
                op=OP_CREATE,
                status=status,
                next_attempt_at=nxt,
                created_at=created,
            )
        )
    await db_session.flush()

    backlog = await outbox_backlog(db_session, now=base)

    assert backlog.pending == 2
    assert backlog.pending_due == 1
    assert backlog.rejected == 1
    assert backlog.unavailable == 2
    # Oldest pending was created 3h before `now`.
    assert backlog.oldest_pending_age_seconds == pytest.approx(3 * 3600)


async def test_backlog_empty_when_no_pending(db_session):
    backlog = await outbox_backlog(db_session, now=NOW)

    assert backlog.pending == 0
    assert backlog.pending_due == 0
    assert backlog.oldest_pending_age_seconds is None


async def test_redrive_resets_unavailable_to_pending(db_session, fake_descriptor):
    """Re-drive returns dead-lettered entries to PENDING and due-now, so the next
    drain re-attempts them once PM has recovered. REJECTED is left alone."""
    base = datetime(2099, 1, 1, tzinfo=UTC)
    unavailable = OutboxEntry(
        entity_type="fake",
        local_id=ULID(),
        op=OP_CREATE,
        status=STATUS_UNAVAILABLE,
        attempts=60,
        next_attempt_at=base + timedelta(hours=1),
        last_error="PM unreachable",
    )
    rejected = OutboxEntry(
        entity_type="fake",
        local_id=ULID(),
        op=OP_CREATE,
        status=STATUS_REJECTED,
        attempts=1,
    )
    db_session.add_all([unavailable, rejected])
    await db_session.flush()
    engine = SyncEngine([fake_descriptor], FakeClient())

    count = await engine.redrive_unavailable(db_session, now=base)

    assert count == 1
    await db_session.refresh(unavailable)
    await db_session.refresh(rejected)
    assert unavailable.status == STATUS_PENDING
    assert unavailable.attempts == 0
    assert unavailable.next_attempt_at == base  # due immediately
    assert rejected.status == STATUS_REJECTED  # untouched — a data bug, not an outage


async def test_drain_commits_per_entry_by_default(db_session, fake_descriptor):
    """#8: when a commit hook is supplied, drain commits after each delivery by
    default (chunk_size=1), so a slow PM never holds one transaction open across
    every round-trip. The commit hook is invoked once per delivered entry."""
    for sid in ("1", "2", "3"):
        await _add_entity(db_session, source_id=sid)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)
    commits = 0

    async def _commit() -> None:
        nonlocal commits
        commits += 1
        await db_session.commit()

    touched = await engine.drain_outbox(db_session, now=NOW, commit=_commit)

    assert len(touched) == 3
    assert commits == 3  # one commit per delivered entry


async def test_drain_commits_per_chunk(db_session, fake_descriptor):
    """A configurable chunk size batches commits: 5 entries at chunk_size=2 →
    commit after 2, after 4, and a final commit for the remaining 1."""
    for sid in ("1", "2", "3", "4", "5"):
        await _add_entity(db_session, source_id=sid)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)
    commits = 0

    async def _commit() -> None:
        nonlocal commits
        commits += 1
        await db_session.commit()

    touched = await engine.drain_outbox(db_session, now=NOW, commit=_commit, chunk_size=2)

    assert len(touched) == 5
    # ceil(5/2) = 3 commit points (2, 2, then a final commit for the last 1).
    assert commits == 3


async def test_drain_without_commit_hook_is_single_transaction(db_session, fake_descriptor):
    """No commit callback → legacy single-transaction behaviour (caller commits)."""
    await _add_entity(db_session, source_id="1")
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, ULID(), {}))
    engine = SyncEngine([fake_descriptor], client)
    await engine.sweep_unanchored(db_session, fake_descriptor)

    touched = await engine.drain_outbox(db_session, now=NOW)

    assert len(touched) == 1
    assert touched[0].status == STATUS_DELIVERED


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
