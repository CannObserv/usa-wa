"""Tests for the provenance integrity sweep (#54).

Re-hashes stored RawPayload bodies against the FetchEvent.content_hash baseline
and reports mismatches (at-rest tamper / corruption detection). NULL baselines
are "unbaselined" — counted separately, never a mismatch.
"""

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from ulid import ULID

from clearinghouse_core import integrity
from clearinghouse_core.integrity import (
    SweepReport,
    load_cursor,
    main,
    rolling_sweep,
    sweep_payloads,
)
from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_core.sweep_state import IntegritySweepState


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
    _, resource_id = await _seed_payload_row(
        db_session, source, body=body, content_hash=content_hash
    )
    return resource_id


async def _seed_payload_row(
    db_session, source, *, body: bytes, content_hash: bytes | None
) -> tuple[RawPayload, str]:
    """Seed a FetchEvent + RawPayload; return the payload row (for its id) and resource_id."""
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
    payload = RawPayload(
        fetch_event_id=fetch.id,
        content_type="text/xml",
        body=body,
        size_bytes=len(body),
    )
    db_session.add(payload)
    await db_session.flush()
    return payload, resource_id


async def _seed_sized(db_session, source, size: int) -> RawPayload:
    """Seed a verified payload of exactly ``size`` bytes; return its row."""
    body = b"x" * size
    payload, _ = await _seed_payload_row(
        db_session, source, body=body, content_hash=hashlib.sha256(body).digest()
    )
    return payload


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


# --- rolling since-cursor (#55) ---------------------------------------------


async def test_sweep_after_id_skips_earlier_rows(db_session):
    """after_id resumes past an earlier cursor — only newer payload ids are scanned."""
    source = await _seed_source(db_session)
    await _seed_sized(db_session, source, 10)
    second = await _seed_sized(db_session, source, 10)
    third = await _seed_sized(db_session, source, 10)

    report = await sweep_payloads(db_session, after_id=str(second.id))

    assert report.scanned == 1  # only the third row (> second.id)
    assert report.last_id == str(third.id)
    assert report.reached_end is True  # stream exhausted, no budget cap


async def test_sweep_byte_budget_bounds_slice(db_session):
    """A byte budget stops the slice after the row that crosses it — bounded per run."""
    source = await _seed_source(db_session)
    first = await _seed_sized(db_session, source, 100)
    await _seed_sized(db_session, source, 100)
    await _seed_sized(db_session, source, 100)

    report = await sweep_payloads(db_session, byte_budget=100)

    assert report.scanned == 1  # first row alone reaches the 100-byte budget
    assert report.last_id == str(first.id)
    assert report.reached_end is False  # more rows remain beyond the budget


async def test_sweep_oversized_payload_still_progresses(db_session):
    """A single payload larger than the whole budget is still scanned (no stall)."""
    source = await _seed_source(db_session)
    big = await _seed_sized(db_session, source, 500)

    report = await sweep_payloads(db_session, byte_budget=100)

    assert report.scanned == 1
    assert report.last_id == str(big.id)
    assert report.reached_end is True  # it was the only row


async def test_load_cursor_absent_is_none(db_session):
    """No state row yet → cursor is None (a full pass from the beginning)."""
    assert await load_cursor(db_session) is None


async def test_rolling_sweep_resumes_and_wraps(db_session):
    """Successive rolling runs advance the cursor, then wrap + flag cycle-complete."""
    source = await _seed_source(db_session)
    rows = [await _seed_sized(db_session, source, 100) for _ in range(3)]

    # One row per run (each row alone hits the 100-byte budget).
    r1 = await rolling_sweep(db_session, byte_budget=100)
    assert r1.scanned == 1 and r1.coverage_cycle_complete is False
    assert await load_cursor(db_session) == rows[0].id  # ULID watermark, same type as id

    r2 = await rolling_sweep(db_session, byte_budget=100)
    assert r2.scanned == 1
    assert await load_cursor(db_session) == rows[1].id

    r3 = await rolling_sweep(db_session, byte_budget=100)
    assert r3.scanned == 1
    assert r3.reached_end is True
    assert r3.coverage_cycle_complete is True
    # Wrapped: cursor reset so the next run re-verifies from the beginning.
    assert await load_cursor(db_session) is None

    # A single persisted state row, not one per run.
    count = len((await db_session.execute(select(IntegritySweepState))).scalars().all())
    assert count == 1


async def test_rolling_sweep_full_corpus_under_budget_is_one_cycle(db_session):
    """When the whole corpus fits the budget, one run completes a coverage cycle."""
    source = await _seed_source(db_session)
    await _seed_sized(db_session, source, 10)
    await _seed_sized(db_session, source, 10)

    report = await rolling_sweep(db_session, byte_budget=10_000)

    assert report.scanned == 2
    assert report.reached_end is True
    assert report.coverage_cycle_complete is True
    assert await load_cursor(db_session) is None


async def test_rolling_sweep_wraps_when_cursor_past_tail(db_session):
    """A stale cursor at/after the tail wraps and re-scans from the start (no dead run)."""
    source = await _seed_source(db_session)
    first = await _seed_sized(db_session, source, 10)
    # Cursor parked past every row (GC could drop rows below it).
    db_session.add(IntegritySweepState(scope="raw_payload", cursor=str(ULID())))
    await db_session.flush()

    report = await rolling_sweep(db_session, byte_budget=10_000)

    assert report.scanned == 1  # re-scanned from the beginning, not a dead 0-row run
    assert report.last_id == str(first.id)
    assert report.coverage_cycle_complete is True
    assert await load_cursor(db_session) is None


async def test_rolling_sweep_detects_mismatch(db_session):
    """A rolling run still flags a tampered body (exit-1 path preserved)."""
    source = await _seed_source(db_session)
    await _seed_payload_row(
        db_session, source, body=b"tampered", content_hash=hashlib.sha256(b"orig").digest()
    )

    report = await rolling_sweep(db_session, byte_budget=10_000)

    assert report.mismatched == 1
    assert report.ok is False


# --- CLI mode dispatch (#55) -------------------------------------------------


def _patch_session_factory(monkeypatch, session):
    """Stub get_session_factory so _run's `async with factory() as session` yields
    ``session`` — lets us assert dispatch without a real engine."""

    @asynccontextmanager
    async def _cm():
        yield session

    monkeypatch.setattr(integrity, "get_session_factory", lambda: _cm)


async def test_run_dispatches_full_and_flags_cycle(monkeypatch):
    """--full runs one whole-corpus pass (no cursor/budget) and flags the cycle."""
    calls: dict = {}

    async def fake_sweep(session, **kw):
        calls["sweep"] = (session, kw)
        return SweepReport(scanned=2, verified=2, reached_end=True)

    async def fake_rolling(session, **kw):
        calls["rolling"] = (session, kw)
        return SweepReport()

    sentinel = object()
    _patch_session_factory(monkeypatch, sentinel)
    monkeypatch.setattr(integrity, "sweep_payloads", fake_sweep)
    monkeypatch.setattr(integrity, "rolling_sweep", fake_rolling)

    report = await integrity._run(integrity._build_parser().parse_args(["--full"]))

    assert "rolling" not in calls
    assert calls["sweep"] == (sentinel, {})  # whole corpus: no limit, no budget
    assert report.coverage_cycle_complete is True  # mirrors reached_end for --full


async def test_run_dispatches_limit(monkeypatch):
    """--limit N routes to a row-capped partial sweep, not the rolling path."""
    calls: dict = {}

    async def fake_sweep(session, **kw):
        calls["sweep"] = (session, kw)
        return SweepReport(limited=True)

    async def fake_rolling(session, **kw):
        calls["rolling"] = True
        return SweepReport()

    sentinel = object()
    _patch_session_factory(monkeypatch, sentinel)
    monkeypatch.setattr(integrity, "sweep_payloads", fake_sweep)
    monkeypatch.setattr(integrity, "rolling_sweep", fake_rolling)

    await integrity._run(integrity._build_parser().parse_args(["--limit", "5"]))

    assert "rolling" not in calls
    assert calls["sweep"] == (sentinel, {"limit": 5})


async def test_run_dispatches_rolling_by_default(monkeypatch):
    """A bare invocation routes to the rolling sweep with the default byte budget."""
    calls: dict = {}

    async def fake_sweep(session, **kw):
        calls["sweep"] = True
        return SweepReport()

    async def fake_rolling(session, **kw):
        calls["rolling"] = (session, kw)
        return SweepReport(coverage_cycle_complete=True)

    sentinel = object()
    _patch_session_factory(monkeypatch, sentinel)
    monkeypatch.setattr(integrity, "sweep_payloads", fake_sweep)
    monkeypatch.setattr(integrity, "rolling_sweep", fake_rolling)

    await integrity._run(integrity._build_parser().parse_args([]))

    assert "sweep" not in calls
    assert calls["rolling"] == (sentinel, {"byte_budget": integrity.DEFAULT_BYTE_BUDGET})
