"""Fail the nightly on any dbt warning (#412 PR E).

    python -m usa_wa_pipeline.build_warnings [--run-results PATH]

A dbt test at ``severity: warn`` never changes ``dbt build``'s exit code, so a warning
reached only the journal. Two warnings carry real news: ``assignments_chamber_vacancy``
(a chamber below 49/98, which ``succession-invariants`` emailed about until PR E
disabled it) and the seat-occupancy ratchet's "ratchet me down". This stage reads the
build's ``run_results.json`` and fails on any node whose status is not ``pass`` or
``success``.

The nightly runs it right after ``dbt build`` and **counts** it rather than aborting:
a warning is news, not a defect, so the registrar and publish still run and the run
exits 1 at the end, which is what emails the operator.

**The rollover window (#452).** From the day a biennium becomes current until WSL publishes
its sponsors, every outgoing span has closed and nothing is open, so
``assignments_chamber_vacancy`` warns every night. ``ACCEPTED_WARNINGS`` names that node and
biennium: while the built duckdb stages no sponsors for it, the warning is counted in
``accepted_warnings`` and logged ``dbt_build_accepted_warning`` rather than failing the
stage. Once sponsors stage, the entry is stale and degrades the stage until it is removed —
the #333 ``ACCEPTED_OUTAGES`` pattern.

Exit ``0`` every node passed (or warned under an acceptance) · ``1`` a node warned (or any
other non-clean status) · ``4`` degraded — no results file, no nodes in it, a run that was
not a ``build``, or a stale acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_pipeline.conformed.spans import current_biennium

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "dbt-build-warnings"

#: The nightly's target path (``pipeline-nightly.sh`` passes it to ``dbt build``).
DEFAULT_RUN_RESULTS = "data/target/run_results.json"

#: The built duckdb the staged sponsors are read from (the registrar's default).
DEFAULT_DB = "data/pipeline.duckdb"

#: Statuses of a node that finished clean: ``success`` for a model, ``pass`` for a test.
CLEAN_STATUSES = frozenset({"pass", "success"})


@dataclass(frozen=True)
class AcceptedWarning:
    """A dbt node whose ``warn`` is KNOWN for one biennium: WSL has not published it.

    Covers a ``warn`` only — never an error — and only while the built duckdb stages no
    sponsors for ``biennium``. The biennium is the time box: dormant before it becomes
    current, stale once its sponsors stage.
    """

    node: str
    biennium: str
    reason: str
    issue: str
    observed: str
    #: Every chore WSL publishing unblocks. The stale signal is the only reminder, so
    #: anything not named here is lost with it (the #333 rule).
    follow_up: tuple[str, ...]


#: The warnings currently accepted. Empty is the normal state; an entry lives for one
#: rollover window and is removed when it goes stale.
ACCEPTED_WARNINGS: tuple[AcceptedWarning, ...] = (
    AcceptedWarning(
        node="test.usa_wa_pipeline.assignments_chamber_vacancy",
        biennium="2027-28",
        reason=(
            "Every 2025-26 span closes at 2026-12-31 and nothing opens until WSL publishes "
            "2027-28 sponsors, so both chambers read 0 open seats every night until then."
        ),
        issue="#452",
        observed="2026-10-01",
        follow_up=(
            "remove this entry from ACCEPTED_WARNINGS in usa_wa_pipeline/build_warnings.py",
            (
                "remove the 2027-28 entry from ACCEPTED_ROSTER_FAULTS in "
                "usa_wa_adapter_legislature/raw_harvest.py"
            ),
            (
                "record the date WSL published 2027-28 in "
                "docs/research/2026-10-01-biennium-rollover-rehearsal.md"
            ),
        ),
    ),
)


def summarize(
    run_results: dict[str, Any],
    *,
    accepted: tuple[AcceptedWarning, ...] = ACCEPTED_WARNINGS,
    biennium: str | None = None,
    staged_sponsors: int | None = None,
) -> JobResult:
    """Gate one ``dbt build``'s ``run_results.json``.

    ``biennium`` is the build's current biennium and ``staged_sponsors`` how many sponsors
    it staged for it (``None`` unknown): together they decide which acceptances apply
    (#452). An unknown count applies none and stales none."""
    which = run_results.get("args", {}).get("which")
    results = run_results.get("results", [])
    if which != "build" or not results:
        # Zero nodes, or a `dbt test` / `dbt run` left in the target dir, reads the
        # build only partly: never report that as zero warnings.
        return JobResult.degraded({"nodes": len(results), "which": which})
    live = [a for a in accepted if a.biennium == biennium and staged_sponsors is not None]
    stale = [a for a in live if staged_sponsors]
    accepting = {a.node: a for a in live if not staged_sponsors}
    warned_accepted = sorted(
        r["unique_id"] for r in results if r["status"] == "warn" and r["unique_id"] in accepting
    )
    not_clean = sorted(
        r["unique_id"]
        for r in results
        if r["status"] not in CLEAN_STATUSES and r["unique_id"] not in warned_accepted
    )
    counters = {
        "nodes": len(results),
        "warned": sum(1 for r in results if r["status"] == "warn"),
        "not_clean": not_clean,
        "accepted_warnings": warned_accepted,
        "stale_acceptances": [a.node for a in stale],
        "generated_at": run_results.get("metadata", {}).get("generated_at"),
    }
    if stale:
        logger.warning(
            "dbt_build_acceptance_stale",
            extra={
                "nodes": counters["stale_acceptances"],
                "issues": [a.issue for a in stale],
                "detail": f"{biennium} sponsors staged — this acceptance is now false",
                "follow_up": [step for a in stale for step in a.follow_up],
            },
        )
    if warned_accepted:
        logger.warning(
            "dbt_build_accepted_warning",
            extra={
                "nodes": warned_accepted,
                "issues": [accepting[node].issue for node in warned_accepted],
                "observed": [accepting[node].observed for node in warned_accepted],
            },
        )
    if not_clean:
        logger.error("dbt_build_not_clean", extra=counters)
        return JobResult.failed(counters, exit_code=1)
    if stale:
        return JobResult.degraded(counters)
    return JobResult.ok(counters)


def staged_sponsors(db_path: Path, biennium: str) -> int | None:
    """How many ``stg_wsl_sponsors`` rows the built duckdb holds for ``biennium``;
    ``None`` when there is no database or no staging table — unknown, never zero."""
    if not db_path.is_file():
        return None
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        [count] = con.execute(
            "select count(*) from stg_wsl_sponsors where biennium = ?", [biennium]
        ).fetchone()
    except duckdb.CatalogException:
        return None
    finally:
        con.close()
    return count


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run-results",
        default=DEFAULT_RUN_RESULTS,
        help=f"dbt run_results.json of the build to gate (default {DEFAULT_RUN_RESULTS}).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help=f"Built pipeline duckdb (default: USA_WA_PIPELINE_DB, else {DEFAULT_DB}).",
    )


def load_run_results(path: Path) -> dict[str, Any] | None:
    """The parsed ``run_results.json``, or ``None`` when the build left none."""
    return json.loads(path.read_text()) if path.is_file() else None


async def _warnings_job(ctx: JobContext) -> JobResult:
    path = Path(ctx.args.run_results)
    run_results = load_run_results(path)
    if run_results is None:
        logger.warning("dbt_run_results_missing", extra={"path": str(path)})
        return JobResult.degraded({"run_results_missing": str(path)})
    biennium = current_biennium()
    staged = None
    if any(a.biennium == biennium for a in ACCEPTED_WARNINGS):
        # Only an acceptance in force reads the database: outside a rollover window the
        # stage stays the run_results reader it always was.
        db = ctx.args.db or os.environ.get("USA_WA_PIPELINE_DB", DEFAULT_DB)
        staged = staged_sponsors(Path(db), biennium)
    return summarize(run_results, biennium=biennium, staged_sponsors=staged)


def main(argv: list[str] | None = None) -> int:
    """Gate the nightly build's run results. Exit ``0`` clean · ``1`` warned · ``4`` unread."""
    return run_job(
        JOB_SLUG,
        _warnings_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.build_warnings",
        description="Fail on any dbt node that did not pass: a warning must reach the operator.",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
