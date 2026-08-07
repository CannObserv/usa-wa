"""Tests for the shared job harness (#179).

The harness is the single place a CLI's run scaffold lives: env resolution, engine
lifecycle, base args, summary emission, exit-code mapping, and the #178 ledger write.
These tests pin the contract ~47 entry points will be migrated onto, so they exercise
the three handler shapes the repo actually has (a sweep that skips-and-continues, a
refresh that returns a summary dataclass, a reconciler that owns its own exit codes).
"""

import argparse
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from ulid import ULID as _ULID

from clearinghouse_core import job as job_module
from clearinghouse_core.job import (
    EXIT_CONFIG,
    EXIT_DEGRADED,
    EXIT_FAILED,
    EXIT_OK,
    JobContext,
    JobResult,
    run_job,
)
from clearinghouse_core.runs import OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK


def _summary(capsys) -> dict:
    """The run summary is the LAST stdout line: ``configure_logging()`` sends the
    structured log records to stdout too, so a naive whole-buffer parse trips over them."""
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    return json.loads(lines[-1])


class _FakeSession:
    """Minimal AsyncSession stand-in recording the harness's transaction decisions."""

    def __init__(self) -> None:
        self.committed = 0
        self.rolled_back = 0

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rolled_back += 1


@pytest.fixture
def fake_db(monkeypatch):
    """Replace the harness's database seam with an in-memory session."""
    session = _FakeSession()

    @asynccontextmanager
    async def _fake_database():
        yield (None, session)

    monkeypatch.setattr(job_module, "_database", _fake_database)
    monkeypatch.setattr(job_module, "get_database_url", lambda: "postgresql+asyncpg://x/y")
    return session


@pytest.fixture(autouse=True)
def ledger_calls(monkeypatch):
    """Record ledger writes instead of performing them.

    Autouse and unconditional: no test in this module may open a real connection —
    the harness resolves ``DATABASE_URL``, which in a dev shell points at production.
    """
    calls: list[tuple] = []

    async def _open(_session, **kwargs):
        calls.append(("open", kwargs))
        return _ULID()

    async def _close(_session, _run_id, **kwargs):
        calls.append(("close", kwargs))

    async def _record(_session, **kwargs):
        calls.append(("record", kwargs))
        return None

    @asynccontextmanager
    async def _ledger_session():
        yield _FakeSession()

    monkeypatch.setattr(job_module, "open_run", _open)
    monkeypatch.setattr(job_module, "close_run", _close)
    monkeypatch.setattr(job_module, "record_run", _record)
    monkeypatch.setattr(job_module, "_ledger_session", _ledger_session)
    return calls


# --- base args ---------------------------------------------------------------


def test_base_args_are_available_to_every_job(fake_db):
    """--dry-run and --json exist without the job declaring them (#179)."""
    seen: dict = {}

    async def handler(ctx: JobContext) -> JobResult:
        seen["dry_run"] = ctx.dry_run
        seen["json"] = ctx.json_output
        return JobResult.ok()

    assert run_job("demo", handler, argv=["--dry-run", "--json"]) == EXIT_OK
    assert seen == {"dry_run": True, "json": True}


def test_extra_args_hook_adds_job_specific_flags(fake_db):
    """A job contributes its own arguments through one hook, not its own parser."""

    def extra_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--from-year", type=int, default=2008)

    seen: dict = {}

    async def handler(ctx: JobContext) -> JobResult:
        seen["from_year"] = ctx.args.from_year
        return JobResult.ok()

    assert run_job("demo", handler, argv=["--from-year", "2020"], extra_args=extra_args) == EXIT_OK
    assert seen == {"from_year": 2020}


# --- outcome / exit-code mapping ---------------------------------------------


def test_ok_exits_zero(fake_db):
    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok({"archived": 1})

    assert run_job("demo", handler, argv=[]) == EXIT_OK


def test_degraded_gets_its_own_nonzero_exit_code(fake_db, capsys):
    """The point of #178/#179: a job that did nothing useful must be distinguishable
    from success AND from a crash, and must trip systemd ``OnFailure=``."""

    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.degraded({"cohorts_skipped": 18})

    code = run_job("demo", handler, argv=["--json"])

    assert code == EXIT_DEGRADED
    assert code != EXIT_OK
    assert code != EXIT_FAILED
    assert _summary(capsys)["outcome"] == OUTCOME_DEGRADED


def test_failed_result_exits_one(fake_db):
    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.failed({"mismatched": 2})

    assert run_job("demo", handler, argv=[]) == EXIT_FAILED


def test_handler_exception_is_a_failed_run_not_a_traceback(fake_db, capsys):
    async def handler(ctx: JobContext) -> JobResult:
        raise ValueError("boom")

    code = run_job("demo", handler, argv=["--json"])

    assert code == EXIT_FAILED
    assert _summary(capsys)["outcome"] == OUTCOME_FAILED


def test_explicit_exit_code_overrides_the_default_mapping(fake_db):
    """Jobs with an established code (the reconcilers' EXIT_ABORTED=3) keep it while
    still recording a truthful ledger outcome."""

    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.failed({"aborted": "cohort_floor"}, exit_code=3)

    assert run_job("demo", handler, argv=[]) == 3


# --- handler return coercion -------------------------------------------------


@dataclass(frozen=True)
class _Summary:
    upserted: int


def test_handler_may_return_none(fake_db, capsys):
    async def handler(ctx: JobContext) -> None:
        return None

    assert run_job("demo", handler, argv=["--json"]) == EXIT_OK
    assert _summary(capsys)["counters"] == {}


def test_handler_may_return_a_bare_mapping(fake_db, capsys):
    async def handler(ctx: JobContext) -> dict:
        return {"present": 34}

    assert run_job("demo", handler, argv=["--json"]) == EXIT_OK
    assert _summary(capsys)["counters"] == {"present": 34}


def test_handler_may_return_a_summary_dataclass(fake_db, capsys):
    """The existing RunSummary / HarvestSummary objects become counters as-is."""

    async def handler(ctx: JobContext) -> _Summary:
        return _Summary(upserted=7)

    assert run_job("demo", handler, argv=["--json"]) == EXIT_OK
    assert _summary(capsys)["counters"] == {"upserted": 7}


# --- summary emission --------------------------------------------------------


def test_json_summary_carries_job_outcome_and_duration(fake_db, capsys):
    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok({"archived": 1})

    run_job("demo-job", handler, argv=["--json"])

    payload = _summary(capsys)
    assert payload["job"] == "demo-job"
    assert payload["outcome"] == OUTCOME_OK
    assert payload["counters"] == {"archived": 1}
    assert payload["duration_ms"] >= 0
    assert payload["dry_run"] is False


def test_default_summary_is_a_human_key_value_line(fake_db, capsys):
    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok({"archived": 1, "skipped": 0})

    run_job("demo-job", handler, argv=[])

    out = capsys.readouterr().out
    assert "demo-job" in out
    assert "outcome=ok" in out
    assert "archived=1" in out
    assert "skipped=0" in out


# --- transaction policy ------------------------------------------------------


def test_success_commits_the_session(fake_db):
    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok()

    run_job("demo", handler, argv=[])
    assert fake_db.committed == 1
    assert fake_db.rolled_back == 0


def test_degraded_still_commits_the_work_that_landed(fake_db):
    """A skip-and-continue sweep's reached years must persist even though the run is
    reported degraded (the sos results harvest's per-year resilience)."""

    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.degraded({"cohorts_skipped": 3})

    run_job("demo", handler, argv=[])
    assert fake_db.committed == 1


def test_failure_rolls_back(fake_db):
    async def handler(ctx: JobContext) -> JobResult:
        raise RuntimeError("boom")

    run_job("demo", handler, argv=[])
    assert fake_db.committed == 0
    assert fake_db.rolled_back == 1


def test_dry_run_rolls_back(fake_db):
    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok()

    run_job("demo", handler, argv=["--dry-run"])
    assert fake_db.committed == 0
    assert fake_db.rolled_back == 1


def test_commit_false_leaves_the_transaction_to_the_handler(fake_db):
    """Read-only reconcilers (PM is authority; nothing local is mutated) opt out."""

    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok()

    run_job("demo", handler, argv=[], commit=False)
    assert fake_db.committed == 0
    assert fake_db.rolled_back == 0


# --- env resolution ----------------------------------------------------------


def test_missing_database_url_is_a_config_exit_not_a_crash(monkeypatch, capsys):
    """One env-resolution path — ``get_database_url()`` — not 29 raw os.environ reads."""

    def _raise() -> str:
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)

    async def handler(ctx: JobContext) -> JobResult:  # pragma: no cover — never reached
        return JobResult.ok()

    assert run_job("demo", handler, argv=[]) == EXIT_CONFIG
    assert "DATABASE_URL" in capsys.readouterr().err


def test_a_db_free_job_never_touches_the_database(monkeypatch):
    """Write-free probes opt out of the session entirely."""

    def _raise() -> str:  # pragma: no cover — must not be called
        raise AssertionError("get_database_url() called for a db-free job")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    seen: dict = {}

    async def handler(ctx: JobContext) -> JobResult:
        seen["session"] = ctx.session
        return JobResult.ok()

    assert run_job("demo", handler, argv=[], needs_db=False) == EXIT_OK
    assert seen == {"session": None}


def test_require_session_raises_when_the_job_declared_no_database(fake_db):
    ctx = JobContext(
        name="demo", args=argparse.Namespace(), session=None, session_factory=None, dry_run=False
    )
    with pytest.raises(RuntimeError, match="needs_db"):
        ctx.require_session()


# --- engine lifecycle --------------------------------------------------------


def test_the_engine_is_disposed_after_the_last_ledger_write(fake_db, monkeypatch):
    """The harness owns engine disposal, so no CLI has to remember its finally block —
    and it must happen *after* the closing ledger write, which uses its own session off
    the same shared engine. Disposing inside the job's session scope would leave that
    write to build a second engine nothing tears down."""
    events: list[str] = []

    async def _dispose() -> None:
        events.append("dispose")

    async def _close(_session, _run_id, **_kwargs):
        events.append("ledger_close")

    monkeypatch.setattr(job_module, "dispose_engine", _dispose)
    monkeypatch.setattr(job_module, "close_run", _close)

    async def handler(ctx: JobContext) -> JobResult:
        events.append("handler")
        return JobResult.ok()

    run_job("demo", handler, argv=[], ledger=True)

    assert events == ["handler", "ledger_close", "dispose"]


# --- ledger integration ------------------------------------------------------


def test_ledger_records_the_run(fake_db, ledger_calls):
    """Every run opens an in-flight row and closes it with the terminal outcome (#178)."""

    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.degraded({"skipped": 4})

    run_job("demo-job", handler, argv=[], ledger=True)

    kinds = [c[0] for c in ledger_calls]
    assert kinds == ["open", "close"]
    assert ledger_calls[0][1]["job_slug"] == "demo-job"
    assert ledger_calls[1][1]["outcome"] == OUTCOME_DEGRADED
    assert ledger_calls[1][1]["counters"] == {"skipped": 4}


def test_a_broken_ledger_never_fails_the_job(fake_db, monkeypatch):
    """The ledger is observability; it must not become a new failure mode."""

    @asynccontextmanager
    async def _ledger_session():
        raise RuntimeError("ledger DB down")
        yield  # pragma: no cover

    monkeypatch.setattr(job_module, "_ledger_session", _ledger_session)

    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok()

    assert run_job("demo", handler, argv=[], ledger=True) == EXIT_OK


def test_ledger_can_be_disabled(fake_db, ledger_calls):
    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok()

    run_job("demo", handler, argv=[], ledger=False)
    assert ledger_calls == []
