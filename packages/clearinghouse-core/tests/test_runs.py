"""Tests for the job-run ledger (#178).

Covers the three writes the harness makes — open an in-flight row, close it with a
terminal outcome, and the single-shot ``record_run`` fallback — plus the counter
normalization that keeps a JSONB write from blowing up on a dataclass or a datetime.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from ulid import ULID as _ULID

from clearinghouse_core.runs import (
    OUTCOME_DEGRADED,
    OUTCOME_FAILED,
    OUTCOME_OK,
    OUTCOMES,
    JobRun,
    close_run,
    normalize_counters,
    open_run,
    record_run,
)


async def test_open_run_inserts_an_in_flight_row(db_session):
    """``open_run`` stamps started_at and leaves the terminal fields NULL — a row with
    ``finished_at IS NULL`` is exactly the "job never reported back" query."""
    run_id = await open_run(db_session, job_slug="demo-job")

    row = await db_session.get(JobRun, run_id)
    assert isinstance(run_id, _ULID)
    assert row.job_slug == "demo-job"
    assert row.started_at is not None
    assert row.finished_at is None
    assert row.outcome is None
    assert row.host


async def test_close_run_stamps_outcome_and_counters(db_session):
    """``close_run`` turns the in-flight row terminal, landing the job's own summary
    counters in the JSONB column."""
    run_id = await open_run(db_session, job_slug="demo-job")

    await close_run(db_session, run_id, outcome=OUTCOME_OK, counters={"archived": 3})

    row = await db_session.get(JobRun, run_id)
    await db_session.refresh(row)
    assert row.outcome == OUTCOME_OK
    assert row.finished_at is not None
    assert row.counters == {"archived": 3}


async def test_close_run_tolerates_an_unknown_run_id(db_session):
    """A close against a row that was never opened (the open write itself failed) is a
    no-op, never an exception — the ledger must not be able to fail a job."""
    await close_run(db_session, _ULID(), outcome=OUTCOME_OK, counters={})


async def test_record_run_writes_a_complete_row_in_one_shot(db_session):
    """The fallback path when the opening write failed: one INSERT carrying both ends."""
    started = datetime.now(UTC)
    run_id = await record_run(
        db_session,
        job_slug="demo-job",
        started_at=started,
        outcome=OUTCOME_DEGRADED,
        counters={"skipped": 9},
    )

    row = await db_session.get(JobRun, run_id)
    assert row.outcome == OUTCOME_DEGRADED
    assert row.started_at is not None
    assert row.finished_at is not None
    assert row.counters == {"skipped": 9}


async def test_outcome_is_constrained_to_the_three_terminal_states(db_session):
    """``degraded`` is a first-class outcome and anything else is rejected at the DB —
    the ledger's vocabulary can't drift per-job (#178)."""
    assert OUTCOMES == (OUTCOME_OK, OUTCOME_DEGRADED, OUTCOME_FAILED)
    run_id = await open_run(db_session, job_slug="demo-job")

    with pytest.raises(IntegrityError, match="ck_job_runs_outcome"):
        await db_session.execute(
            text(
                "UPDATE clearinghouse_core.job_runs SET outcome = 'weird' WHERE id = :id"
            ).bindparams(id=run_id.to_uuid())
        )


async def test_job_slug_is_queryable_for_staleness(db_session):
    """ "When did each job last finish ok?" is a plain query over the ledger."""
    first = await open_run(db_session, job_slug="demo-job")
    await close_run(db_session, first, outcome=OUTCOME_OK, counters={})
    second = await open_run(db_session, job_slug="demo-job")
    await close_run(db_session, second, outcome=OUTCOME_FAILED, counters={})

    rows = (
        (
            await db_session.execute(
                select(JobRun)
                .where(JobRun.job_slug == "demo-job", JobRun.outcome == OUTCOME_OK)
                .order_by(JobRun.started_at.desc())
            )
        )
        .scalars()
        .all()
    )
    assert [r.id for r in rows] == [first]


# --- counter normalization ---------------------------------------------------


@dataclass(frozen=True)
class _Summary:
    archived: int
    skipped: int


def test_normalize_counters_accepts_a_summary_dataclass():
    """The job summaries this repo already builds (RunSummary, HarvestSummary, …) land
    in the ledger without a per-job adapter."""
    assert normalize_counters(_Summary(archived=2, skipped=1)) == {"archived": 2, "skipped": 1}


def test_normalize_counters_accepts_a_mapping_and_none():
    assert normalize_counters({"a": 1}) == {"a": 1}
    assert normalize_counters(None) == {}


def test_normalize_counters_makes_values_json_safe():
    """A datetime or a ULID in a summary must not fail the JSONB write."""
    ulid = _ULID()
    out = normalize_counters({"at": datetime(2026, 1, 2, tzinfo=UTC), "id": ulid})
    assert out["at"].startswith("2026-01-02")
    assert out["id"] == str(ulid)
