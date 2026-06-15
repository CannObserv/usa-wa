"""/health/sync exposes the PM-sync outbox backlog for operators."""

from ulid import ULID

from clearinghouse_sync_powermap.models import (
    OP_CREATE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    OutboxEntry,
)


async def test_health_sync_reports_backlog(client, db_session):
    db_session.add_all(
        [
            OutboxEntry(entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_PENDING),
            OutboxEntry(entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_REJECTED),
            OutboxEntry(
                entity_type="fake", local_id=ULID(), op=OP_CREATE, status=STATUS_UNAVAILABLE
            ),
        ]
    )
    await db_session.flush()

    response = await client.get("/health/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["pending"] == 1
    assert body["rejected"] == 1
    assert body["unavailable"] == 1
    assert "pending_due" in body
    assert "oldest_pending_age_seconds" in body


async def test_health_sync_empty(client):
    response = await client.get("/health/sync")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "pending": 0,
        "pending_due": 0,
        "rejected": 0,
        "unavailable": 0,
        "oldest_pending_age_seconds": None,
    }
