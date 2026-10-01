"""The #434 backup and restore checks against a real Postgres — the test database.

The unit tests drive fakes shaped like ``pg_dump``, ``pg_restore`` and ``psql``; this
runs the real binaries, so a TOC line, a ``COPY`` header or a ``--csv`` row the
parsers misread fails here rather than on the first night in production. The test
database stands in for production: it carries the registry schema (``create_all``),
and a committed handful of rows gives the counts and the crosswalk something to say.

What it does not do is load a dump into a second database — the test role cannot
create one. That half is the restore drill, run by hand on the host
(docs/RECOVERY.md § Rehearsals).
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from ulid import ULID

from usa_wa_api.backup.dump import dump_metadata, take_dump
from usa_wa_api.backup.restore import check_crosswalk, check_restored

pytestmark = pytest.mark.db

ENTITIES = [ULID.from_uuid(uuid.uuid4()) for _ in range(2)]
KEYS = [("rehearsal:1", ENTITIES[0]), ("rehearsal:2", ENTITIES[1])]


def libpq_dsn() -> str:
    """``TEST_DATABASE_URL`` as the command-line tools take it (no ``+asyncpg``)."""
    return os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture
async def committed_registry(test_engine):
    """Rows a separate process can see — committed, then removed."""
    async with test_engine.begin() as conn:
        existed = (
            await conn.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
        ).scalar()
        if not existed:
            await conn.execute(
                text("CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY)")
            )
            await conn.execute(text("INSERT INTO public.alembic_version VALUES ('rehearsal')"))
        for entity in ENTITIES:
            await conn.execute(
                text(
                    "INSERT INTO registry.entities (id, kind, created_at) "
                    "VALUES (:id, 'person', now())"
                ),
                {"id": entity.to_uuid()},
            )
        for key, entity in KEYS:
            await conn.execute(
                text(
                    "INSERT INTO registry.entity_keys "
                    "(id, kind, natural_key, entity_id, registered_at, registered_by) "
                    "VALUES (:id, 'person', :key, :entity, now(), 'rehearsal')"
                ),
                {"id": uuid.uuid4(), "key": key, "entity": entity.to_uuid()},
            )
    try:
        yield
    finally:
        async with test_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM registry.entity_keys WHERE registered_by = 'rehearsal'")
            )
            await conn.execute(
                text("DELETE FROM registry.entities WHERE id = ANY(:ids)"),
                {"ids": [entity.to_uuid() for entity in ENTITIES]},
            )
            if not existed:
                await conn.execute(text("DROP TABLE public.alembic_version"))


def published(root, rows) -> None:
    """A catalog naming a person crosswalk of ``rows`` and an empty org crosswalk."""
    datasets = []
    for kind, kind_rows in (("person", rows), ("org", [])):
        target = root / f"{kind}_crosswalk" / "v1"
        target.mkdir(parents=True)
        with (target / "data.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["entity_id", "natural_key", "merged_into"])
            writer.writerows([str(entity), key, ""] for key, entity in kind_rows)
        datasets.append({"name": f"{kind}_crosswalk", "latest_version": "v1"})
    (root / "catalog.json").write_text(json.dumps({"datasets": datasets}))


async def test_a_real_dump_verifies_and_describes_the_database(
    committed_registry, tmp_path
) -> None:
    dsn = libpq_dsn()
    dump = take_dump(dsn, tmp_path / "work", runner=subprocess.run, now=lambda: datetime.now(UTC))
    assert dump.toc.pg_dump_version
    assert dump.registry_rows["registry.entities"] >= len(ENTITIES)
    assert dump.registry_rows["registry.entity_keys"] >= len(KEYS)
    # The database is its own dump's restore: the checks must find nothing.
    meta = dump_metadata(dump, host="rehearsal")
    assert check_restored(dsn, meta, run_as=None, runner=subprocess.run) == []


async def test_the_crosswalk_check_reads_the_registry_as_published(
    committed_registry, tmp_path
) -> None:
    published(tmp_path, KEYS)
    result = check_crosswalk(libpq_dsn(), tmp_path, run_as=None, runner=subprocess.run)
    assert result["problems"] == []
    assert result["person"]["matched"] == len(KEYS)


async def test_the_crosswalk_check_catches_a_moved_ulid(committed_registry, tmp_path) -> None:
    published(tmp_path, [(KEYS[0][0], KEYS[1][1]), KEYS[1]])
    result = check_crosswalk(libpq_dsn(), tmp_path, run_as=None, runner=subprocess.run)
    assert result["problems"] == ["person: 1 published key(s) on a different ULID (rehearsal:1)"]
