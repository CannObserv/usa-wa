"""The nightly backup to GCS (#434): the database dump and the raw store, create-only.

Run by ``deploy/usa-wa-backup.service``, after the 08:00 pipeline, as its own
dynamic user with no capabilities and no database credential; docs/RECOVERY.md
explains the sandbox and the runbook. Built on CannObserv/watcher's
``src/ops/backup.py``.

One run, two independent halves, after a bucket preflight that fails before
anything costs a ``pg_dump`` of production:

1. **The database** (:mod:`usa_wa_api.backup.dump`) — dumped, verified, shipped as
   ``db/<host>/<YYYYMMDDTHHMMSSZ>.dump`` with its schema version and the registry's
   row counts as metadata.
2. **The raw store** (:mod:`usa_wa_api.backup.raw_mirror`) — whatever the bucket
   does not list yet, under ``raw/``.

A failure in one does not cost the night the other; either one fails the run
(exit 1, the ``OnFailure=`` email). A missing bucket name or a misplaced key is a
configuration error (exit 2) that ships nothing. ``--dry-run`` dumps, verifies and
hashes everything but uploads nothing — the rehearsal for a new host or a changed
sandbox.

    python -m usa_wa_api.backup.run [--database usa_wa] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.cloud import storage

from clearinghouse_core.job import EXIT_CONFIG, JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import get_raw_root
from usa_wa_api.backup.dump import Runner, dump_key, dump_metadata, take_dump
from usa_wa_api.backup.gcs import (
    BUCKET_ENV,
    PREFIX_ENV,
    BackupError,
    create_object,
    iso,
    misplaced_key,
    preflight,
)
from usa_wa_api.backup.raw_mirror import mirror

logger = get_logger(__name__)

#: Stable ledger identity (#178). Named in the summary line; no ledger row is written —
#: the job holds no database credential that could write one.
JOB_SLUG = "backup"
DEFAULT_DATABASE = "usa_wa"
#: How many unshippable raw objects a failure names before it counts the rest.
_NAMED = 3


#: How the job runs pg_dump, pg_restore and psql. A seam for the tests, read at call
#: time — never ``subprocess.run`` itself, which the harness's own git call shares.
run_command: Runner = subprocess.run


def make_client() -> Any:
    """The GCS client, from ``GOOGLE_APPLICATION_CREDENTIALS``. A seam for the tests."""
    return storage.Client()


def _describe(exc: BaseException) -> str:
    """One line for any failure: a revoked key surfaces from google.auth as a
    ``RefreshError``, a transport fault as a ``TransportError`` — neither a
    ``BackupError``, and the email must still name it."""
    return str(exc) if isinstance(exc, BackupError) else f"{type(exc).__name__}: {exc}"


def _ship_dump(
    *,
    client: Any,
    bucket: str,
    prefix: str,
    database: str,
    workdir: Path,
    runner: Runner,
    host: str,
    now: Callable[[], datetime],
    dry_run: bool,
) -> dict[str, Any]:
    dump = take_dump(database, workdir, runner=runner, now=now)
    key = dump_key(prefix, dump.dumped_at)
    outcome = (
        "dry-run"
        if dry_run
        else create_object(
            client,
            bucket,
            key,
            dump.path,
            sha256=dump.sha256,
            metadata=dump_metadata(dump, host=host),
        )
    )
    return {
        "outcome": outcome,
        "object": f"gs://{bucket}/{key}",
        "dumped_at": iso(dump.dumped_at),
        "size_bytes": dump.size_bytes,
        "sha256": dump.sha256,
        "alembic_head": dump.alembic_head,
        "registry_rows": dump.registry_rows,
    }


def _mirror_raw(
    *, client: Any, bucket: str, raw_root: Path, host: str, dry_run: bool, failures: list[str]
) -> dict[str, Any]:
    if not raw_root.is_dir():
        failures.append(f"raw: no raw store at {raw_root}")
        return {}
    result = mirror(client, bucket, raw_root, host=host, dry_run=dry_run)
    if not result.local:
        failures.append(f"raw: the store at {raw_root} holds nothing")
    if result.mismatched:
        named = ", ".join(result.mismatched[:_NAMED])
        more = (
            f" and {len(result.mismatched) - _NAMED} more"
            if len(result.mismatched) > _NAMED
            else ""
        )
        failures.append(
            f"raw: {len(result.mismatched)} object(s) no longer hash to their names, "
            f"not shipped: {named}{more} — run the integrity sweep"
        )
    return {**asdict(result), "mismatched": len(result.mismatched)}


def run_backup(
    *,
    client: Any,
    bucket: str,
    prefix: str,
    database: str,
    raw_root: Path,
    workdir: Path,
    runner: Runner,
    host: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    dry_run: bool = False,
) -> dict[str, Any]:
    """One run. Raises :class:`BackupError` only when the bucket preflight fails;
    otherwise returns counters whose ``failures`` list says what did not ship."""
    preflight(client, bucket)
    failures: list[str] = []
    counters: dict[str, Any] = {"bucket": bucket, "source_host": host, "dry_run": dry_run}
    try:
        counters["db"] = _ship_dump(
            client=client,
            bucket=bucket,
            prefix=prefix,
            database=database,
            workdir=workdir,
            runner=runner,
            host=host,
            now=now,
            dry_run=dry_run,
        )
    except Exception as exc:
        failures.append(f"db: {_describe(exc)}")
    try:
        counters["raw"] = _mirror_raw(
            client=client,
            bucket=bucket,
            raw_root=raw_root,
            host=host,
            dry_run=dry_run,
            failures=failures,
        )
    except Exception as exc:
        failures.append(f"raw: {_describe(exc)}")
    counters["failures"] = failures
    return counters


async def _handler(ctx: JobContext) -> JobResult:
    environ = os.environ
    bucket = environ.get(BUCKET_ENV, "").strip()
    if not bucket:
        # No default bucket: guessing one is how bytes land where nobody reads.
        logger.error("backup_config_error", extra={"error": f"{BUCKET_ENV} not set"})
        return JobResult.failed({"error": f"{BUCKET_ENV} not set"}, exit_code=EXIT_CONFIG)
    if misplaced := misplaced_key(environ):
        logger.error("backup_config_error", extra={"error": misplaced})
        return JobResult.failed({"error": misplaced}, exit_code=EXIT_CONFIG)
    host = socket.gethostname()
    prefix = environ.get(PREFIX_ENV, "").strip() or host
    try:
        # Built first, so a missing or unreadable key fails before the dump.
        client = make_client()
    except Exception as exc:  # google.auth raises its own hierarchy
        logger.error("backup_failed", extra={"error": _describe(exc)})
        return JobResult.failed({"error": _describe(exc)})
    with tempfile.TemporaryDirectory(prefix="usa-wa-backup-") as work:
        try:
            counters = run_backup(
                client=client,
                bucket=bucket,
                prefix=prefix,
                database=ctx.args.database,
                raw_root=get_raw_root(),
                workdir=Path(work),
                runner=run_command,
                host=host,
                dry_run=ctx.dry_run,
            )
        except Exception as exc:
            logger.error("backup_failed", extra={"error": _describe(exc), "bucket": bucket})
            return JobResult.failed({"error": _describe(exc), "bucket": bucket})
    for failure in counters["failures"]:
        logger.error("backup_failed", extra={"error": failure, "bucket": bucket})
    if counters["failures"]:
        return JobResult.failed(counters)
    logger.info("backup_shipped", extra={"db": counters["db"], "raw": counters["raw"]})
    return JobResult.ok(counters)


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help=f"the database to dump, by name or DSN (default {DEFAULT_DATABASE})",
    )


def main(argv: list[str] | None = None) -> int:
    """Timer entrypoint. Exit 0 only when the dump and every raw file are in the bucket."""
    return run_job(
        JOB_SLUG,
        _handler,
        argv=argv,
        description="Nightly backup of the database and the raw store to GCS (#434)",
        prog="python -m usa_wa_api.backup.run",
        extra_args=_add_args,
        needs_db=False,
        dry_run_help="Dump and verify the database and hash the raw store, but upload nothing.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
