"""Bring a #434 backup back: the database from a dump, or the raw store from its mirror.

Run by hand, as root — it reads the root-only key — and every step refuses rather
than guesses. docs/RECOVERY.md is the runbook. Built on CannObserv/watcher's
``src/ops/restore.py``.

**The database.** *Find* a dump (by key, or the newest name under a host's prefix —
names are timestamps); *fetch* it into a private directory and prove it is what was
shipped (sha256 against the object's metadata, then the backup's own verification,
whose ``COPY`` row counts must equal the ones recorded at dump time); *load* it into
an existing, **empty** database in one transaction, so a failure leaves nothing
half-loaded and ``--into usa_wa`` by mistake loads nothing at all; then *check* it:

- the schema version and every registry table's row count equal the dump's own;
- every key in the newest published ``person_crosswalk`` / ``org_crosswalk``
  resolves to the same ULID in the restored registry. ULIDs are sticky, so a key on
  a different one is a failure; a merge recorded since that publish is not.

**The raw store.** ``--raw-into DIR`` fetches the whole mirror into an empty private
directory, proves every object hashes to its name and every manifest to its
recorded digest, and rebuilds each source's ``latest.json`` from the manifests.

**The source host is always named.** A restore runs on a different host from the one
that shipped — an incident's replacement — so this host's name is the one prefix
never wanted. ``--latest`` takes ``--prefix``; ``--list`` without one shows every
host's dumps.

    python -m usa_wa_api.backup.restore --list
    python -m usa_wa_api.backup.restore --latest --prefix usa-wa --download-only /root/restore
    python -m usa_wa_api.backup.restore --latest --prefix usa-wa --into usa_wa_restore \
        --run-as postgres
    python -m usa_wa_api.backup.restore --raw-into /root/usa-wa-raw
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

from google.api_core.exceptions import NotFound
from google.cloud import storage
from ulid import ULID

from clearinghouse_core.job import EXIT_CONFIG, JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_api.backup.dump import (
    COUNTED_SCHEMA,
    DB_PREFIX,
    KEY_TIME_FORMAT,
    OBJECT_SUFFIX,
    PG_DUMP_TIMEOUT_SECONDS,
    QUERY_TIMEOUT_SECONDS,
    Runner,
    read_alembic_head,
    verify_dump,
)
from usa_wa_api.backup.gcs import (
    BUCKET_ENV,
    LIST_TIMEOUT_SECONDS,
    UPLOAD_TIMEOUT_SECONDS,
    BackupError,
    iso,
    sha256_file,
)
from usa_wa_api.backup.raw_mirror import fetch_mirror, private_dir

logger = get_logger(__name__)

#: Stable ledger identity (#178); no ledger row is written.
JOB_SLUG = "backup-restore"
#: How far a dump's name may run ahead of the bucket's creation time for it: the
#: dumping host's clock is not the bucket's.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=10)
#: The crosswalks the publisher writes off the registry (#309), by registry ``kind``.
CROSSWALK_KINDS = ("person", "org")
DATASETS_ROOT_ENV = "USA_WA_DATASETS_ROOT"
#: How many keys a crosswalk problem names before it stops.
_NAMED = 5
# What --list prints beside each name, in this order.
_LISTED_METADATA = ("dumped_at", "alembic_head", "size_bytes", "source_host", "sha256")
_TABLE_RE = re.compile(rf"^{COUNTED_SCHEMA}\.[a-z_]+$")
#: A publisher version directory: ``v<YYYYMMDDTHHMMSSZ>-<hex>`` (usa_wa_pipeline.publish).
_VERSION_RE = re.compile(r"^v(\d{8}T\d{6}Z)-[0-9a-f]+$")

#: How the restore runs pg_restore and psql. A seam for the tests, read at call time.
run_command: Runner = subprocess.run


def make_client() -> Any:
    """The GCS client, from ``GOOGLE_APPLICATION_CREDENTIALS``. A seam for the tests."""
    return storage.Client()


def as_user(argv: list[str], run_as: str | None) -> list[str]:
    """Prefix ``argv`` to run as ``run_as`` via ``setpriv``; unchanged when ``None``.

    ``setpriv`` is a plain exec — no PAM session — and ``--reset-env`` hands the child
    only its own passwd entry's basics, so nothing of the operator's root shell
    reaches a process any ``postgres``-uid process can read through
    ``/proc/<pid>/environ``.
    """
    if run_as is None:
        return argv
    return [
        "setpriv",
        f"--reuid={run_as}",
        f"--regid={run_as}",
        "--init-groups",
        "--reset-env",
        "--",
        *argv,
    ]


class Snapshot(NamedTuple):
    """One shipped dump: its name, the metadata the backup wrote, and the bucket's own
    record of when the object was created."""

    name: str
    metadata: dict
    created: datetime | None

    @property
    def named_at(self) -> datetime | None:
        """The time the name claims; ``None`` for a name the backup never writes."""
        stamp = Path(self.name).name.removesuffix(OBJECT_SUFFIX)
        try:
            return datetime.strptime(stamp, KEY_TIME_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            return None

    @property
    def suspect(self) -> bool:
        """Whether the name claims a time the bucket's own clock contradicts.

        An honest dump is named when ``pg_dump`` starts and created when the upload
        lands, so its name is never later than its creation, beyond clock skew. A
        later name is a skewed clock or a forgery — one planting ``2099…`` would
        otherwise own ``--latest`` until the lifecycle rule removed it.
        """
        named_at = self.named_at
        if named_at is None or self.created is None:
            return True
        return named_at > self.created + CLOCK_SKEW_TOLERANCE


def _under(prefix: str | None) -> str:
    return f"{DB_PREFIX}/{prefix.strip('/')}/" if prefix else f"{DB_PREFIX}/"


def list_snapshots(client: Any, bucket: str, prefix: str | None) -> list[Snapshot]:
    """Every dump under a host's prefix (every host's, if ``None``), in name order."""
    blobs = client.list_blobs(bucket, prefix=_under(prefix), timeout=LIST_TIMEOUT_SECONDS)
    return sorted(
        (
            Snapshot(blob.name, dict(blob.metadata or {}), blob.time_created)
            for blob in blobs
            if blob.name.endswith(OBJECT_SUFFIX)
        ),
        key=lambda snapshot: snapshot.name,
    )


def latest_key(client: Any, bucket: str, prefix: str) -> str:
    """The newest dump the named host shipped, passing over suspect names."""
    snapshots = list_snapshots(client, bucket, prefix)
    suspect = [snapshot.name for snapshot in snapshots if snapshot.suspect]
    if suspect:
        logger.warning("restore_suspect_names", extra={"names": suspect})
    candidates = [snapshot for snapshot in snapshots if not snapshot.suspect]
    if not candidates:
        passed = f" (passed over {len(suspect)} suspect)" if suspect else ""
        raise BackupError(f"no dumps under gs://{bucket}/{_under(prefix)}{passed}")
    return candidates[-1].name


def _recorded_rows(metadata: Mapping[str, str]) -> dict[str, int]:
    """The registry counts the backup recorded, validated: these become SQL."""
    rows = json.loads(metadata.get("registry_rows") or "{}")
    bad = [table for table in rows if not _TABLE_RE.match(table)]
    if bad:
        raise BackupError(f"recorded registry_rows names tables outside {COUNTED_SCHEMA}: {bad}")
    return {table: int(count) for table, count in rows.items()}


def fetch_dump(
    client: Any, bucket: str, key: str, dest_dir: Path, *, runner: Runner
) -> tuple[Path, dict[str, str]]:
    """Download ``key`` into ``dest_dir`` and prove it is the dump that was shipped.

    Created ``O_EXCL | O_NOFOLLOW`` at 0600 in a private directory, so nothing already
    at the name is written through and no other user can read the result. A failed
    proof removes it.
    """
    blob = client.bucket(bucket).get_blob(key, timeout=LIST_TIMEOUT_SECONDS)
    if blob is None:
        raise BackupError(f"gs://{bucket}/{key} not found")
    metadata = dict(blob.metadata or {})
    expected = metadata.get("sha256")
    if not expected:
        raise BackupError(f"gs://{bucket}/{key} carries no recorded sha256; refusing it")
    private_dir(dest_dir)
    path = dest_dir / Path(key).name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as exc:
        raise BackupError(f"{path} already exists; refusing to write over it") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            try:
                blob.download_to_file(handle, timeout=UPLOAD_TIMEOUT_SECONDS)
            except NotFound as exc:
                raise BackupError(f"gs://{bucket}/{key} not found") from exc
        actual = sha256_file(path)
        if actual != expected:
            raise BackupError(f"{key}: sha256 {actual} does not match the recorded {expected}")
        _toc, rows = verify_dump(path, dest_dir, runner=runner)
        for table, count in _recorded_rows(metadata).items():
            if rows.get(table) != count:
                raise BackupError(
                    f"{key}: {table} holds {rows.get(table)} rows, the backup recorded {count}"
                )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, metadata


def _psql(
    database: str, sql: str, *, run_as: str | None, runner: Runner, csv_out: bool = False
) -> str:
    argv = [
        "psql",
        "--no-password",
        "--no-psqlrc",
        "--quiet",
        "--tuples-only",
        *(["--csv"] if csv_out else ["--no-align"]),
        f"--dbname={database}",
        f"--command={sql}",
    ]
    result = runner(
        as_user(argv, run_as),
        capture_output=True,
        text=True,
        check=False,
        timeout=QUERY_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        tail = " | ".join((result.stderr or "").strip().splitlines()[-3:])
        raise BackupError(f"psql against {database} failed: {tail}")
    return result.stdout


def require_empty(database: str, *, run_as: str | None, runner: Runner) -> None:
    """Refuse a target that holds any relation outside the system schemas."""
    sql = (
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'"
    )
    count = int(_psql(database, sql, run_as=run_as, runner=runner).strip() or 0)
    if count:
        raise BackupError(
            f"{database} is not empty ({count} relations); restore only into a new, empty database"
        )


def restore_into(path: Path, database: str, *, run_as: str | None, runner: Runner) -> None:
    """Load ``path`` into ``database``, all or nothing, reading the archive on stdin so
    ``postgres`` never needs to read a file root wrote."""
    argv = as_user(
        [
            "pg_restore",
            "--no-password",
            "--exit-on-error",
            "--single-transaction",
            f"--dbname={database}",
        ],
        run_as,
    )
    with path.open("rb") as handle:
        result = runner(
            argv,
            stdin=handle,
            capture_output=True,
            text=True,
            check=False,
            timeout=PG_DUMP_TIMEOUT_SECONDS,
        )
    if result.returncode != 0:
        tail = " | ".join((result.stderr or "").strip().splitlines()[-3:])
        raise BackupError(f"pg_restore exited {result.returncode}: {tail}")


def check_restored(
    database: str, metadata: Mapping[str, str], *, run_as: str | None, runner: Runner
) -> list[str]:
    """What differs between the restored database and the dump's own record of itself."""
    problems = []
    head = read_alembic_head(
        database, runner=lambda argv, **kw: runner(as_user(argv, run_as), **kw)
    )
    if head != (metadata.get("alembic_head") or None):
        problems.append(f"alembic head {head}, dump recorded {metadata.get('alembic_head')}")
    expected = _recorded_rows(metadata)
    if expected:
        # Names validated against _TABLE_RE by _recorded_rows: the bucket's metadata
        # is the writer's claim, and these become SQL.
        sql = " UNION ALL ".join(
            f"SELECT '{t}', count(*) FROM {t}"  # noqa: S608
            for t in sorted(expected)
        )
        out = _psql(database, sql, run_as=run_as, runner=runner)
        restored = {
            table: int(count)
            for table, _, count in (line.partition("|") for line in out.splitlines() if line)
        }
        problems += [
            f"{table}: {restored.get(table)} rows restored, {count} in the dump"
            for table, count in sorted(expected.items())
            if restored.get(table) != count
        ]
    return problems


@dataclass
class CrosswalkCheck:
    """One crosswalk's published keys against the restored registry."""

    version: str = ""
    published: int = 0
    matched: int = 0
    missing: int = 0
    reassigned: int = 0
    merge_changed: int = 0
    newer: int = 0


def _complete(version: Path) -> bool:
    """Whether ``data.csv`` is the file its ``datapackage.json`` describes. A publish
    that crashed leaves its version directory behind, unlisted and possibly partial."""
    try:
        package = json.loads((version / "datapackage.json").read_text())
    except (OSError, ValueError):
        return False
    resource = next((r for r in package.get("resources", []) if r.get("path") == "data.csv"), None)
    data = version / "data.csv"
    return (
        resource is not None
        and data.is_file()
        and resource.get("hash") == f"sha256:{sha256_file(data)}"
    )


def published_version(root: Path, kind: str, at: datetime | None) -> Path | None:
    """The newest complete ``<kind>_crosswalk`` version published at or before ``at``.

    Not the catalog's latest: a dump older than the newest publish — a rollback to an
    older object, or a night the backup failed while the pipeline published — would
    read every key registered since as missing.
    """
    base = root / f"{kind}_crosswalk"
    if not base.is_dir():
        return None
    stamped = []
    for path in base.iterdir():
        match = _VERSION_RE.match(path.name)
        if match and path.is_dir():
            when = datetime.strptime(match[1], KEY_TIME_FORMAT).replace(tzinfo=UTC)
            if at is None or when <= at:
                stamped.append((when, path))
    return next((path for _, path in sorted(stamped, reverse=True) if _complete(path)), None)


def _published_crosswalk(data: Path) -> dict[str, tuple[str, str | None]]:
    """``natural_key -> (entity_id, merged_into)`` from one version's ``data.csv``."""
    with data.open(newline="") as handle:
        return {
            row["natural_key"]: (row["entity_id"], row["merged_into"] or None)
            for row in csv.DictReader(handle)
        }


def _ulid(text: str) -> str | None:
    return str(ULID.from_uuid(uuid.UUID(text))) if text else None


#: Every registered key with its entity's tombstone — one query, nothing interpolated.
_CROSSWALK_SQL = (
    "SELECT k.kind, k.natural_key, k.entity_id, e.merged_into FROM registry.entity_keys k "
    "JOIN registry.entities e ON e.id = k.entity_id"
)


def _restored_crosswalks(
    database: str, *, run_as: str | None, runner: Runner
) -> dict[str, dict[str, tuple[str, str | None]]]:
    """``kind -> natural_key -> (entity_id, merged_into)`` from the restored registry,
    for the kinds that publish a crosswalk."""
    out = _psql(database, _CROSSWALK_SQL, run_as=run_as, runner=runner, csv_out=True)
    crosswalks: dict[str, dict[str, tuple[str, str | None]]] = {k: {} for k in CROSSWALK_KINDS}
    for kind, key, entity, merged in csv.reader(io.StringIO(out)):
        if kind in crosswalks:
            crosswalks[kind][key] = (_ulid(entity), _ulid(merged))
    return crosswalks


def check_crosswalk(
    database: str,
    datasets_root: Path,
    *,
    dumped_at: datetime | None,
    run_as: str | None,
    runner: Runner,
) -> dict[str, Any]:
    """Every key the dump's own publish carried, against the restored registry.

    The reference is the newest complete version published at or before the dump
    (:func:`published_version`). A key missing, or on a different ULID, is a problem —
    ULIDs never move (an adjudicated key move since that publish reads as one too:
    check ``registry.adjudications``). A ``merged_into`` that differs is counted but
    not a problem, and nor is a key registered since. A kind with no such version is
    ``skipped`` — a fresh host has no datasets.
    """
    result: dict[str, Any] = {"problems": []}
    restored_all: dict[str, dict[str, tuple[str, str | None]]] | None = None
    for kind in CROSSWALK_KINDS:
        version = published_version(datasets_root, kind, dumped_at)
        if version is None:
            reason = f"no complete {kind}_crosswalk published at or before the dump"
            result[kind] = {"skipped": f"{reason} under {datasets_root}"}
            continue
        published = _published_crosswalk(version / "data.csv")
        if restored_all is None:
            restored_all = _restored_crosswalks(database, run_as=run_as, runner=runner)
        restored = restored_all[kind]
        check = CrosswalkCheck(
            version=version.name,
            published=len(published),
            newer=len(restored.keys() - published.keys()),
        )
        missing, reassigned = [], []
        for key, (entity, merged) in sorted(published.items()):
            if key not in restored:
                missing.append(key)
            elif restored[key][0] != entity:
                reassigned.append(key)
            else:
                check.matched += 1
                check.merge_changed += restored[key][1] != merged
        check.missing, check.reassigned = len(missing), len(reassigned)
        for label, keys in (("missing", missing), ("on a different ULID", reassigned)):
            if keys:
                named = ", ".join(keys[:_NAMED])
                result["problems"].append(f"{kind}: {len(keys)} published key(s) {label} ({named})")
        result[kind] = asdict(check)
    return result


def _dumped_at(metadata: Mapping[str, str]) -> datetime | None:
    """The dump's recorded start; ``None`` for an object without one."""
    try:
        return datetime.strptime(metadata.get("dumped_at", ""), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


def _print_listing(client: Any, bucket: str, prefix: str | None) -> int:
    snapshots = list_snapshots(client, bucket, prefix)
    for snapshot in snapshots:
        created = iso(snapshot.created) if snapshot.created else ""
        print(
            snapshot.name,
            *(f"{name}={snapshot.metadata.get(name, '')}" for name in _LISTED_METADATA),
            f"created={created}",
            *(["SUSPECT(named after the bucket created it)"] if snapshot.suspect else []),
        )
    return len(snapshots)


def _restore_database(ctx: JobContext, client: Any, bucket: str) -> JobResult:
    args = ctx.args
    key = args.object or latest_key(client, bucket, args.prefix)
    if args.download_only:
        path, _ = fetch_dump(client, bucket, key, args.download_only, runner=run_command)
        return JobResult.ok({"object": f"gs://{bucket}/{key}", "verified": str(path)})
    require_empty(args.into, run_as=args.run_as, runner=run_command)
    with tempfile.TemporaryDirectory(prefix="usa-wa-restore-") as work:
        path, metadata = fetch_dump(client, bucket, key, Path(work), runner=run_command)
        restore_into(path, args.into, run_as=args.run_as, runner=run_command)
    problems = check_restored(args.into, metadata, run_as=args.run_as, runner=run_command)
    crosswalk = check_crosswalk(
        args.into,
        args.datasets_root,
        dumped_at=_dumped_at(metadata),
        run_as=args.run_as,
        runner=run_command,
    )
    problems += crosswalk.pop("problems")
    counters = {
        "object": f"gs://{bucket}/{key}",
        "into": args.into,
        "registry_rows": _recorded_rows(metadata),
        "crosswalk": crosswalk,
        "problems": problems,
    }
    if problems:
        for problem in problems:
            logger.error("restore_check_failed", extra={"problem": problem})
        return JobResult.failed(counters)
    return JobResult.ok(counters)


def _usage_error(args: argparse.Namespace) -> str | None:
    if (args.list or args.raw_into) and (args.into or args.download_only):
        # Mid-incident, an ignored --into reads as a load that happened.
        mode = "--list" if args.list else "--raw-into"
        return f"{mode} takes no --into or --download-only; a database restore is --latest/--object"
    if args.latest and not args.prefix:
        return (
            "say whose: --latest needs --prefix HOST, the host that shipped the dump "
            "(--list shows every host's)"
        )
    if (args.latest or args.object) and not (args.into or args.download_only):
        return "say where: --into DATABASE or --download-only DIR"
    return None


async def _handler(ctx: JobContext) -> JobResult:
    args = ctx.args
    bucket = os.environ.get(BUCKET_ENV, "").strip()
    error = f"{BUCKET_ENV} not set" if not bucket else _usage_error(args)
    if error:
        print(error, file=sys.stderr)
        return JobResult.failed({"error": error}, exit_code=EXIT_CONFIG)
    # Inside the harness's try: a missing or revoked key raises from google.auth here,
    # and an operator mid-incident gets one line, not a traceback.
    client = make_client()
    if args.list:
        return JobResult.ok({"dumps": _print_listing(client, bucket, args.prefix)})
    if args.raw_into:
        fetched = fetch_mirror(client, bucket, args.raw_into)
        return JobResult.ok({"raw_into": str(args.raw_into), **asdict(fetched)})
    return _restore_database(ctx, client, bucket)


def _add_args(parser: argparse.ArgumentParser) -> None:
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--list", action="store_true", help="list the database dumps and exit")
    which.add_argument("--latest", action="store_true", help="the newest dump of --prefix HOST")
    which.add_argument("--object", metavar="KEY", help="a dump by its object key")
    which.add_argument(
        "--raw-into", metavar="DIR", type=Path, help="fetch the raw-store mirror into a new DIR"
    )
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--into", metavar="DATABASE", help="restore into this new, empty database")
    where.add_argument("--download-only", metavar="DIR", type=Path, help="fetch and verify only")
    parser.add_argument(
        "--prefix", metavar="HOST", help="the host whose dumps to use (required with --latest)"
    )
    parser.add_argument("--run-as", help="OS user for pg_restore and psql (peer auth)")
    parser.add_argument(
        "--datasets-root",
        type=Path,
        default=Path(os.environ.get(DATASETS_ROOT_ENV, "data/datasets")),
        help="the published datasets the restored crosswalk is checked against",
    )


def main(argv: list[str] | None = None) -> int:
    """Operator entrypoint; see the module docstring."""
    return run_job(
        JOB_SLUG,
        _handler,
        argv=argv,
        description="Restore the #434 backup: a database dump, or the raw-store mirror",
        prog="python -m usa_wa_api.backup.restore",
        extra_args=_add_args,
        needs_db=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
