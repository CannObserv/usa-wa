"""Raw-store integrity sweep (#304): re-hash file objects against their names.

    python -m clearinghouse_core.raw_integrity [--root PATH] [--source SLUG]
                                               [--byte-budget BYTES]

The file-store successor to :mod:`clearinghouse_core.integrity` (#54/#55), for
the #302 raw tier: every manifest-referenced object is re-hashed against the
sha256 it is stored under — the name *is* the baseline, as
``FetchEvent.content_hash`` was. A mismatch or a missing object is
corruption/tamper: ``failed`` (exit 1), never ``degraded``. The default run is
a rolling byte-slice (``--byte-budget``, default 256 MiB) resuming from a
cursor persisted at ``<root>/.raw_integrity_state.json`` and wrapping at the
tail, so per-run cost stays flat as the archive grows; ``--dry-run`` sweeps
without persisting the cursor (the alert semantics stay: the cursor advances
past a mismatch, so one corruption emails once per coverage cycle). Runs on
the #179 job harness for the ledger row; the database session goes unused.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import get_raw_root, verify_store

logger = get_logger(__name__)

#: Stable ledger identity (#178) — distinct from the DB sweep's ``integrity-sweep``;
#: both run while the transition keeps both stores live (#302 step 10 retires the DB one).
JOB_SLUG = "raw-integrity-sweep"

DEFAULT_BYTE_BUDGET = 256 * 1024 * 1024
STATE_FILENAME = ".raw_integrity_state.json"


def _load_cursor(state_path: Path) -> tuple[str, str] | None:
    if not state_path.is_file():
        return None
    cursor = json.loads(state_path.read_text()).get("cursor")
    return (cursor[0], cursor[1]) if cursor else None


def _store_cursor(state_path: Path, cursor: tuple[str, str] | None) -> None:
    state_path.write_text(json.dumps({"cursor": list(cursor) if cursor else None}) + "\n")


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        default=None,
        help="Raw store root (default: USA_WA_RAW_ROOT, else ./raw).",
    )
    parser.add_argument("--source", default=None, help="Verify one source slug only.")
    parser.add_argument(
        "--byte-budget",
        type=int,
        default=DEFAULT_BYTE_BUDGET,
        help="Bytes to verify this run (rolling slice, #55 idiom). 0 = unbounded.",
    )


async def _sweep_job(ctx: JobContext) -> JobResult:
    """Harness handler. Write-free on the store except the cursor file."""
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    if not root.is_dir():
        logger.info("raw_integrity_empty_root", extra={"root": str(root)})
        return JobResult.ok({"objects_verified": 0, "empty_root": True})
    state_path = root / STATE_FILENAME
    budget = ctx.args.byte_budget or None
    cursor = _load_cursor(state_path) if budget else None

    result = verify_store(root, ctx.args.source, byte_budget=budget, after=cursor)
    if cursor is not None and result.objects_verified == 0 and not result.exhausted_budget:
        # cursor sat at the tail — wrap and start a fresh coverage cycle
        result = verify_store(root, ctx.args.source, byte_budget=budget, after=None)

    next_cursor = result.last_key if result.exhausted_budget else None
    if not ctx.dry_run:
        _store_cursor(state_path, next_cursor)

    counters = {
        "objects_verified": result.objects_verified,
        "bytes_verified": result.bytes_verified,
        "mismatched": len(result.mismatched),
        "missing": len(result.missing),
        "exhausted_budget": result.exhausted_budget,
    }
    if not result.clean:
        logger.error(
            "raw_integrity_mismatch",
            extra={
                "mismatched": result.mismatched[:20],
                "missing": result.missing[:20],
                **counters,
            },
        )
        return JobResult.failed(counters, exit_code=1)
    logger.info("raw_integrity_clean", extra=counters)
    return JobResult.ok(counters)


def main(argv: list[str] | None = None) -> int:
    """Verify the raw store. Exit ``0`` clean · ``1`` mismatch/missing · ``2`` config."""
    return run_job(
        JOB_SLUG,
        _sweep_job,
        argv=argv,
        prog="python -m clearinghouse_core.raw_integrity",
        description="Re-hash raw-store objects against their manifest baselines (#304).",
        extra_args=_add_args,
        commit=True,
        dry_run_help="sweep without persisting the coverage cursor",
    )


if __name__ == "__main__":
    raise SystemExit(main())
