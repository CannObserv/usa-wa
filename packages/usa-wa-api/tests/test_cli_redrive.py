"""python -m usa_wa_api.cli.redrive re-drives UNAVAILABLE outbox entries.

Exercises arg parsing, the in-loop ``_run`` (session open → perform → commit)
against the savepointed test session, and ``main``'s arg-wiring + JSON output
(with ``_run`` patched, since ``main`` spins its own event loop via
``asyncio.run``, which cannot share the session-scoped test loop).
"""

import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.models import (
    OP_CREATE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    OutboxEntry,
)
from usa_wa_api.cli import redrive as cli


async def _statuses(db_session) -> list[str]:
    rows = (await db_session.execute(select(OutboxEntry.status))).scalars().all()
    return sorted(rows)


def _patch_factory(monkeypatch, db_session):
    """Make get_session_factory yield the savepointed test session."""

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(cli, "get_session_factory", lambda: _ctx)


def test_parser_defaults():
    args = cli._build_parser().parse_args([])
    assert args.entity_type is None
    assert args.older_than_seconds is None
    assert args.limit is None
    assert args.dry_run is False


def test_parser_flags():
    args = cli._build_parser().parse_args(
        ["--entity-type", "person", "--older-than-seconds", "3600", "--limit", "5", "--dry-run"]
    )
    assert args.entity_type == "person"
    assert args.older_than_seconds == 3600
    assert args.limit == 5
    assert args.dry_run is True


def test_parser_rejects_negative_older_than_seconds():
    """A negative age inverts the filter (matches everything, including future rows),
    silently turning a scoped re-drive unscoped — reject it, mirroring Query(ge=0)."""
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(["--older-than-seconds", "-5"])


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_parser_rejects_non_positive_limit(bad):
    """A limit < 1 flips nothing — reject it, mirroring Query(ge=1)."""
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(["--limit", bad])


async def test_run_redrives_and_commits(monkeypatch, db_session):
    db_session.add_all(
        [
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            ),
            OutboxEntry(entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_REJECTED),
        ]
    )
    await db_session.flush()
    _patch_factory(monkeypatch, db_session)

    result = await cli._run(entity_type=None, older_than_seconds=None, limit=None, dry_run=False)

    assert result["matched"] == 1
    assert result["redriven"] == 1
    assert await _statuses(db_session) == [STATUS_PENDING, STATUS_REJECTED]


async def test_run_dry_run_does_not_mutate(monkeypatch, db_session):
    db_session.add(
        OutboxEntry(entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE)
    )
    await db_session.flush()
    _patch_factory(monkeypatch, db_session)

    result = await cli._run(entity_type=None, older_than_seconds=None, limit=None, dry_run=True)

    assert result["matched"] == 1
    assert result["redriven"] == 0
    assert await _statuses(db_session) == [STATUS_UNAVAILABLE]


async def test_run_caps_with_limit(monkeypatch, db_session):
    """--limit caps the flip; the JSON result surfaces would_redrive for the cap."""
    db_session.add_all(
        [
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            )
            for _ in range(3)
        ]
    )
    await db_session.flush()
    _patch_factory(monkeypatch, db_session)

    result = await cli._run(entity_type=None, older_than_seconds=None, limit=2, dry_run=False)

    assert result["matched"] == 3  # full pile
    assert result["would_redrive"] == 2
    assert result["redriven"] == 2  # capped
    assert await _statuses(db_session) == [STATUS_PENDING, STATUS_PENDING, STATUS_UNAVAILABLE]


async def test_run_dry_run_reflects_limit_cap(monkeypatch, db_session):
    """A dry-run --limit preview surfaces the capped would_redrive, mutating nothing."""
    db_session.add_all(
        [
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            )
            for _ in range(4)
        ]
    )
    await db_session.flush()
    _patch_factory(monkeypatch, db_session)

    result = await cli._run(entity_type=None, older_than_seconds=None, limit=2, dry_run=True)

    assert result["matched"] == 4
    assert result["would_redrive"] == 2
    assert result["redriven"] == 0
    assert await _statuses(db_session) == [STATUS_UNAVAILABLE] * 4


async def test_run_scopes_by_entity_type(monkeypatch, db_session):
    db_session.add_all(
        [
            OutboxEntry(
                entity_type="person", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            ),
            OutboxEntry(
                entity_type="organization",
                local_id=ULID(),
                op=OP_CREATE,
                status=STATUS_UNAVAILABLE,
            ),
        ]
    )
    await db_session.flush()
    _patch_factory(monkeypatch, db_session)

    result = await cli._run(
        entity_type="person", older_than_seconds=None, limit=None, dry_run=False
    )

    assert result["matched"] == 1
    assert result["redriven"] == 1
    rows = (
        await db_session.execute(
            select(OutboxEntry.entity_type, OutboxEntry.status).order_by(OutboxEntry.entity_type)
        )
    ).all()
    assert dict(rows) == {"organization": STATUS_UNAVAILABLE, "person": STATUS_PENDING}


def test_main_wires_args_and_prints_json(monkeypatch, capsys):
    """main parses flags, calls _run with them, and prints the result as JSON."""
    seen = {}

    async def _fake_run(entity_type, older_than_seconds, limit, dry_run):
        seen["args"] = (entity_type, older_than_seconds, limit, dry_run)
        return {"matched": 3, "redriven": 0, "dry_run": dry_run, "entity_type": entity_type}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main(
        ["--entity-type", "person", "--older-than-seconds", "60", "--limit", "5", "--dry-run"]
    )

    assert rc == 0
    assert seen["args"] == ("person", 60, 5, True)
    body = json.loads(capsys.readouterr().out)
    assert body == {"matched": 3, "redriven": 0, "dry_run": True, "entity_type": "person"}
