"""Non-convergence backstop tests (usa-wa#112).

An anchored row whose reconcile re-observation PM keeps ``auto-attached`` WITHOUT
applying our diff (the #110 role-classifier churn, power-map#311b before #111)
re-sends an identical payload every reconcile cycle forever — silent until a
manual outbox audit. The backstop counts consecutive identical ``auto-attached``
re-sends per row (a persisted counter keyed on ``(entity_type, local_id)``, since
each reconcile mints a *fresh* DELIVERED entry) and, past a threshold, surfaces
the row as an operator-visible, alerting standing count. A changed payload — a
genuine new local edit — resets the counter and re-arms.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import ObservationResult
from clearinghouse_sync_powermap.engine import SyncEngine, nonconverging_count
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_NEW,
    OP_UPDATE,
    NonConvergenceState,
    OutboxEntry,
)
from clearinghouse_sync_powermap.testing import FakeClient, FakeEntity

NOW = datetime(2099, 1, 1, tzinfo=UTC)


async def _anchored_entity(session, *, source_id="1", name="x", anchor):
    row = FakeEntity(source="wsl", source_id=source_id, name=name, pm_fake_id=anchor)
    session.add(row)
    await session.flush()
    return row


async def _enqueue_update(session, row) -> OutboxEntry:
    """Mint a fresh OP_UPDATE for an anchored row — the per-reconcile re-enqueue."""
    entry = OutboxEntry(entity_type="fake", local_id=row.id, op=OP_UPDATE)
    session.add(entry)
    await session.flush()
    return entry


async def _state(session, row) -> NonConvergenceState | None:
    return await session.scalar(
        select(NonConvergenceState).where(
            NonConvergenceState.entity_type == "fake",
            NonConvergenceState.local_id == row.id,
        )
    )


async def test_repeated_identical_auto_attach_accrues_and_flags(
    db_session, fake_descriptor, caplog
):
    """N consecutive identical ``auto-attached`` re-sends of an already-anchored row
    climb the counter and, at the threshold, flag the row (drain stat + WARNING)."""
    pm_id = ULID()
    row = await _anchored_entity(db_session, anchor=pm_id)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client, nonconvergence_threshold=3)

    with caplog.at_level("WARNING"):
        for _ in range(3):
            await _enqueue_update(db_session, row)
            await engine.drain_outbox(db_session, now=NOW)

    state = await _state(db_session, row)
    assert state is not None
    assert state.count == 3
    # The final drain crossed the threshold → flagged once this drain.
    assert engine.last_drain_stats.non_converging == 1
    assert any(r.message == "observation_not_converging" for r in caplog.records)
    # Standing query surfaces the row for the cycle summary.
    assert await nonconverging_count(db_session, threshold=3) == 1


async def test_below_threshold_does_not_flag(db_session, fake_descriptor):
    pm_id = ULID()
    row = await _anchored_entity(db_session, anchor=pm_id)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client, nonconvergence_threshold=3)

    for _ in range(2):
        await _enqueue_update(db_session, row)
        await engine.drain_outbox(db_session, now=NOW)

    state = await _state(db_session, row)
    assert state.count == 2
    assert engine.last_drain_stats.non_converging == 0
    assert await nonconverging_count(db_session, threshold=3) == 0


async def test_changed_payload_resets_and_rearms(db_session, fake_descriptor):
    """A genuine new local edit (changed payload) resets the counter — the re-arm:
    a provably-futile identical re-send is caught, but a real change still propagates."""
    pm_id = ULID()
    row = await _anchored_entity(db_session, name="x", anchor=pm_id)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client, nonconvergence_threshold=3)

    for _ in range(2):
        await _enqueue_update(db_session, row)
        await engine.drain_outbox(db_session, now=NOW)
    assert (await _state(db_session, row)).count == 2

    # A real local edit changes the observation payload → counter resets to 1.
    row.name = "y"
    await db_session.flush()
    await _enqueue_update(db_session, row)
    await engine.drain_outbox(db_session, now=NOW)

    state = await _state(db_session, row)
    assert state.count == 1
    assert engine.last_drain_stats.non_converging == 0


async def test_reanchor_resets_counter(db_session, fake_descriptor):
    """An ``auto-attached`` that resolves to a *different* pm_id is a #108 genuine
    change (a start-date correction minting a fresh PM id), not futile churn — reset."""
    pm_id = ULID()
    row = await _anchored_entity(db_session, anchor=pm_id)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client, nonconvergence_threshold=3)
    for _ in range(2):
        await _enqueue_update(db_session, row)
        await engine.drain_outbox(db_session, now=NOW)
    assert (await _state(db_session, row)).count == 2

    # Next delivery re-anchors to a new id.
    new_pm_id = ULID()
    client._observation_result = ObservationResult(DISPOSITION_AUTO_ATTACHED, new_pm_id, {})
    await _enqueue_update(db_session, row)
    await engine.drain_outbox(db_session, now=NOW)

    assert (await _state(db_session, row)).count == 0
    assert engine.last_drain_stats.non_converging == 0


async def test_first_attach_does_not_accrue(db_session, fake_descriptor):
    """A first anchor (CREATE that PM auto-attaches by name) is not a re-send —
    old anchor is None — so it never accrues the churn counter."""
    row = await _anchored_entity(db_session, anchor=None)
    pm_id = ULID()
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client, nonconvergence_threshold=3)
    await engine.sweep_unanchored(db_session, fake_descriptor)
    await engine.drain_outbox(db_session, now=NOW)

    assert row.pm_fake_id == pm_id
    assert await _state(db_session, row) is None
    assert engine.last_drain_stats.non_converging == 0


async def test_new_disposition_does_not_accrue(db_session, fake_descriptor):
    """A ``new`` disposition mints a PM record (genuine change) — never futile churn."""
    pm_id = ULID()
    row = await _anchored_entity(db_session, anchor=pm_id)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_NEW, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client, nonconvergence_threshold=3)
    for _ in range(3):
        await _enqueue_update(db_session, row)
        await engine.drain_outbox(db_session, now=NOW)

    # `new` on the same id is not the stable-auto-attach signature → no accrual.
    state = await _state(db_session, row)
    assert state is None or state.count == 0
    assert engine.last_drain_stats.non_converging == 0
