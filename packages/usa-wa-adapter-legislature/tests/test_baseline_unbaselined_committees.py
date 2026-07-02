"""Retroactive baselining of the pre-#54 committee fetch events (#64).

The Jun 19–28 ``committees:2025-26`` daily pulls predate the #54 content-hash
baseline: NULL ``content_hash``. But — contrary to the original assumption — they
DID archive their bodies (each has a ``RawPayload``). So rather than delete them, we
backfill ``content_hash = sha256(RawPayload.body)`` — the exact #54 baseline the runner
now writes — converting them from "unbaselined" to "verified" while keeping the fetch
history and the bytes. This suite drives that: hash-and-set → idempotent no-op →
skip a payload-less NULL-hash event (nothing to hash) → don't touch baselined rows.
"""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from usa_wa_adapter_legislature import baseline_unbaselined_committees as bl

RESOURCE = "committees:2025-26"


async def _source(db_session, usa_wa):
    src = Source(
        jurisdiction_id=usa_wa.id, name="WA Legislature", slug="usa_wa_legislature", kind="soap"
    )
    db_session.add(src)
    await db_session.flush()
    return src


async def _event(
    db_session, src, *, content_hash, fetched_at, body=b"<wire/>", resource_id=RESOURCE
):
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
    if body is not None:
        db_session.add(
            RawPayload(
                fetch_event_id=ev.id, content_type="text/xml", body=body, size_bytes=len(body)
            )
        )
        await db_session.flush()
    return ev


async def test_baselines_null_hash_events_from_body(db_session, usa_wa):
    src = await _source(db_session, usa_wa)
    bodies = [b"<committees day=%d/>" % d for d in range(19, 25)]  # 6 distinct bodies
    events = [
        await _event(
            db_session,
            src,
            content_hash=None,
            fetched_at=datetime(2026, 6, d, tzinfo=UTC),
            body=bodies[i],
        )
        for i, d in enumerate(range(19, 25))
    ]

    result = await bl.baseline_unbaselined(db_session, resource_id=RESOURCE)

    assert result["baselined"] == 6
    assert result["skipped_no_payload"] == 0
    # each event now carries sha256 over its own body
    for ev, body in zip(events, bodies, strict=True):
        refreshed = (
            await db_session.execute(select(FetchEvent).where(FetchEvent.id == ev.id))
        ).scalar_one()
        assert refreshed.content_hash == hashlib.sha256(body).digest()


async def test_baseline_is_idempotent(db_session, usa_wa):
    src = await _source(db_session, usa_wa)
    await _event(db_session, src, content_hash=None, fetched_at=datetime(2026, 6, 19, tzinfo=UTC))
    first = await bl.baseline_unbaselined(db_session, resource_id=RESOURCE)
    assert first["baselined"] == 1
    second = await bl.baseline_unbaselined(db_session, resource_id=RESOURCE)
    assert second["baselined"] == 0
    assert second["status"] == "noop"


async def test_skips_null_hash_event_without_payload(db_session, usa_wa):
    """A NULL-hash event with no body can't be hashed — count it, don't fail."""
    src = await _source(db_session, usa_wa)
    await _event(
        db_session, src, content_hash=None, fetched_at=datetime(2026, 6, 19, tzinfo=UTC), body=None
    )
    result = await bl.baseline_unbaselined(db_session, resource_id=RESOURCE)
    assert result["baselined"] == 0
    assert result["skipped_no_payload"] == 1


async def test_leaves_baselined_events_untouched(db_session, usa_wa):
    src = await _source(db_session, usa_wa)
    existing = b"\x09" * 32
    ev = await _event(
        db_session, src, content_hash=existing, fetched_at=datetime(2026, 6, 30, tzinfo=UTC)
    )
    result = await bl.baseline_unbaselined(db_session, resource_id=RESOURCE)
    assert result["baselined"] == 0
    refreshed = (
        await db_session.execute(select(FetchEvent).where(FetchEvent.id == ev.id))
    ).scalar_one()
    assert refreshed.content_hash == existing  # unchanged
