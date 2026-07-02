"""Provenance cleanup of the pre-#54 unbaselined committee fetch events (#64).

The Jun 19–28 ``committees:2025-26`` daily pulls predate the #54 content-hash
baseline: NULL ``content_hash``, no ``RawPayload`` body. They're superseded by the
Jun 30+ archived+hashed pulls. Deleting them trips ``citations.fetch_event_id``'s
``ondelete=RESTRICT``, so their citations must first re-point to a surviving
baselined fetch event. This suite drives that: re-point → delete → survivor intact →
idempotent no-op, plus the defensive abort when a "target" unexpectedly carries bytes.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from ulid import ULID

from clearinghouse_core.provenance import (
    Citation,
    FetchEvent,
    FetchStatus,
    RawPayload,
    Source,
)
from usa_wa_adapter_legislature import cleanup_unbaselined_committees as cu

RESOURCE = "committees:2025-26"


async def _source(db_session, usa_wa):
    src = Source(
        jurisdiction_id=usa_wa.id,
        name="WA Legislature",
        slug="usa_wa_legislature",
        kind="soap",
    )
    db_session.add(src)
    await db_session.flush()
    return src


async def _fetch_event(db_session, src, *, content_hash, fetched_at, resource_id=RESOURCE):
    ev = FetchEvent(
        source_id=src.id,
        resource_id=resource_id,
        url="https://wsl/soap",
        fetched_at=fetched_at,
        http_status=200,
        content_hash=content_hash,
        status=FetchStatus.ok,
    )
    db_session.add(ev)
    await db_session.flush()
    return ev


async def _citation(db_session, event):
    c = Citation(
        entity_type="organization",
        entity_id=ULID(),
        fetch_event_id=event.id,
        asserted_at=event.fetched_at,
    )
    db_session.add(c)
    await db_session.flush()
    return c


async def _seed_prod_shape(db_session, usa_wa):
    """6 unbaselined (NULL-hash, payload-less) events each with a citation, plus a
    newest baselined survivor carrying a RawPayload — the real production shape."""
    src = await _source(db_session, usa_wa)
    targets = []
    for day in range(19, 25):  # Jun 19..24 → 6 events
        ev = await _fetch_event(
            db_session, src, content_hash=None, fetched_at=datetime(2026, 6, day, tzinfo=UTC)
        )
        await _citation(db_session, ev)
        targets.append(ev)
    survivor = await _fetch_event(
        db_session, src, content_hash=b"\x01" * 32, fetched_at=datetime(2026, 6, 30, tzinfo=UTC)
    )
    db_session.add(
        RawPayload(
            fetch_event_id=survivor.id, content_type="text/xml", body=b"<wire/>", size_bytes=7
        )
    )
    await db_session.flush()
    return src, targets, survivor


async def test_cleanup_repoints_and_deletes(db_session, usa_wa):
    src, targets, survivor = await _seed_prod_shape(db_session, usa_wa)
    target_ids = [t.id for t in targets]

    result = await cu.cleanup_unbaselined(db_session, resource_id=RESOURCE)

    assert result["deleted"] == 6
    assert result["repointed"] == 6
    assert result["survivor"] == str(survivor.id)
    # the 6 events are gone
    remaining = (
        await db_session.execute(
            select(func.count()).select_from(FetchEvent).where(FetchEvent.id.in_(target_ids))
        )
    ).scalar_one()
    assert remaining == 0
    # every citation now points at the survivor
    stray = (
        await db_session.execute(
            select(func.count())
            .select_from(Citation)
            .where(Citation.fetch_event_id.in_(target_ids))
        )
    ).scalar_one()
    assert stray == 0
    on_survivor = (
        await db_session.execute(
            select(func.count()).select_from(Citation).where(Citation.fetch_event_id == survivor.id)
        )
    ).scalar_one()
    assert on_survivor == 6
    # survivor + its payload untouched
    survivor_payloads = (
        await db_session.execute(
            select(func.count())
            .select_from(RawPayload)
            .where(RawPayload.fetch_event_id == survivor.id)
        )
    ).scalar_one()
    assert survivor_payloads == 1


async def test_cleanup_is_idempotent(db_session, usa_wa):
    await _seed_prod_shape(db_session, usa_wa)
    first = await cu.cleanup_unbaselined(db_session, resource_id=RESOURCE)
    assert first["deleted"] == 6
    second = await cu.cleanup_unbaselined(db_session, resource_id=RESOURCE)
    assert second["deleted"] == 0
    assert second["repointed"] == 0
    assert second["status"] == "noop"


async def test_cleanup_aborts_without_survivor(db_session, usa_wa):
    """No baselined event for the resource → refuse to orphan the citations."""
    src = await _source(db_session, usa_wa)
    ev = await _fetch_event(
        db_session, src, content_hash=None, fetched_at=datetime(2026, 6, 19, tzinfo=UTC)
    )
    await _citation(db_session, ev)
    result = await cu.cleanup_unbaselined(db_session, resource_id=RESOURCE)
    assert result["aborted"] == "no_survivor"
    assert result["deleted"] == 0


async def test_cleanup_aborts_if_target_has_payload(db_session, usa_wa):
    """A NULL-hash event that somehow carries bytes is a contradiction — never delete
    archived payload. Abort rather than cascade it away."""
    src = await _source(db_session, usa_wa)
    ev = await _fetch_event(
        db_session, src, content_hash=None, fetched_at=datetime(2026, 6, 19, tzinfo=UTC)
    )
    db_session.add(
        RawPayload(fetch_event_id=ev.id, content_type="text/xml", body=b"<x/>", size_bytes=4)
    )
    # a survivor exists, so only the payload guard can trip
    await _fetch_event(
        db_session, src, content_hash=b"\x02" * 32, fetched_at=datetime(2026, 6, 30, tzinfo=UTC)
    )
    await db_session.flush()
    result = await cu.cleanup_unbaselined(db_session, resource_id=RESOURCE)
    assert result["aborted"] == "target_has_payload"
    assert result["deleted"] == 0
