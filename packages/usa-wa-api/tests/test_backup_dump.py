"""The database half of the #434 nightly backup: dump, verify, describe."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usa_wa_api.backup.dump import (
    REQUIRED_TABLES,
    dump_key,
    dump_metadata,
    parse_copy_rows,
    parse_toc,
    take_dump,
    verify_dump,
)
from usa_wa_api.backup.gcs import BackupError

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


NOW = datetime(2026, 10, 1, 10, 17, 3, 456, tzinfo=UTC)


def test_parse_toc_reads_the_header_and_the_data_sections() -> None:
    toc = parse_toc(toc_with(["registry.entities", "public.alembic_version"]))
    assert toc.server_version.startswith("16.14")
    assert toc.pg_dump_version.startswith("16.14")
    assert toc.entries == 499
    assert toc.tables_with_data == {"registry.entities", "public.alembic_version"}


def test_parse_copy_rows_counts_every_table() -> None:
    rows = parse_copy_rows(data_sql({"registry.entities": 2, "serving.persons": 0}).splitlines())
    assert rows == {"registry.entities": 2, "serving.persons": 0}


def test_a_row_that_looks_like_a_terminator_only_ends_the_block_alone() -> None:
    """COPY text escapes a backslash, so only a bare ``\\.`` line ends a block."""
    lines = ["COPY registry.notes (id, body) FROM stdin;", "1\\tends with \\\\.", "\\."]
    assert parse_copy_rows(lines) == {"registry.notes": 1}


def test_dump_key_is_db_host_and_the_start_second() -> None:
    assert dump_key("usa-wa", NOW) == "db/usa-wa/20261001T101703Z.dump"


class TestVerifyDump:
    def test_a_good_archive_yields_its_toc_and_row_counts(self, tmp_path) -> None:
        archive = tmp_path / "usa_wa.dump"
        archive.write_bytes(b"x")
        toc, rows = verify_dump(archive, tmp_path, runner=FakeRunner())
        assert toc.tables_with_data >= set(REQUIRED_TABLES)
        assert rows["registry.entities"] == 3
        # The decompressed script is scratch, not something left in the run's /tmp.
        assert [p.name for p in tmp_path.iterdir()] == ["usa_wa.dump"]

    def test_the_wrong_database_is_refused(self, tmp_path) -> None:
        """A readable dump without the registry is not a backup of this database."""
        archive = tmp_path / "usa_wa.dump"
        archive.write_bytes(b"x")
        runner = FakeRunner(toc=toc_with(["public.alembic_version", "public.watched_items"]))
        with pytest.raises(BackupError, match="registry.entities"):
            verify_dump(archive, tmp_path, runner=runner)

    def test_an_unreadable_archive_is_refused(self, tmp_path) -> None:
        archive = tmp_path / "usa_wa.dump"
        archive.write_bytes(b"x")
        with pytest.raises(BackupError, match="list"):
            verify_dump(archive, tmp_path, runner=FakeRunner(fail="list"))

    def test_a_truncated_archive_fails_the_read_through(self, tmp_path) -> None:
        """Custom format writes its table of contents first, so a truncated archive
        still lists; only reading every data block catches it."""
        archive = tmp_path / "usa_wa.dump"
        archive.write_bytes(b"x")
        with pytest.raises(BackupError, match="read .* through"):
            verify_dump(archive, tmp_path, runner=FakeRunner(fail="data"))
        assert [p.name for p in tmp_path.iterdir()] == ["usa_wa.dump"]


class TestTakeDump:
    def test_dump_verify_describe(self, tmp_path) -> None:
        runner = FakeRunner()
        dump = take_dump("usa_wa", tmp_path, runner=runner, now=lambda: NOW)
        assert dump.dumped_at == NOW.replace(microsecond=0)
        assert dump.alembic_head == "abc123"
        assert dump.size_bytes == len(b"PGDMP-archive-bytes")
        assert dump.registry_rows == {
            table: 3 for table in REQUIRED_TABLES if table.startswith("registry.")
        }
        assert runner.calls[1][:2] == ["pg_dump", "--format=custom"]
        assert "--dbname=usa_wa" in runner.calls[1]

    def test_a_failed_pg_dump_ships_nothing(self, tmp_path) -> None:
        with pytest.raises(BackupError, match="pg_dump exited 1"):
            take_dump("usa_wa", tmp_path, runner=FakeRunner(fail="pg_dump"), now=lambda: NOW)

    def test_an_unreadable_schema_version_fails_before_the_dump(self, tmp_path) -> None:
        runner = FakeRunner(fail="psql")
        with pytest.raises(BackupError, match="alembic_version"):
            take_dump("usa_wa", tmp_path, runner=runner, now=lambda: NOW)
        assert [call[0] for call in runner.calls] == ["psql"]


def test_dump_metadata_is_all_strings_and_carries_the_registry_counts(tmp_path) -> None:
    dump = take_dump("usa_wa", tmp_path, runner=FakeRunner(), now=lambda: NOW)
    meta = dump_metadata(dump, host="usa-wa")
    assert all(isinstance(value, str) for value in meta.values())
    assert meta["dumped_at"] == "2026-10-01T10:17:03Z"
    assert meta["alembic_head"] == "abc123"
    assert meta["source_host"] == "usa-wa"
    assert meta["sha256"] == dump.sha256
    assert json.loads(meta["registry_rows"])["registry.entity_keys"] == 3
    assert Path(dump.path).exists()
