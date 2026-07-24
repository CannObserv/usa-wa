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

import pytest
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


async def test_threshold_below_one_is_rejected(fake_descriptor):
    """CR-1: a ``0``/negative threshold would flag on the first stable re-observe AND make
    ``nonconverging_count``'s ``count >= threshold`` match every *reset* (count 0) row —
    turning the rise-alert into a per-cycle flood naming converged rows. Validated at
    construction like the sibling ``sweep_batch_size`` knob."""
    for bad in (0, -1):
        with pytest.raises(ValueError, match="nonconvergence_threshold"):
            SyncEngine([fake_descriptor], FakeClient(), nonconvergence_threshold=bad)


async def test_flag_warning_is_throttled_per_row(db_session, fake_descriptor, caplog):
    """CR-3: a flagged row re-flags on every drain, so the WARNING is emitted once per row
    per process (the ``_warned_stuck``/``_warned_dead_anchors`` convention) — one actionable
    signal, not 305 lines a drain for a #110-sized cohort. The per-drain stat still counts
    every occurrence (it is a volume tally), and the standing count stays visible."""
    pm_id = ULID()
    row = await _anchored_entity(db_session, anchor=pm_id)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client, nonconvergence_threshold=3)

    with caplog.at_level("INFO"):
        for _ in range(4):  # drains 3 and 4 both flag; only the first WARNs
            await _enqueue_update(db_session, row)
            await engine.drain_outbox(db_session, now=NOW)

    warnings = [r for r in caplog.records if r.message == "observation_not_converging"]
    assert len(warnings) == 1
    assert warnings[0].levelname == "WARNING"
    # The 4th drain still flagged — throttled to INFO, not dropped.
    repeats = [r for r in caplog.records if r.message == "observation_still_not_converging"]
    assert len(repeats) == 1
    assert repeats[0].levelname == "INFO"
    assert engine.last_drain_stats.non_converging == 1  # per-drain tally unaffected
    assert (await _state(db_session, row)).count == 4


async def test_flag_warning_rearms_after_the_row_converges(db_session, fake_descriptor, caplog):
    """CR-9: the throttle must RE-ARM. A row that churns → is flagged → gets fixed → churns
    again on a new payload is a second, genuinely-new episode and must WARN again — the
    standing count re-arms (0 → 1 rise → email), and that email tells the operator to grep
    for the WARNING, so the two must not disagree."""
    pm_id = ULID()
    row = await _anchored_entity(db_session, name="x", anchor=pm_id)
    client = FakeClient(observation_result=ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}))
    engine = SyncEngine([fake_descriptor], client, nonconvergence_threshold=3)

    with caplog.at_level("INFO"):
        for _ in range(3):  # episode 1 → flagged
            await _enqueue_update(db_session, row)
            await engine.drain_outbox(db_session, now=NOW)
        # Operator fixes the diff: a changed payload resets the counter (and the throttle).
        row.name = "y"
        await db_session.flush()
        await _enqueue_update(db_session, row)
        await engine.drain_outbox(db_session, now=NOW)
        assert (await _state(db_session, row)).count == 1
        for _ in range(2):  # episode 2 on the NEW payload → flagged again
            await _enqueue_update(db_session, row)
            await engine.drain_outbox(db_session, now=NOW)

    warnings = [r for r in caplog.records if r.message == "observation_not_converging"]
    assert len(warnings) == 2, "a re-armed non-convergence must WARN again, not stay throttled"


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
