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
import json
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import select

from clearinghouse_core import job as job_module
from clearinghouse_core.config import DATABASE_ROLE_OWNER
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_core.testing import patch_job_runtime
from usa_wa_adapter_legislature.committees import migrate_fetch_baseline as bl

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


# --- CLI (#179b: the shared job harness, owner role) ---------------------------


def test_main_declares_the_owner_role(monkeypatch):
    """The app role is REVOKEd UPDATE on the provenance ledger (#54), so this is a
    declaration to ``run_job()`` now rather than a private ``_owner_url()`` helper."""
    seen: dict = {}

    def _capture(_name, _handler, **kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(bl, "run_job", _capture)
    bl.main([])
    assert seen["role"] == DATABASE_ROLE_OWNER


def test_main_missing_owner_url_is_exit_two(monkeypatch, capsys):
    """Was a bare RuntimeError traceback (exit 1); is now the harness's config exit 2,
    matching every other owner-role CLI. Documented in COMMANDS-BACKFILL.md."""

    def _raise(_role="app"):
        raise RuntimeError("DATABASE_URL_OWNER is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    assert bl.main([]) == 2
    assert "DATABASE_URL_OWNER" in capsys.readouterr().err


def test_main_dry_run_rolls_back(monkeypatch, capsys):
    recording = patch_job_runtime(monkeypatch)

    async def _fake(_session, **_kwargs):
        return {"status": "baselined", "baselined": 3, "skipped_no_payload": 0}

    with patch.object(bl, "baseline_unbaselined", _fake):
        code = bl.main(["--dry-run", "--json"])

    assert code == 0
    assert (recording.committed, recording.rolled_back) == (0, 1)
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["counters"]["baselined"] == 3
