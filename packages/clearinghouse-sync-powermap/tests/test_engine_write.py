"""Engine write-path tests — sweep + outbox worker (engine step 3)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import (
    DeliveryBlockedError,
    ObservationResult,
    PayloadRejectedError,
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
