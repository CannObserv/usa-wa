"""Fakes and fixtures shared by the #434 backup and restore tests.

The command-line half of the doubles: ``FakeRunner`` stands in for ``subprocess.run``
across ``pg_dump``, ``pg_restore`` and ``psql``, shaped on their real output (the
db-tier ``test_backup_rehearsal`` checks the parsers against the real binaries);
``harvest`` lands one raw-store run. The bucket's doubles are ``gcs_fakes``.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

from clearinghouse_core.rawstore import RawStore
from usa_wa_api.backup.dump import REQUIRED_TABLES

TOC_HEADER = """\
;
; Archive created at 2026-09-26 23:11:13 UTC
;     dbname: usa_wa
;     TOC Entries: 499
;     Compression: gzip
;     Format: CUSTOM
;     Dumped from database version: 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)
;     Dumped by pg_dump version: 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)
;
; Selected TOC Entries:
;
11; 2615 2275505 SCHEMA - registry usa_wa_owner
"""


def toc_with(tables) -> str:
    """A ``pg_restore --list`` listing carrying a data section for each table."""
    lines = [
        f"{4000 + i}; 0 {20000 + i} TABLE DATA {t.split('.')[0]} {t.split('.')[1]} usa_wa_owner"
        for i, t in enumerate(tables)
    ]
    return TOC_HEADER + "\n".join(lines) + "\n"


def data_sql(rows: dict[str, int]) -> str:
    """A ``pg_restore --data-only`` script with ``rows[table]`` COPY rows per table."""
    out = ["--\n-- PostgreSQL database dump\n--\n", "SET statement_timeout = 0;\n"]
    for table, count in rows.items():
        out.append(f"COPY {table} (id, kind) FROM stdin;\n")
        out.extend(f"row-{n}\tperson\n" for n in range(count))
        out.append("\\.\n\n")
    return "".join(out)


FULL = {table: 3 for table in REQUIRED_TABLES} | {"public.alembic_version": 1}


class FakeRunner:
    """Stands in for ``subprocess.run`` across pg_dump, pg_restore and psql."""

    def __init__(
        self,
        *,
        toc: str | None = None,
        data: str | None = None,
        head: str = "abc123\n",
        fail: str | None = None,
    ) -> None:
        self.toc = toc if toc is not None else toc_with(FULL)
        self.data = data if data is not None else data_sql(FULL)
        self.head = head
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        tool = argv[0]
        if "--list" in argv:
            step = "list"
        elif "--data-only" in argv:
            step = "data"
        else:
            step = tool
        if self.fail == step:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=f"{step}: boom\n")
        if step == "pg_dump":
            kwargs["stdout"].write(b"PGDMP-archive-bytes")
            return subprocess.CompletedProcess(argv, 0, stderr=b"")
        if step == "list":
            return subprocess.CompletedProcess(argv, 0, stdout=self.toc, stderr="")
        if step == "data":
            kwargs["stdout"].write(self.data.encode())
            return subprocess.CompletedProcess(argv, 0, stderr=b"")
        if step == "psql":
            return subprocess.CompletedProcess(argv, 0, stdout=self.head, stderr="")
        raise AssertionError(f"unexpected command {argv}")


def harvest(root, source: str, bodies: dict[str, bytes]) -> RawStore:
    """One closed run recording ``bodies`` under ``source``."""
    store = RawStore(root, source)
    run = store.open_run()
    for resource, body in bodies.items():
        run.record(
            resource, body, url=f"urn:{resource}", fetched_at=datetime(2026, 9, 1, tzinfo=UTC)
        )
    run.close()
    return store
