"""The database half of the #434 backup: ``pg_dump``, verified, described.

**It holds no database credential.** ``pg_dump`` and the ``psql`` that reads the
schema version connect over the local socket by peer auth, as the unit's dynamic
user, to the role of the same name — ``pg_read_all_data`` and nothing else
(``scripts/setup-backup-role.sql``).

**It verifies before it ships.** ``pg_restore --list`` must read the archive and
find a data section for every table in :data:`REQUIRED_TABLES` — a readable dump
of the wrong database is refused. Then ``pg_restore --data-only`` decompresses every
data block through to the end, since custom format writes its table of contents
first and a truncated archive still lists. That read also counts each table's
``COPY`` rows, and the registry's counts travel with the object: they are what the
restore checks a restored database against, from the same snapshot.

**The object is named by the dump's start** — when ``pg_dump`` took its snapshot —
so a listing is a timeline and the newest name is the newest data.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from usa_wa_api.backup.gcs import BackupError, iso, sha256_file

Runner = Callable[..., subprocess.CompletedProcess]

DB_PREFIX = "db"
OBJECT_SUFFIX = ".dump"
# Basic-format ISO 8601, UTC: sorts as it reads, and no ':' for a shell to trip on.
KEY_TIME_FORMAT = "%Y%m%dT%H%M%SZ"

#: A dump lacking a data section for any of these is not a backup of this database.
#: The registry is why the backup exists (#434): nothing else can rebuild it.
REQUIRED_TABLES = (
    "public.alembic_version",
    "registry.entities",
    "registry.entity_keys",
    "registry.adjudications",
    "registry.operator_events",
    "registry.committee_succession_events",
)
#: The schema whose per-table row counts ride with the object as metadata.
COUNTED_SCHEMA = "registry"

# The dump measured 13.2 MB in 2.2 s (#430, 2026-09-26). Generous for that, and
# short enough that a wedged call is a failed unit rather than a hang.
PG_DUMP_TIMEOUT_SECONDS = 1800
QUERY_TIMEOUT_SECONDS = 60

_HEADER_RE = re.compile(r"^;\s+(Dumped from database version|Dumped by pg_dump version): (.+)$")
_ENTRIES_RE = re.compile(r"^;\s+TOC Entries: (\d+)$")
_TABLE_DATA_RE = re.compile(r"^\d+; \d+ \d+ TABLE DATA (\S+) (\S+) ")
_COPY_RE = re.compile(r"^COPY (\S+) .*FROM stdin;$")
_COPY_END = "\\."


@dataclass(frozen=True)
class Toc:
    """What ``pg_restore --list`` says about an archive."""

    server_version: str | None = None
    pg_dump_version: str | None = None
    entries: int | None = None
    tables_with_data: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Dump:
    """A verified dump on local disk, and what it says about itself."""

    path: Path
    size_bytes: int
    sha256: str
    dumped_at: datetime
    alembic_head: str | None
    toc: Toc
    registry_rows: dict[str, int]


def parse_toc(text: str) -> Toc:
    """The header fields and the tables with a data section."""
    versions: dict[str, str] = {}
    entries: int | None = None
    tables: set[str] = set()
    for line in text.splitlines():
        if match := _HEADER_RE.match(line):
            versions[match.group(1)] = match.group(2).strip()
        elif match := _ENTRIES_RE.match(line):
            entries = int(match.group(1))
        elif match := _TABLE_DATA_RE.match(line):
            tables.add(f"{match.group(1)}.{match.group(2)}")
    return Toc(
        server_version=versions.get("Dumped from database version"),
        pg_dump_version=versions.get("Dumped by pg_dump version"),
        entries=entries,
        tables_with_data=frozenset(tables),
    )


def parse_copy_rows(lines: Iterable[str]) -> dict[str, int]:
    """Rows per table in a ``pg_restore --data-only`` script.

    COPY text format puts one row on one line (a newline in a value is escaped) and
    escapes a backslash, so only a line that is exactly ``\\.`` ends a block.
    """
    rows: dict[str, int] = {}
    table: str | None = None
    for raw in lines:
        line = raw.rstrip("\n")
        if table is None:
            if match := _COPY_RE.match(line):
                table = match.group(1)
                rows[table] = 0
        elif line == _COPY_END:
            table = None
        else:
            rows[table] += 1
    return rows


def dump_key(prefix: str, dumped_at: datetime) -> str:
    """``db/<host>/<YYYYMMDDTHHMMSSZ>.dump``."""
    stamp = dumped_at.astimezone(UTC).strftime(KEY_TIME_FORMAT)
    return f"{DB_PREFIX}/{prefix.strip('/')}/{stamp}{OBJECT_SUFFIX}"


def _tail(text: str | bytes | None) -> str:
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return " | ".join((text or "").strip().splitlines()[-3:])


def read_alembic_head(database: str, *, runner: Runner) -> str | None:
    """The schema version, recorded so a restore can be checked against it."""
    argv = [
        "psql",
        "--no-password",
        "--no-psqlrc",
        "--quiet",
        "--tuples-only",
        "--no-align",
        f"--dbname={database}",
        "--command=SELECT version_num FROM alembic_version",
    ]
    result = runner(
        argv, capture_output=True, text=True, check=False, timeout=QUERY_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        raise BackupError(f"psql could not read alembic_version: {_tail(result.stderr)}")
    return result.stdout.strip() or None


def run_pg_dump(database: str, out: Path, *, runner: Runner) -> None:
    """Custom format into ``out``, through an fd this process opened."""
    argv = ["pg_dump", "--format=custom", "--no-password", f"--dbname={database}"]
    with out.open("wb") as handle:
        result = runner(
            argv,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
            timeout=PG_DUMP_TIMEOUT_SECONDS,
        )
    if result.returncode != 0:
        raise BackupError(f"pg_dump exited {result.returncode}: {_tail(result.stderr)}")


def verify_dump(path: Path, workdir: Path, *, runner: Runner) -> tuple[Toc, dict[str, int]]:
    """Refuse an archive ``pg_restore`` cannot read, or one of the wrong database;
    return its table of contents and the rows each table's data section holds.

    Two reads. ``--list`` reads the header and the table of contents, which says
    whose database it is. ``--data-only`` then decompresses every data block
    through to the end (a truncated archive lists cleanly) into a scratch script
    in ``workdir``, which is counted and removed.
    """
    listed = runner(
        ["pg_restore", "--list", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=QUERY_TIMEOUT_SECONDS,
    )
    if listed.returncode != 0:
        raise BackupError(f"pg_restore --list rejected {path.name}: {_tail(listed.stderr)}")
    toc = parse_toc(listed.stdout)
    missing = [table for table in REQUIRED_TABLES if table not in toc.tables_with_data]
    if missing:
        raise BackupError(f"dump has no data for {', '.join(missing)}; refusing to ship it")
    script = workdir / f".{path.name}.data.sql"
    try:
        with script.open("wb") as handle:
            full = runner(
                ["pg_restore", "--data-only", "--file=-", str(path)],
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
                timeout=PG_DUMP_TIMEOUT_SECONDS,
            )
        if full.returncode != 0:
            raise BackupError(
                f"pg_restore could not read {path.name} through: {_tail(full.stderr)}"
            )
        with script.open(encoding="utf-8", errors="replace") as handle:
            rows = parse_copy_rows(handle)
    finally:
        script.unlink(missing_ok=True)
    return toc, rows


def take_dump(database: str, workdir: Path, *, runner: Runner, now: Callable[[], datetime]) -> Dump:
    """Dump, verify, describe. Named by its start: ``pg_dump`` takes its snapshot as
    it begins."""
    dumped_at = now().astimezone(UTC).replace(microsecond=0)
    alembic_head = read_alembic_head(database, runner=runner)
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "usa_wa.dump"
    run_pg_dump(database, path, runner=runner)
    toc, rows = verify_dump(path, workdir, runner=runner)
    return Dump(
        path=path,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        dumped_at=dumped_at,
        alembic_head=alembic_head,
        toc=toc,
        registry_rows={
            table: count
            for table, count in sorted(rows.items())
            if table.startswith(f"{COUNTED_SCHEMA}.")
        },
    )


def dump_metadata(dump: Dump, *, host: str) -> dict[str, str]:
    """What travels with the object — everything a restore checks without trusting
    the file. GCS metadata values are strings."""
    return {
        "dumped_at": iso(dump.dumped_at),
        "sha256": dump.sha256,
        "size_bytes": str(dump.size_bytes),
        "alembic_head": dump.alembic_head or "",
        "server_version": dump.toc.server_version or "",
        "pg_dump_version": dump.toc.pg_dump_version or "",
        "toc_entries": "" if dump.toc.entries is None else str(dump.toc.entries),
        "registry_rows": json.dumps(dump.registry_rows, sort_keys=True, separators=(",", ":")),
        "source_host": host,
    }
