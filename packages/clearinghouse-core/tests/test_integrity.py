"""Tests for the provenance integrity sweep (#54).

Re-hashes stored RawPayload bodies against the FetchEvent.content_hash baseline
and reports mismatches (at-rest tamper / corruption detection). NULL baselines
are "unbaselined" — counted separately, never a mismatch.
"""

import hashlib
from datetime import UTC, datetime

from ulid import ULID

from clearinghouse_core import integrity
from clearinghouse_core.integrity import SweepReport, main, sweep_payloads
from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source


async def _seed_source(db_session) -> Source:
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
        slug="fake_source",
        kind="http",
        reliability=1.0,
        cache_ttl_days=30,
    )
    db_session.add(source)
    await db_session.flush()
    return source


async def _seed_payload(db_session, source, *, body: bytes, content_hash: bytes | None) -> str:
    resource_id = str(ULID())
    fetch = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://example.test/x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=content_hash,
        status=FetchStatus.ok,
    )
    db_session.add(fetch)
    await db_session.flush()
    db_session.add(
        RawPayload(
            fetch_event_id=fetch.id,
            content_type="text/xml",
            body=body,
            size_bytes=len(body),
        )
    )
    await db_session.flush()
    return resource_id


async def test_sweep_reports_all_verified(db_session):
    """A payload whose body hashes to its baseline is verified, no mismatch."""
    source = await _seed_source(db_session)
    body = b"<soap:Envelope>ok</soap:Envelope>"
    await _seed_payload(db_session, source, body=body, content_hash=hashlib.sha256(body).digest())

    report = await sweep_payloads(db_session)

    assert report.scanned == 1
    assert report.verified == 1
    assert report.unbaselined == 0
    assert report.mismatched == 0
    assert report.mismatches == []
    assert report.ok is True


async def test_sweep_detects_mismatch(db_session):
    """A body that no longer hashes to its baseline is flagged (tamper/corruption)."""
    source = await _seed_source(db_session)
    resource_id = await _seed_payload(
        db_session, source, body=b"tampered", content_hash=hashlib.sha256(b"original").digest()
    )

    report = await sweep_payloads(db_session)

    assert report.scanned == 1
    assert report.verified == 0
    assert report.mismatched == 1
    assert report.ok is False
    assert report.mismatches[0]["resource_id"] == resource_id


async def test_sweep_skips_null_baseline_as_unbaselined(db_session):
    """A NULL content_hash is legacy/unbaselined — counted apart, never a mismatch."""
    source = await _seed_source(db_session)
    await _seed_payload(db_session, source, body=b"anything", content_hash=None)

    report = await sweep_payloads(db_session)

    assert report.scanned == 1
    assert report.verified == 0
    assert report.unbaselined == 1
    assert report.mismatched == 0
    assert report.ok is True  # unbaselined alone is not a failure


async def test_sweep_limit_marks_partial(db_session):
    """A --limit cap is surfaced (no silent truncation of coverage)."""
    source = await _seed_source(db_session)
    body = b"x"
    for _ in range(3):
        await _seed_payload(
            db_session, source, body=body, content_hash=hashlib.sha256(body).digest()
        )

    report = await sweep_payloads(db_session, limit=2)

    assert report.scanned == 2
    assert report.limited is True


def test_main_exit_zero_when_clean(monkeypatch, capsys):
    """A clean sweep prints the report and exits 0."""

    async def _fake_run(_args):
        return SweepReport(scanned=5, verified=5)

    monkeypatch.setattr(integrity, "_run", _fake_run)
    code = main([])
    assert code == 0
    assert '"mismatched": 0' in capsys.readouterr().out


def test_main_exit_one_on_mismatch(monkeypatch, capsys):
    """Any mismatch exits 1 — the non-zero the #49 OnFailure handler emails on."""

    async def _fake_run(_args):
        return SweepReport(
            scanned=2,
            verified=1,
            mismatched=1,
            mismatches=[{"resource_id": "X", "fetch_event_id": "Y"}],
        )

    monkeypatch.setattr(integrity, "_run", _fake_run)
    code = main([])
    assert code == 1
    assert '"resource_id": "X"' in capsys.readouterr().out
