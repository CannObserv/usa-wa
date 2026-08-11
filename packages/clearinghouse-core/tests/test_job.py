"""Tests for the shared job harness (#179).

The harness is the single place a CLI's run scaffold lives: env resolution, engine
lifecycle, base args, summary emission, exit-code mapping, and the #178 ledger write.
These tests pin the contract ~47 entry points will be migrated onto, so they exercise
the three handler shapes the repo actually has (a sweep that skips-and-continues, a
refresh that returns a summary dataclass, a reconciler that owns its own exit codes).
"""

import argparse
import asyncio
import json
import locale
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import pytest
from ulid import ULID as _ULID

from clearinghouse_core import job as job_module
from clearinghouse_core.config import DATABASE_ROLE_APP, DATABASE_ROLE_OWNER
from clearinghouse_core.job import (
    EXIT_CONFIG,
    EXIT_DEGRADED,
    EXIT_FAILED,
    EXIT_OK,
    JobContext,
    JobResult,
    load_json_batch,
    run_job,
)
from clearinghouse_core.runs import OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK
from clearinghouse_core.testing import patch_job_runtime


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


class _EngineStub:
    """``create_async_engine`` stand-in for the owner-role path, which builds a
    per-run engine instead of borrowing the process-shared one."""

    def __init__(self, url: str, **_kwargs) -> None:
        self.url = url
        self.disposed = 0

    async def dispose(self) -> None:
        self.disposed += 1


class _SessionmakerStub:
    """``async_sessionmaker`` stand-in: calling it yields a recording session."""

    def __init__(self) -> None:
        self.session = _FakeSession()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc) -> bool:
        return False


@pytest.fixture
def fake_db(monkeypatch):
    """Replace the harness's database seam with an in-memory session **and factory**.

    The factory is not decoration: the self-session jobs reach it through
    ``ctx.require_session_factory()`` (CR #196 finding 49), so a fixture that yielded
    ``None`` there would make the seam untestable through the harness.
    """
    session = _FakeSession()
    factory = _SessionmakerStub()
    factory.session = session
    session.factory = factory

    @asynccontextmanager
    async def _fake_database():
        yield (factory, session)

    monkeypatch.setattr(job_module, "_database", _fake_database)
    monkeypatch.setattr(
        job_module, "get_database_url", lambda *_a, **_k: "postgresql+asyncpg://x/y"
    )
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

    def _raise(_role: str = DATABASE_ROLE_APP) -> str:
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)

    async def handler(ctx: JobContext) -> JobResult:  # pragma: no cover — never reached
        return JobResult.ok()

    assert run_job("demo", handler, argv=[]) == EXIT_CONFIG
    assert "DATABASE_URL" in capsys.readouterr().err


def test_a_db_free_job_never_touches_the_database(monkeypatch):
    """Write-free probes opt out of the session entirely."""

    def _raise(_role: str = DATABASE_ROLE_APP) -> str:  # pragma: no cover — must not be called
        raise AssertionError("get_database_url() called for a db-free job")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    seen: dict = {}

    async def handler(ctx: JobContext) -> JobResult:
        seen["session"] = ctx.session
        return JobResult.ok()

    assert run_job("demo", handler, argv=[], needs_db=False) == EXIT_OK
    assert seen == {"session": None}


# --- the owner role (#179b) ---------------------------------------------------


def test_owner_role_resolves_the_owner_dsn(monkeypatch):
    """Five migrations hard-delete citations the app role is REVOKEd on (#54), so they
    run under ``DATABASE_URL_OWNER``. Before #179b each read it off ``os.environ``
    itself — the same split brain #179 closed for ``DATABASE_URL``."""
    asked: list[str] = []

    def _url(role: str = DATABASE_ROLE_APP) -> str:
        asked.append(role)
        return "postgresql+asyncpg://owner@h/db"

    monkeypatch.setattr(job_module, "get_database_url", _url)
    built: list[str] = []

    def _build(url: str):
        built.append(url)
        return _EngineStub(url), _SessionmakerStub()

    monkeypatch.setattr(job_module, "_build_role_engine", _build)

    seen: dict = {}

    async def handler(ctx: JobContext) -> JobResult:
        seen["session"] = ctx.session
        return JobResult.ok()

    assert run_job("demo", handler, argv=[], role=DATABASE_ROLE_OWNER) == EXIT_OK
    # Once for the pre-flight config check, once to build the engine — never for "app".
    assert set(asked) == {DATABASE_ROLE_OWNER}
    assert built == ["postgresql+asyncpg://owner@h/db"]
    assert seen["session"] is not None


def test_missing_owner_url_is_a_config_exit(monkeypatch, capsys):
    """Exit 2 — the code every one of the five owner CLIs already returned by hand."""

    def _raise(_role: str = DATABASE_ROLE_APP) -> str:
        raise RuntimeError("DATABASE_URL_OWNER is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)

    async def handler(ctx: JobContext) -> JobResult:  # pragma: no cover — never reached
        return JobResult.ok()

    assert run_job("demo", handler, argv=[], role=DATABASE_ROLE_OWNER) == EXIT_CONFIG
    assert "DATABASE_URL_OWNER" in capsys.readouterr().err


def test_owner_role_disposes_its_own_engine(monkeypatch):
    """The owner engine is per-run and not the process-shared one, so the harness — not
    the CLI — still owns its ``finally``."""

    def _url(role: str = DATABASE_ROLE_APP) -> str:
        return "postgresql+asyncpg://owner@h/db"

    monkeypatch.setattr(job_module, "get_database_url", _url)
    engines: list = []

    def _build(url: str):
        made = _EngineStub(url)
        engines.append(made)
        return made, _SessionmakerStub()

    monkeypatch.setattr(job_module, "_build_role_engine", _build)

    async def handler(ctx: JobContext) -> JobResult:
        return JobResult.ok()

    run_job("demo", handler, argv=[], role=DATABASE_ROLE_OWNER, ledger=True)

    assert [e.disposed for e in engines] == [1]


def test_owner_role_routes_every_session_through_the_owner_engine(monkeypatch, ledger_calls):
    """Including the ledger's. An owner job must not need ``DATABASE_URL`` as well —
    otherwise the #178 row for the riskiest jobs in the repo is the one most likely to
    go missing, and the owner role can always write ``job_runs`` anyway."""
    owner_factory = _SessionmakerStub()

    def _url(role: str = DATABASE_ROLE_APP) -> str:
        if role == DATABASE_ROLE_APP:  # pragma: no cover — must not be reached
            raise AssertionError("owner job resolved the app DSN")
        return "postgresql+asyncpg://owner@h/db"

    def _no_shared_factory():  # pragma: no cover — must not be reached
        raise AssertionError("owner job fell back to the process-shared app engine")

    monkeypatch.setattr(job_module, "get_database_url", _url)
    monkeypatch.setattr(
        job_module, "_build_role_engine", lambda url: (_EngineStub(url), owner_factory)
    )
    monkeypatch.setattr(job_module, "get_session_factory", _no_shared_factory)

    seen: list = []

    async def handler(ctx: JobContext) -> JobResult:
        seen.append(job_module._session_factory())
        return JobResult.ok({"retired": 2})

    run_job("owner-demo", handler, argv=[], role=DATABASE_ROLE_OWNER, ledger=True)

    assert seen == [owner_factory]
    assert [c[0] for c in ledger_calls] == ["open", "close"]
    assert ledger_calls[0][1]["job_slug"] == "owner-demo"


def test_the_app_role_still_uses_the_process_shared_engine(fake_db, monkeypatch):
    """The owner path is additive: nothing changes for the 39 app-role jobs."""
    shared = object()
    monkeypatch.setattr(job_module, "get_session_factory", lambda: shared)

    seen: list = []

    async def handler(ctx: JobContext) -> JobResult:
        seen.append(job_module._session_factory())
        return JobResult.ok()

    assert run_job("demo", handler, argv=[]) == EXIT_OK
    assert seen == [shared]


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


# --- CR #191 regression pins ------------------------------------------------


@dataclass
class _SummaryShape:
    """A summary dataclass of the shape existing CLIs already return."""

    scanned: int = 3
    skipped: int = 1


def test_directly_constructed_job_result_normalizes_its_counters():
    """``JobResult(...)`` normalizes like its classmethods do (CR #191 finding 2).

    The classmethods always normalized; direct construction — which the public
    dataclass signature invites — did not. ``_render_human`` then called ``.items()``
    on a dataclass from ``_emit``, which runs *after* ``asyncio.run`` and so sits
    outside every ``try`` in ``_execute``: an AttributeError traceback out of the one
    module that promises never to raise for a handler failure.
    """
    result = JobResult(OUTCOME_DEGRADED, _SummaryShape())  # type: ignore[arg-type]
    assert result.counters == {"scanned": 3, "skipped": 1}


def test_directly_constructed_job_result_survives_rendering(capsys):
    """The end-to-end path finding 2 actually crashed on: emit, not construction."""

    async def handler(ctx: JobContext) -> JobResult:
        return JobResult(OUTCOME_OK, _SummaryShape())  # type: ignore[arg-type]

    assert run_job("render-pin", handler, argv=[], needs_db=False) == EXIT_OK
    assert "scanned=3" in capsys.readouterr().out


def test_ledger_defaults_off_when_the_job_declares_no_database(monkeypatch, caplog):
    """``ledger`` follows ``needs_db`` (CR #191 finding 3).

    A ``needs_db=False`` probe skips the DSN check, so an unconditional ledger default
    made every run emit ``job_ledger_open_failed`` *and* ``job_ledger_close_failed`` —
    two meaningless warnings per run, devaluing the signal that matters when the ledger
    genuinely breaks.
    """
    opened = False

    async def _spy(*_args, **_kwargs):
        nonlocal opened
        opened = True
        return None

    monkeypatch.setattr(job_module, "_open_ledger_row", _spy)

    async def handler(ctx: JobContext) -> dict:
        return {"probed": 1}

    assert run_job("probe-pin", handler, argv=[], needs_db=False) == EXIT_OK
    assert opened is False, "a needs_db=False job must not attempt a ledger write"
    assert "job_ledger_open_failed" not in caplog.text
    assert "job_ledger_close_failed" not in caplog.text


def test_ledger_can_still_be_forced_on_for_a_dbless_job(monkeypatch):
    """``ledger=True`` remains available explicitly — the default changed, not the knob."""
    opened = False

    async def _spy(*_args, **_kwargs):
        nonlocal opened
        opened = True
        return None

    monkeypatch.setattr(job_module, "_open_ledger_row", _spy)

    async def handler(ctx: JobContext) -> dict:
        return {}

    assert run_job("probe-forced", handler, argv=[], needs_db=False, ledger=True) == EXIT_OK
    assert opened is True


def test_git_sha_is_resolved_before_the_event_loop(monkeypatch):
    """The blocking ``git rev-parse`` runs on the calling thread (CR #191 finding 4).

    ``subprocess.run`` inside a coroutine stalls the loop. ``run_job`` primes the
    memoized value first, so by the time ``_open_ledger_row`` asks, the cache is warm
    and no subprocess runs under ``asyncio.run``.

    Two details make this test actually able to fail (CR #191 round 2, finding 9 — the
    first version could not, and passed with the priming line deleted):

    - ``needs_db=True`` so the **ledger path runs**. Under ``needs_db=False`` the ledger
      now defaults off (finding 3), leaving the priming call the only caller of
      ``_git_sha`` — so nothing could distinguish primed from unprimed.
    - The probe records whether a *running loop* exists at call time, rather than
      counting calls around the handler. ``_open_ledger_row`` evaluates ``_git_sha()``
      as an argument to ``open_run``, so it is reached even with the ledger writers
      stubbed out.
    """
    job_module._git_sha.cache_clear()
    patch_job_runtime(monkeypatch)

    called_with_running_loop: list[bool] = []
    real_run = job_module.subprocess.run

    def _tracking_run(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            called_with_running_loop.append(False)
        else:
            called_with_running_loop.append(True)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(job_module.subprocess, "run", _tracking_run)

    async def handler(ctx: JobContext) -> dict:
        return {}

    assert run_job("sha-pin", handler, argv=[], needs_db=True) == EXIT_OK
    assert called_with_running_loop, "expected git rev-parse to be attempted at least once"
    assert not any(called_with_running_loop), (
        "git rev-parse ran inside the event loop — the priming call in run_job is gone, "
        "so a blocking subprocess is stalling the loop again"
    )
    job_module._git_sha.cache_clear()


# --- A job that cannot roll back must not advertise --dry-run (CR #196 finding 47) ---


def test_a_job_that_cannot_roll_back_declines_the_dry_run_flag(fake_db):
    """``dry_run=False`` removes the flag rather than accepting it and ignoring it.

    The harness adds ``--dry-run`` to every job with the help text "Run the work but roll
    back instead of committing". A ``commit=False`` job whose handler owns an
    unconditional transaction — the WSL refresh, the PM subscription bootstrap — cannot
    honour that, and silently committing behind the flag is worse than not offering it:
    the summary line even printed ``dry_run=true`` on the run that wrote.
    """

    async def handler(ctx: JobContext) -> dict:
        return {}

    with pytest.raises(SystemExit) as excinfo:
        run_job("no-rollback", handler, argv=["--dry-run"], dry_run=False)
    # argparse's own "unrecognized arguments" exit, which is also the harness's EXIT_CONFIG.
    assert excinfo.value.code == EXIT_CONFIG


def test_declining_the_flag_still_hands_the_handler_a_false_dry_run(fake_db, capsys):
    """The attribute has to exist regardless — ``_execute`` and ``_emit`` both read it."""
    seen: list[bool] = []

    async def handler(ctx: JobContext) -> dict:
        seen.append(ctx.dry_run)
        return {}

    assert run_job("no-rollback", handler, argv=[], dry_run=False) == EXIT_OK
    assert seen == [False]
    assert "dry_run=true" not in capsys.readouterr().out


def test_the_flag_is_offered_by_default(fake_db):
    """The suppression is opt-in: every other job keeps the base flag."""

    async def handler(ctx: JobContext) -> dict:
        return {}

    assert run_job("rollback-capable", handler, argv=["--dry-run"]) == EXIT_OK


# --- The role factory is per-run, not process-wide (CR #196 finding 50) ---


def test_concurrent_owner_runs_do_not_share_a_role_factory(monkeypatch):
    """Two owner-role runs in one process must each see their own session factory.

    As a module global, the first task to *finish* blanked the factory in its ``finally``
    while the second was still inside its lifetime — so the survivor silently fell back to
    the process-shared **app** engine. One job per process makes that unreachable today,
    but ``job.py`` is the repo's reusable harness and nothing guards the invariant.
    """
    monkeypatch.setattr(job_module, "get_database_url", lambda role="app": f"postgres://{role}")

    class _FakeEngine:
        async def dispose(self) -> None:
            return None

    tags = iter(["factory-a", "factory-b"])
    monkeypatch.setattr(job_module, "_build_role_engine", lambda url: (_FakeEngine(), next(tags)))

    async def _scenario() -> dict[str, object]:
        observed: dict[str, object] = {}

        async def _one(name: str, hold: float) -> None:
            async with job_module._engine_lifetime("owner"):
                await asyncio.sleep(hold)
                observed[name] = job_module._session_factory()

        # ``a`` is still inside its lifetime when ``b`` opens and closes its own.
        await asyncio.gather(_one("a", 0.02), _one("b", 0.0))
        return observed

    assert asyncio.run(_scenario()) == {"a": "factory-a", "b": "factory-b"}


# --- Jobs that open their own sessions stay on this run's engine (CR #196 finding 49) ---


def test_require_session_factory_hands_back_the_harness_factory(fake_db):
    """The seam the self-session jobs use instead of ``get_session_factory()``."""
    seen: list[object] = []

    async def handler(ctx: JobContext) -> dict:
        seen.append(ctx.require_session_factory())
        return {}

    assert run_job("own-session", handler, argv=[]) == EXIT_OK
    assert seen == [fake_db.factory]


def test_require_session_factory_raises_for_a_db_free_job(monkeypatch):
    """Symmetric with ``require_session``: a probe has no factory to hand out."""
    raised: list[str] = []

    async def handler(ctx: JobContext) -> dict:
        try:
            ctx.require_session_factory()
        except RuntimeError as exc:
            raised.append(str(exc))
        return {}

    assert run_job("probe", handler, argv=[], needs_db=False) == EXIT_OK
    assert "needs_db=False" in raised[0]


def test_a_narrower_dry_run_can_state_its_own_meaning(fake_db, capsys):
    """``dry_run_help`` for the job whose flag is real but not a rollback.

    ``meetings/harvest.py`` declared "harvest but do not write the seed" itself until
    #179b took its parser away and left it advertising the generic rollback string, which
    is false there — its archive writes commit either way (CR #196 finding 56).
    """

    async def handler(ctx: JobContext) -> dict:
        return {}

    with pytest.raises(SystemExit):
        run_job(
            "narrow",
            handler,
            argv=["--help"],
            dry_run_help="harvest but do not write the seed",
        )
    out = capsys.readouterr().out
    assert "harvest but do not write the seed" in out
    assert "roll back instead of committing" not in out


# --- load_json_batch (#196 CR items 5 + 7) ----------------------------------


async def test_load_json_batch_reads_off_the_event_loop(tmp_path):
    """The read runs in a worker thread, not on the loop (#196)."""
    path = tmp_path / "batch.json"
    path.write_text('[{"a": 1}]', encoding="utf-8")

    loop_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    def loader(payload):
        return payload

    real_read_text = Path.read_text

    def recording_read_text(self, *args, **kwargs):
        seen.append(threading.current_thread())
        return real_read_text(self, *args, **kwargs)

    with mock.patch.object(Path, "read_text", recording_read_text):
        result = await load_json_batch(str(path), loader)

    assert result == [{"a": 1}]
    assert seen and seen[0] is not loop_thread


async def test_load_json_batch_decodes_utf8_regardless_of_locale(tmp_path):
    """The batch is decoded as UTF-8 explicitly, not via the locale default (#196 CR 7).

    ``open()``/``read_text()`` without an encoding follow the process locale, so a batch
    authored on one box and read on another can mis-decode or raise. The specs these
    files carry include legislator names, which are exactly where a non-ASCII byte lives.
    """
    path = tmp_path / "batch.json"
    path.write_bytes('[{"name": "Muñoz"}]'.encode())

    captured: dict[str, object] = {}

    def loader(payload):
        captured["payload"] = payload
        return payload

    # Force a decoder that would mangle the UTF-8 bytes if it were consulted.
    with mock.patch.object(locale, "getpreferredencoding", lambda *a, **k: "ascii"):
        result = await load_json_batch(str(path), loader)

    assert captured["payload"] == [{"name": "Muñoz"}]
    assert result == [{"name": "Muñoz"}]


async def test_load_json_batch_propagates_the_loaders_error(tmp_path):
    """Parsing and validation stay with the caller, so its error type still surfaces."""
    path = tmp_path / "batch.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")

    class _CallerError(Exception):
        pass

    def loader(payload):
        raise _CallerError("must be a JSON array")

    with pytest.raises(_CallerError):
        await load_json_batch(str(path), loader)
