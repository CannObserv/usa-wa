"""POST /sync/redrive resets UNAVAILABLE outbox entries to PENDING.

Operator-friendly wrapper over ``SyncEngine.redrive_unavailable`` with optional
``entity_type`` / age scoping, a non-mutating ``dry_run`` preview, and an
operator-token auth gate (mutating surface).
"""

from datetime import UTC, datetime, timedelta

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

OPERATOR_TOKEN = "test-operator-token"
AUTH_HEADERS = {"X-Operator-Token": OPERATOR_TOKEN}


@pytest.fixture(autouse=True)
def _operator_token(monkeypatch):
    """Configure the operator token for the redrive auth gate."""
    monkeypatch.setenv("USA_WA_OPERATOR_TOKEN", OPERATOR_TOKEN)


async def _statuses(db_session) -> list[str]:
    rows = (await db_session.execute(select(OutboxEntry.status))).scalars().all()
    return sorted(rows)


async def test_redrive_requires_auth(client, db_session):
    """Unauthenticated calls are rejected without mutating."""
    db_session.add(
        OutboxEntry(entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE)
    )
    await db_session.flush()

    response = await client.post("/sync/redrive")

    assert response.status_code == 401
    assert await _statuses(db_session) == [STATUS_UNAVAILABLE]


async def test_redrive_rejects_wrong_token(client):
    response = await client.post("/sync/redrive", headers={"X-Operator-Token": "nope"})
    assert response.status_code == 401


async def test_redrive_resets_unavailable_to_pending(client, db_session):
    """A real (non-dry-run) call flips UNAVAILABLE rows to PENDING."""
    db_session.add_all(
        [
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            ),
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            ),
            # REJECTED and PENDING are left untouched.
            OutboxEntry(entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_REJECTED),
        ]
    )
    await db_session.flush()

    response = await client.post("/sync/redrive", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 2
    assert body["redriven"] == 2
    assert body["dry_run"] is False
    assert body["entity_type"] is None
    # Two flipped to PENDING; the REJECTED row stays.
    assert await _statuses(db_session) == [STATUS_PENDING, STATUS_PENDING, STATUS_REJECTED]


async def test_redrive_dry_run_previews_without_mutating(client, db_session):
    """dry_run returns the would-redrive count and changes nothing."""
    db_session.add_all(
        [
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            ),
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            ),
        ]
    )
    await db_session.flush()

    response = await client.post("/sync/redrive?dry_run=true", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 2
    assert body["redriven"] == 0
    assert body["dry_run"] is True
    # Nothing mutated.
    assert await _statuses(db_session) == [STATUS_UNAVAILABLE, STATUS_UNAVAILABLE]


async def test_redrive_scopes_by_entity_type(client, db_session):
    """entity_type filter only redrives that type."""
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

    response = await client.post("/sync/redrive?entity_type=person", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 1
    assert body["redriven"] == 1
    assert body["entity_type"] == "person"
    # The person row flipped; the organization row stays UNAVAILABLE.
    rows = (
        await db_session.execute(
            select(OutboxEntry.entity_type, OutboxEntry.status).order_by(OutboxEntry.entity_type)
        )
    ).all()
    assert dict(rows) == {"organization": STATUS_UNAVAILABLE, "person": STATUS_PENDING}


async def test_redrive_caps_with_limit(client, db_session):
    """limit caps the flip; matched still reports the full in-scope pile."""
    db_session.add_all(
        [
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            )
            for _ in range(3)
        ]
    )
    await db_session.flush()

    response = await client.post("/sync/redrive?limit=2", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 3  # full pile
    assert body["redriven"] == 2  # capped
    assert body["limit"] == 2
    assert await _statuses(db_session) == [STATUS_PENDING, STATUS_PENDING, STATUS_UNAVAILABLE]


async def test_redrive_scopes_by_age(client, db_session):
    """older_than_seconds only redrives entries aged past the threshold."""
    now = datetime.now(UTC)
    old = OutboxEntry(entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE)
    fresh = OutboxEntry(
        entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
    )
    db_session.add_all([old, fresh])
    await db_session.flush()
    # Backdate the "old" row well past the threshold; keep "fresh" recent.
    old.created_at = now - timedelta(hours=2)
    fresh.created_at = now - timedelta(seconds=1)
    await db_session.flush()

    response = await client.post("/sync/redrive?older_than_seconds=3600", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 1
    assert body["redriven"] == 1
    assert body["older_than_seconds"] == 3600
    await db_session.refresh(old)
    await db_session.refresh(fresh)
    assert old.status == STATUS_PENDING
    assert fresh.status == STATUS_UNAVAILABLE
