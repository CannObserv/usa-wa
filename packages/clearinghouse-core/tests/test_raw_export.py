"""The RawPayload corpus export (#305): Postgres archive → raw-tier files.

db-marked: exercises the real provenance tables through the ``db_session``
fixture. The exporter's contracts: hashes verified against the FetchEvent
baseline before anything lands (a mismatch aborts — corruption is not laundered
into the new store), NULL baselines exported and counted as unbaselined,
payload-less FetchEvents (dedup shares) skipped, resumable by FetchEvent id.
"""

import hashlib
from datetime import UTC, datetime

import pytest

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.provenance import FetchEvent, RawPayload, Source
from clearinghouse_core.raw_export import ExportMismatch, export_corpus
from clearinghouse_core.rawstore import RawStore

pytestmark = pytest.mark.db


async def _seed_source(db_session, slug: str = "fake_source") -> Source:
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()
    jurisdiction = Jurisdiction(
        slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add(jurisdiction)
    await db_session.flush()
    source = Source(
        jurisdiction_id=jurisdiction.id,
        name="Fake Source",
        slug=slug,
        kind="http",
        reliability=1.0,
        cache_ttl_days=30,
    )
    db_session.add(source)
    await db_session.flush()
    return source


async def _seed_event(
    db_session,
    source: Source,
    resource_id: str,
    *,
    body: bytes | None,
    content_hash: bytes | str | None = "derive",
    fetched_at: datetime | None = None,
) -> FetchEvent:
    if content_hash == "derive" and body is not None:
        content_hash = hashlib.sha256(body).digest()
    event = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url=f"https://example/{resource_id}",
        fetched_at=fetched_at or datetime(2025, 6, 1, tzinfo=UTC),
        http_status=200,
        content_hash=content_hash if isinstance(content_hash, bytes) else None,
        status="ok",
    )
    db_session.add(event)
    await db_session.flush()
    if body is not None:
        db_session.add(
            RawPayload(
                fetch_event_id=event.id,
                content_type="text/xml",
                body=body,
                size_bytes=len(body),
            )
        )
        await db_session.flush()
    return event


async def test_exports_payloads_with_verified_hashes(db_session, tmp_path) -> None:
    source = await _seed_source(db_session)
    await _seed_event(db_session, source, "r1", body=b"body-one")
    await _seed_event(db_session, source, "r2", body=b"body-two", content_hash=None)
    await _seed_event(db_session, source, "r3", body=None)  # dedup share — no payload

    summary = await export_corpus(db_session, tmp_path)
    assert summary["exported"] == 2
    assert summary["unbaselined"] == 1

    store = RawStore(tmp_path, "fake_source")
    [manifest_path] = store.manifest_paths()
    import json

    manifest = json.loads(manifest_path.read_text())
    by_resource = {e["resource_id"]: e for e in manifest["entries"]}
    assert set(by_resource) == {"r1", "r2"}
    assert by_resource["r1"]["sha256"] == hashlib.sha256(b"body-one").hexdigest()
    assert by_resource["r1"]["fetched_at"].startswith("2025-06-01")
    assert by_resource["r2"].get("unbaselined") is True
    assert store.object_path(by_resource["r1"]["sha256"]).read_bytes() == b"body-one"


async def test_baseline_mismatch_aborts(db_session, tmp_path) -> None:
    source = await _seed_source(db_session)
    await _seed_event(
        db_session, source, "r1", body=b"actual", content_hash=hashlib.sha256(b"other").digest()
    )
    with pytest.raises(ExportMismatch):
        await export_corpus(db_session, tmp_path)


async def test_resumable_by_cursor(db_session, tmp_path) -> None:
    source = await _seed_source(db_session)
    await _seed_event(db_session, source, "r1", body=b"one")
    await _seed_event(db_session, source, "r2", body=b"two")

    first = await export_corpus(db_session, tmp_path, limit=1)
    assert first["exported"] == 1
    assert first["last_event_id"] is not None

    rest = await export_corpus(db_session, tmp_path, after_event_id=first["last_event_id"])
    assert rest["exported"] == 1

    again = await export_corpus(db_session, tmp_path, after_event_id=rest["last_event_id"])
    assert again["exported"] == 0
