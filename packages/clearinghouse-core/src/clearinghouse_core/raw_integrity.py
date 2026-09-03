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
import fcntl
import json
import secrets
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


def _load_state(state_path: Path) -> dict[str, list | None]:
    """The per-scope cursor map. A ``--source`` run and the unscoped run walk
    different keyspaces, so each scope owns its own coverage cursor (#302 CR);
    the pre-scoping shape (``{"cursor": [...]}``) reads as the unscoped scope."""
    if not state_path.is_file():
        return {}
    loaded = json.loads(state_path.read_text())
    if "cursors" in loaded:
        return dict(loaded["cursors"])
    return {"": loaded["cursor"]} if loaded.get("cursor") else {}


def _load_cursor(state_path: Path, scope: str) -> tuple[str, str] | None:
    cursor = _load_state(state_path).get(scope)
    return (cursor[0], cursor[1]) if cursor else None


def _store_cursor(state_path: Path, scope: str, cursor: tuple[str, str] | None) -> None:
    """Read-modify-write of the shared multi-scope state, under a lock and via
    tmp+replace (CR 43): a concurrent timer + ad-hoc ``--source`` sweep must
    not drop each other's cursor, and a crash mid-write must not leave
    truncated JSON that wedges every later load. A cleared cursor is pruned,
    not kept as an accumulating null."""
    with open(state_path.parent / (state_path.name + ".lock"), "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            state = _load_state(state_path)
            if cursor:
                state[scope] = list(cursor)
            else:
                state.pop(scope, None)
            tmp = state_path.with_name(f"{state_path.name}.{secrets.token_hex(4)}.tmp")
            tmp.write_text(json.dumps({"cursors": state}) + "\n")
            tmp.replace(state_path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


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
    scope = ctx.args.source or ""
    budget = ctx.args.byte_budget or None
    cursor = _load_cursor(state_path, scope) if budget else None

    result = verify_store(root, ctx.args.source, byte_budget=budget, after=cursor)
    if cursor is not None and result.objects_verified == 0 and not result.exhausted_budget:
        # cursor sat at the tail — wrap and start a fresh coverage cycle.
        # Merge, never replace: a missing-only tail is exactly the state that
        # lands here (missing objects verify nothing), and the wrap pass may
        # exhaust its budget before re-reaching it (#302 CR).
        tail = result
        result = verify_store(root, ctx.args.source, byte_budget=budget, after=None)
        result.missing.extend(sha for sha in tail.missing if sha not in result.missing)
        result.mismatched.extend(sha for sha in tail.mismatched if sha not in result.mismatched)

    next_cursor = result.last_key if result.exhausted_budget else None
    if not ctx.dry_run:
        _store_cursor(state_path, scope, next_cursor)

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
