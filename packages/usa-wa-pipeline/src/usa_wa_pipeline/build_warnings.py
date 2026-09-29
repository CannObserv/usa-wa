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

Exit ``0`` every node passed · ``1`` a node warned (or any other non-clean status) ·
``4`` degraded — no results file, no nodes in it, or a run that was not a ``build``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "dbt-build-warnings"

#: The nightly's target path (``pipeline-nightly.sh`` passes it to ``dbt build``).
DEFAULT_RUN_RESULTS = "data/target/run_results.json"

#: Statuses of a node that finished clean: ``success`` for a model, ``pass`` for a test.
CLEAN_STATUSES = frozenset({"pass", "success"})


def summarize(run_results: dict[str, Any]) -> JobResult:
    """Gate one ``dbt build``'s ``run_results.json``."""
    which = run_results.get("args", {}).get("which")
    results = run_results.get("results", [])
    if which != "build" or not results:
        # Zero nodes, or a `dbt test` / `dbt run` left in the target dir, reads the
        # build only partly: never report that as zero warnings.
        return JobResult.degraded({"nodes": len(results), "which": which})
    not_clean = sorted(r["unique_id"] for r in results if r["status"] not in CLEAN_STATUSES)
    counters = {
        "nodes": len(results),
        "warned": sum(1 for r in results if r["status"] == "warn"),
        "not_clean": not_clean,
        "generated_at": run_results.get("metadata", {}).get("generated_at"),
    }
    if not_clean:
        logger.error("dbt_build_not_clean", extra=counters)
        return JobResult.failed(counters, exit_code=1)
    return JobResult.ok(counters)


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run-results",
        default=DEFAULT_RUN_RESULTS,
        help=f"dbt run_results.json of the build to gate (default {DEFAULT_RUN_RESULTS}).",
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
    return summarize(run_results)


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
