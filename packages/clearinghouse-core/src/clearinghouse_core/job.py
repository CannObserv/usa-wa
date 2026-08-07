"""Shared job harness (#179) — the run scaffold every operational CLI shares.

Before this module, all ~47 entry points re-implemented the same seven things: read
``DATABASE_URL`` (29 of them straight off ``os.environ``, bypassing
:func:`~clearinghouse_core.config.get_database_url`'s error message), build an
``ArgumentParser``, re-declare ``--dry-run``, create their own engine, wrap the work in
try/except/finally-dispose, ``print`` a bespoke summary, and invent an exit code. Any
cross-cutting change — a run ledger, a timeout policy, a ``degraded`` convention, a
correlation id — was a 47-file edit, so the cheapest way to add a capability was always
script #48.

:func:`run_job` owns that scaffold::

    def main(argv=None) -> int:
        return run_job("sos-results-harvest", harvest, extra_args=_add_args)

and the job shrinks to a handler that takes a :class:`JobContext` and returns counters.

**``degraded`` is the point.** It is a first-class terminal outcome with its own
non-zero exit code (:data:`EXIT_DEGRADED`), so systemd's ``OnFailure=`` fires on a run
that completed but accomplished nothing. Today
``usa_wa_adapter_sos.results.harvest`` detects a total source outage, logs a WARNING,
and returns 0 — a signal with no consumer. A handler reports it by returning
``JobResult.degraded(counters)``: no exception, counters intact, alert raised.

**Contract for handlers.** ``async def handler(ctx: JobContext) -> ...`` returning any
of: a :class:`JobResult`, a mapping, a summary dataclass (``RunSummary``,
``HarvestSummary``, …), or ``None``. The last three are read as ``ok`` with those
counters, so migrating an existing job is usually a delete, not a rewrite.

**Transactions.** ``commit=True`` (the default) commits the session on ``ok`` and on
``degraded`` — a skip-and-continue sweep's partial work is real work — and rolls back
on ``--dry-run`` or failure. Jobs that manage their own transaction, or that write
nothing at all (the PM-authoritative reconcilers), pass ``commit=False``.

**Exit codes.** ``0`` ok · ``1`` failed · ``2`` config error (matches argparse's own
usage exit) · ``3`` is left to jobs with an established "aborted, took no action"
convention, via ``JobResult(..., exit_code=3)`` · ``4`` degraded.
"""

import argparse
import asyncio
import dataclasses
import json
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clearinghouse_core.config import get_database_url, get_settings
from clearinghouse_core.database import dispose_engine, get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runs import (
    OUTCOME_DEGRADED,
    OUTCOME_FAILED,
    OUTCOME_OK,
    close_run,
    normalize_counters,
    open_run,
    record_run,
)

logger = get_logger(__name__)

EXIT_OK = 0
"""Clean run."""

EXIT_FAILED = 1
"""The job raised or reported failure — the non-zero the #49 alert path emails on."""

EXIT_CONFIG = 2
"""Environment/configuration error (missing ``DATABASE_URL``). Matches argparse's own
usage-error exit so "the operator invoked this wrong" is one code, not two."""

EXIT_DEGRADED = 4
"""The run completed but its work did not land (#178). Its own code — never 0 (which
reads as success) and never 1 (which reads as a crash) — chosen clear of the ``3`` the
reconcilers already use for "aborted, took no action"."""

_EXIT_BY_OUTCOME = {
    OUTCOME_OK: EXIT_OK,
    OUTCOME_DEGRADED: EXIT_DEGRADED,
    OUTCOME_FAILED: EXIT_FAILED,
}

ArgsBuilder = Callable[[argparse.ArgumentParser], None]
"""Hook a job uses to contribute its own arguments to the shared parser."""


@dataclass
class JobContext:
    """Everything a handler needs, assembled once by the harness.

    ``session`` is ``None`` only for a job declared ``needs_db=False`` (a write-free
    probe); use :meth:`require_session` to get it typed. ``session_factory`` is there
    for the rarer job that needs a *second*, independent session — one whose
    transaction survives a rollback of the main one.
    """

    name: str
    args: argparse.Namespace
    session: AsyncSession | None
    session_factory: async_sessionmaker[AsyncSession] | None
    dry_run: bool
    json_output: bool = False
    run_id: Any = None

    def require_session(self) -> AsyncSession:
        """Return the job's session, or raise if the job declared it needs no database."""
        if self.session is None:
            raise RuntimeError(
                f"job {self.name!r} asked for a session but was declared needs_db=False"
            )
        return self.session


@dataclass(frozen=True)
class JobResult:
    """A handler's terminal report: an outcome plus the counters that justify it.

    ``exit_code`` overrides the default outcome→code mapping for a job with an
    established convention (the reconcilers' ``3`` for a guardrail abort). The ledger
    still records the honest ``outcome``, so a bespoke exit code never costs the
    ledger its comparability across jobs.
    """

    outcome: str = OUTCOME_OK
    counters: dict[str, Any] = field(default_factory=dict)
    exit_code: int | None = None

    @classmethod
    def ok(cls, counters: Any = None, *, exit_code: int | None = None) -> "JobResult":
        """The work landed."""
        return cls(OUTCOME_OK, normalize_counters(counters), exit_code)

    @classmethod
    def degraded(cls, counters: Any = None, *, exit_code: int | None = None) -> "JobResult":
        """The run completed but its work did not land — alert, don't crash (#178)."""
        return cls(OUTCOME_DEGRADED, normalize_counters(counters), exit_code)

    @classmethod
    def failed(cls, counters: Any = None, *, exit_code: int | None = None) -> "JobResult":
        """The job reports failure without raising."""
        return cls(OUTCOME_FAILED, normalize_counters(counters), exit_code)

    def resolved_exit_code(self) -> int:
        """The process exit code: the job's override, else the outcome's default."""
        if self.exit_code is not None:
            return self.exit_code
        return _EXIT_BY_OUTCOME[self.outcome]


JobHandler = Callable[[JobContext], Awaitable[Any]]
"""``async def handler(ctx) -> JobResult | Mapping | dataclass | None``."""


def _as_result(returned: Any) -> JobResult:
    """Coerce whatever a handler returned into a :class:`JobResult`.

    A bare mapping / summary dataclass / ``None`` reads as ``ok`` with those counters,
    so a job that already builds a summary object migrates without restructuring it.
    """
    if isinstance(returned, JobResult):
        return returned
    if returned is None or isinstance(returned, Mapping) or dataclasses.is_dataclass(returned):
        return JobResult.ok(returned)
    return JobResult.ok({"result": returned})


@lru_cache(maxsize=1)
def _git_sha() -> str | None:
    """The SHA of the running code, for the ledger row.

    ``BUILD_ID`` is the repo's existing convention (stamped by ``usa-wa.service``'s
    ``ExecStartPre``) but the timer-driven oneshots don't set it, so fall back to
    asking git in the working directory the units pin (``WorkingDirectory=``).
    Best-effort: an answer of ``None`` costs a column, never a run.
    """
    build_id = get_settings().build_id
    if build_id and build_id != "dev":
        return build_id[:40]
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = completed.stdout.strip()
    return sha[:40] if completed.returncode == 0 and sha else None


def _build_parser(
    name: str, description: str | None, prog: str | None, extra_args: ArgsBuilder | None
) -> argparse.ArgumentParser:
    """Build the shared parser: base args first, then the job's own."""
    parser = argparse.ArgumentParser(prog=prog, description=description or f"{name} job")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the work but roll back instead of committing.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the run summary as JSON on stdout instead of a human-readable line.",
    )
    if extra_args is not None:
        extra_args(parser)
    return parser


@asynccontextmanager
async def _engine_lifetime() -> AsyncIterator[None]:
    """Guarantee the shared engine is disposed once, after everything that uses it.

    Wraps the *whole* run — the job's session **and** both ledger writes — because the
    ledger deliberately uses its own session off the same shared engine: disposing
    inside the job's session scope would leave the closing ledger write to build a
    second engine that nothing tears down, and asyncio would close the loop on its
    open asyncpg connections.
    """
    try:
        yield
    finally:
        await dispose_engine()


@asynccontextmanager
async def _database() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], AsyncSession]]:
    """Open the job's session — the single seam between the harness and
    :mod:`clearinghouse_core.database`, so no CLI has to build its own engine.
    Disposal belongs to :func:`_engine_lifetime`, which outlives this scope."""
    factory = get_session_factory()
    async with factory() as session:
        yield factory, session


@asynccontextmanager
async def _ledger_session() -> AsyncIterator[AsyncSession]:
    """A session dedicated to the ledger writes.

    Deliberately not the job's session: the ledger must commit independently of — and
    survive a rollback of — the work it is recording.
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def _open_ledger_row(name: str, started_at: datetime) -> Any:
    """Best-effort in-flight ledger row. Returns its id, or ``None`` if the write failed."""
    try:
        async with _ledger_session() as session:
            return await open_run(session, job_slug=name, started_at=started_at, git_sha=_git_sha())
    except Exception as exc:  # noqa: BLE001 — observability must never fail the job it observes
        logger.warning("job_ledger_open_failed", extra={"job": name, "error": repr(exc)})
        return None


async def _close_ledger_row(
    name: str, run_id: Any, started_at: datetime, result: JobResult
) -> None:
    """Best-effort terminal ledger write; falls back to a one-shot insert when the
    opening write never landed, so the run is still recorded."""
    try:
        async with _ledger_session() as session:
            if run_id is None:
                await record_run(
                    session,
                    job_slug=name,
                    started_at=started_at,
                    outcome=result.outcome,
                    counters=result.counters,
                    git_sha=_git_sha(),
                )
            else:
                await close_run(session, run_id, outcome=result.outcome, counters=result.counters)
    except Exception as exc:  # noqa: BLE001 — observability must never fail the job it observes
        logger.warning("job_ledger_close_failed", extra={"job": name, "error": repr(exc)})


def _render_human(name: str, result: JobResult, duration_ms: int, dry_run: bool) -> str:
    """One ``key=value`` line. Non-scalar counters render as compact JSON so a nested
    detail list (the integrity sweep's mismatches) stays on the line rather than
    printing a Python repr."""
    parts = [f"job={name}", f"outcome={result.outcome}", f"duration_ms={duration_ms}"]
    if dry_run:
        parts.append("dry_run=true")
    for key, value in result.counters.items():
        if isinstance(value, bool) or value is None:
            # JSON casing throughout, so the line and the --json form agree.
            parts.append(f"{key}={json.dumps(value)}")
        elif isinstance(value, (str, int, float)):
            parts.append(f"{key}={value}")
        else:
            parts.append(f"{key}={json.dumps(value, separators=(',', ':'), default=str)}")
    return " ".join(parts)


def _emit(name: str, result: JobResult, duration_ms: int, ctx_dry_run: bool, as_json: bool) -> None:
    """Emit the run summary to stdout and, always, as one structured log record.

    The log record carries the full counters regardless of the stdout format, so
    journald keeps the machine-readable form even for a human-formatted run.
    """
    payload = {
        "job": name,
        "outcome": result.outcome,
        "counters": result.counters,
        "duration_ms": duration_ms,
        "dry_run": ctx_dry_run,
        "exit_code": result.resolved_exit_code(),
    }
    log = logger.info if result.outcome == OUTCOME_OK else logger.warning
    log("job_finished", extra=payload)
    if as_json:
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_render_human(name, result, duration_ms, ctx_dry_run) + "\n")


async def _execute(
    name: str,
    handler: JobHandler,
    args: argparse.Namespace,
    *,
    needs_db: bool,
    commit: bool,
    ledger: bool,
    started_at: datetime,
) -> JobResult:
    """Run the handler inside the harness's resource + transaction envelope."""

    async def _invoke(
        factory: async_sessionmaker[AsyncSession] | None,
        session: AsyncSession | None,
        run_id: Any,
    ) -> JobResult:
        ctx = JobContext(
            name=name,
            args=args,
            session=session,
            session_factory=factory,
            dry_run=args.dry_run,
            json_output=args.json_output,
            run_id=run_id,
        )
        try:
            result = _as_result(await handler(ctx))
        except Exception:
            logger.exception("job_failed", extra={"job": name})
            if session is not None and commit:
                await session.rollback()
            return JobResult.failed()
        if session is not None and commit:
            if args.dry_run or result.outcome == OUTCOME_FAILED:
                await session.rollback()
            else:
                # ok AND degraded commit: a skip-and-continue sweep's reached work is
                # real work, and rolling it back would punish the very resilience the
                # degraded signal exists to report.
                await session.commit()
        return result

    # One engine lifetime spans the opening ledger write, the work, and the closing
    # ledger write — all three share the process-global engine, so disposal has to
    # outlive the last of them.
    async with _engine_lifetime():
        run_id = await _open_ledger_row(name, started_at) if ledger else None
        try:
            if needs_db:
                async with _database() as (factory, session):
                    result = await _invoke(factory, session, run_id)
            else:
                result = await _invoke(None, None, run_id)
        except Exception:
            # Resource-level failure — the engine or session could not be
            # established. The handler's own failures are caught inside _invoke.
            logger.exception("job_failed", extra={"job": name})
            result = JobResult.failed()
        if ledger:
            await _close_ledger_row(name, run_id, started_at, result)
    return result


def run_job(
    name: str,
    handler: JobHandler,
    *,
    argv: list[str] | None = None,
    description: str | None = None,
    prog: str | None = None,
    extra_args: ArgsBuilder | None = None,
    needs_db: bool = True,
    commit: bool = True,
    ledger: bool = True,
) -> int:
    """Run ``handler`` as job ``name`` and return the process exit code.

    ``name`` is the ledger's ``job_slug`` — a stable identity (``"integrity-sweep"``),
    not the module path. ``extra_args`` receives the shared parser to add the job's own
    arguments; ``--dry-run`` and ``--json`` are always present. ``needs_db=False`` skips
    the session entirely (write-free probes); ``commit=False`` leaves the transaction to
    the handler; ``ledger=False`` skips the #178 row.

    Never raises for a handler failure: an exception is logged, recorded as ``failed``,
    and returned as :data:`EXIT_FAILED`, so the operator gets one actionable line and a
    traceback in the log rather than a bare traceback on stderr.
    """
    configure_logging()
    args = _build_parser(name, description, prog, extra_args).parse_args(argv)

    if needs_db:
        try:
            get_database_url()
        except RuntimeError as exc:
            # One env-resolution path for every job — not 29 raw os.environ reads, each
            # with its own message and its own exit code.
            logger.error("job_config_error", extra={"job": name, "error": str(exc)})
            print(str(exc), file=sys.stderr)
            return EXIT_CONFIG

    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    result = asyncio.run(
        _execute(
            name,
            handler,
            args,
            needs_db=needs_db,
            commit=commit,
            ledger=ledger,
            started_at=started_at,
        )
    )
    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    _emit(name, result, duration_ms, args.dry_run, args.json_output)
    return result.resolved_exit_code()
