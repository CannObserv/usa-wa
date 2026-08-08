"""Job-run ledger (#178) — one row per execution of an operational job.

Nothing in this system recorded "job X ran at T, took D, processed N, ended in state
S". Alerting is exit-code driven (``OnFailure=usa-wa-notify-failure@``), so the failure
mode that actually bites — **a job that exits 0 having silently done nothing** — was
invisible by construction. ``usa_wa_adapter_sos.results.harvest`` names it exactly: a
whole-source outage logs a WARNING and returns 0, and nothing consumes that signal.

This table is the missing record. Three things make it useful:

- **``outcome`` has three terminal values, not two.** ``degraded`` sits between ``ok``
  and ``failed``: the job ran to completion but its work did not land. A CHECK
  constraint pins the vocabulary so it cannot drift per-job. The harness
  (:mod:`clearinghouse_core.job`) maps ``degraded`` to its own non-zero exit code, so
  systemd's ``OnFailure=`` fires on it like any other failure.
- **``counters`` is JSONB.** Every job in this repo already builds a summary object
  (``RunSummary``, ``HarvestSummary``, ``SweepReport``, a plain dict) that has nowhere
  to land. :func:`normalize_counters` accepts any of them.
- **A row is opened before the work and closed after it.** A row with
  ``finished_at IS NULL`` is a job that was killed, hung, or crashed hard — a state a
  write-at-the-end ledger cannot represent. It also makes staleness a plain query:
  "when did each job last finish ``ok``?"

The writes are deliberately forgiving — :func:`close_run` no-ops on an id it cannot
find, and the harness runs every ledger write best-effort. Observability must never
become a new way for a job to fail.
"""

import dataclasses
import json
import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "clearinghouse_core"

OUTCOME_OK = "ok"
"""The job ran and its work landed."""

OUTCOME_DEGRADED = "degraded"
"""The job ran to completion but its work did not land — a total source outage, an
aborted guardrail, an empty pull. Distinct from ``ok`` (the work landed) and from
``failed`` (the job raised, or reported a condition it considers a failure)."""

OUTCOME_FAILED = "failed"
"""The job raised, or reported a condition it considers a failure."""

OUTCOMES = (OUTCOME_OK, OUTCOME_DEGRADED, OUTCOME_FAILED)
"""The closed vocabulary, in escalation order. Enforced by a CHECK constraint."""

OUTCOME_CHECK_NAME = "ck_job_runs_outcome"
"""Name of the CHECK constraint pinning :data:`OUTCOMES`. Shared with the migration."""


def outcome_check_sql() -> str:
    """Render the ``outcome`` CHECK expression **from** :data:`OUTCOMES`.

    The vocabulary is declared once. Previously the tuple and the constraint were
    independent copies of the same list, so adding a fourth outcome would have updated
    the Python side only and surfaced as an ``IntegrityError`` in production
    (CR #191 finding 5). ``tests/test_runs.py`` pins this expression against the
    migration's own copy, which alembic cannot import without creating a cycle.
    """
    values = ", ".join(f"'{outcome}'" for outcome in OUTCOMES)
    return f"outcome IS NULL OR outcome IN ({values})"


def _new_ulid() -> _ULID:
    """Default factory for the ULID PK column."""
    return _ULID()


class JobRun(Base, TimestampMixin):
    """One execution of one operational job (#178).

    ``outcome`` and ``finished_at`` are NULL while the run is in flight; a row that
    stays that way is a job that never reported back (killed, OOM, hung past
    ``TimeoutStartSec=``) — the case a write-at-the-end ledger silently drops.

    ``job_slug`` is the stable job identity (``"integrity-sweep"``,
    ``"sos-results-harvest"``), not the module path: a module can move without
    orphaning its run history. ``git_sha`` and ``host`` answer "which code, which box"
    for a run whose counters look wrong.
    """

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint(outcome_check_sql(), name=OUTCOME_CHECK_NAME),
        Index("ix_job_runs_job_slug_started_at", "job_slug", "started_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    job_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    counters: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)


def normalize_counters(counters: Any) -> dict[str, Any]:
    """Coerce a job's summary object into a JSONB-safe dict.

    Accepts ``None``, a mapping, or a dataclass instance — the three shapes the repo's
    existing summaries take. Values are round-tripped through JSON with ``default=str``
    so a ``datetime``, a ``ULID``, or a nested dataclass can't fail the insert; the
    ledger write is best-effort and a serialization error must not surface as a job
    failure.
    """
    if counters is None:
        raw: Any = {}
    elif isinstance(counters, Mapping):
        raw = dict(counters)
    elif dataclasses.is_dataclass(counters) and not isinstance(counters, type):
        raw = dataclasses.asdict(counters)
    else:
        raw = {"value": counters}
    return json.loads(json.dumps(raw, default=str))


async def open_run(
    session: AsyncSession,
    *,
    job_slug: str,
    started_at: datetime | None = None,
    git_sha: str | None = None,
    host: str | None = None,
) -> _ULID:
    """Insert an in-flight run row and return its id.

    Commits immediately: the row's job is to exist even if the process is killed
    mid-run, so it cannot ride the job's own transaction.
    """
    row = JobRun(
        job_slug=job_slug,
        started_at=started_at or datetime.now(UTC),
        git_sha=git_sha,
        host=host or socket.gethostname(),
    )
    session.add(row)
    await session.commit()
    return row.id


async def close_run(
    session: AsyncSession,
    run_id: _ULID,
    *,
    outcome: str,
    counters: Any = None,
    finished_at: datetime | None = None,
) -> None:
    """Stamp an in-flight run row terminal.

    No-ops when ``run_id`` names no row — the opening write may have failed, and a
    close that raised would turn the ledger into a failure mode of the job it observes.
    """
    row = await session.scalar(select(JobRun).where(JobRun.id == run_id))
    if row is None:
        return
    row.finished_at = finished_at or datetime.now(UTC)
    row.outcome = outcome
    row.counters = normalize_counters(counters)
    await session.commit()


async def record_run(
    session: AsyncSession,
    *,
    job_slug: str,
    started_at: datetime,
    outcome: str,
    counters: Any = None,
    finished_at: datetime | None = None,
    git_sha: str | None = None,
    host: str | None = None,
) -> _ULID:
    """Insert a complete run row in one shot.

    The fallback for when the opening write failed (a DB blip at start): the run still
    gets a truthful record rather than vanishing because its bookend is missing.
    """
    row = JobRun(
        job_slug=job_slug,
        started_at=started_at,
        finished_at=finished_at or datetime.now(UTC),
        outcome=outcome,
        counters=normalize_counters(counters),
        git_sha=git_sha,
        host=host or socket.gethostname(),
    )
    session.add(row)
    await session.commit()
    return row.id
