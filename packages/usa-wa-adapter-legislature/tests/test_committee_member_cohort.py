"""Archive-first committee-member cohort provider (#82).

The provider's contract is "latest archived roster per (biennium, committee)". The subtlety
these tests pin is that **latest FetchEvent ≠ latest archived wire**: the runner re-records a
FetchEvent on every forced re-pull (TTL + content-hash ledger) but only stores a RawPayload
when the bytes changed (``_archive_payload`` → ``skip_unchanged``). The daily member fan-out
forces past the TTL, so from the second run onward the newest event for a stable roster is
**payload-less**. A provider that ordered on FetchEvent alone would read that roster as empty
and silently drop the current biennium out of every membership span.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.committee_member_cohort import CommitteeMemberCohortProvider

CURRENT = "2025-26"
CID = "31635"


class _WireEchoClient:
    """Parses a wire back into the member rows its bytes name (``b"<r:100,200/>"``)."""

    async def parse_historical_committee_members(self, wire: bytes) -> list[dict]:
        ids = wire.decode().removeprefix("<r:").removesuffix("/>")
        return [
            {"Id": int(i), "FirstName": "A", "LastName": "B", "Agency": "House"}
            for i in ids.split(",")
        ]


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(
        jurisdiction_id=usa_wa.id,
        name="WSL",
        slug="usa_wa_legislature",
        kind="soap",
        reliability=1.0,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _event(session, source, biennium, *, fetched_at, content_hash, body: bytes | None):
    """One FetchEvent; ``body=None`` models the runner's dedup skip (no RawPayload written)."""
    resource_id = committee_members_hist_resource_id(biennium, CID, "House", "Appropriations")
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=fetched_at,
        http_status=200,
        content_hash=content_hash,
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    if body is not None:
        session.add(
            RawPayload(
                fetch_event_id=ev.id, content_type="text/xml", body=body, size_bytes=len(body)
            )
        )
        await session.flush()
    return ev


def _provider(db_session, source):
    return CommitteeMemberCohortProvider(_WireEchoClient(), session=db_session, source_id=source.id)


async def test_payload_less_latest_event_does_not_hide_the_archived_roster(
    db_session, usa_wa, wsl_source
):
    """Day 1 archives the wire; day 2's forced re-pull is byte-identical, so the runner writes
    a NEW FetchEvent with NO RawPayload. The roster must still resolve to day 1's bytes —
    otherwise the current biennium reads as unobserved and its membership spans close."""
    now = datetime.now(UTC)
    await _event(
        db_session,
        wsl_source,
        CURRENT,
        fetched_at=now - timedelta(days=1),
        content_hash=b"\x01" * 32,
        body=b"<r:100,200/>",
    )
    await _event(
        db_session, wsl_source, CURRENT, fetched_at=now, content_hash=b"\x01" * 32, body=None
    )

    rosters = await _provider(db_session, wsl_source).archived_rosters()

    assert [r["Id"] for r in rosters[(CURRENT, CID)]] == [100, 200]


async def test_fetch_event_map_targets_the_payload_bearing_event(db_session, usa_wa, wsl_source):
    """The citation target is the pull that actually delivered the archived bytes."""
    now = datetime.now(UTC)
    day1 = await _event(
        db_session,
        wsl_source,
        CURRENT,
        fetched_at=now - timedelta(days=1),
        content_hash=b"\x01" * 32,
        body=b"<r:100/>",
    )
    await _event(
        db_session, wsl_source, CURRENT, fetched_at=now, content_hash=b"\x01" * 32, body=None
    )

    events = await _provider(db_session, wsl_source).fetch_event_map()

    assert events[(CURRENT, CID)][0] == day1.id


async def test_changed_wire_supersedes_the_older_archived_roster(db_session, usa_wa, wsl_source):
    """When the roster genuinely changes, the newer payload-bearing event wins."""
    now = datetime.now(UTC)
    await _event(
        db_session,
        wsl_source,
        CURRENT,
        fetched_at=now - timedelta(days=1),
        content_hash=b"\x01" * 32,
        body=b"<r:100/>",
    )
    await _event(
        db_session,
        wsl_source,
        CURRENT,
        fetched_at=now,
        content_hash=b"\x02" * 32,
        body=b"<r:100,300/>",
    )

    rosters = await _provider(db_session, wsl_source).archived_rosters()

    assert [r["Id"] for r in rosters[(CURRENT, CID)]] == [100, 300]


async def test_same_timestamp_events_break_the_tie_on_id(db_session, usa_wa, wsl_source):
    """Identical ``fetched_at`` must not resolve nondeterministically — the ULID id (monotonic)
    is the secondary key, so the later-inserted payload wins."""
    at = datetime.now(UTC)
    await _event(
        db_session, wsl_source, CURRENT, fetched_at=at, content_hash=b"\x01" * 32, body=b"<r:100/>"
    )
    await _event(
        db_session, wsl_source, CURRENT, fetched_at=at, content_hash=b"\x02" * 32, body=b"<r:400/>"
    )

    rosters = await _provider(db_session, wsl_source).archived_rosters()

    assert [r["Id"] for r in rosters[(CURRENT, CID)]] == [400]


async def test_empty_wire_archive_yields_an_empty_roster(db_session, usa_wa, wsl_source):
    """The swallowed "no roster that biennium" Fault archives ``b""`` — a real RawPayload that
    contributes no members but still anchors provenance."""
    await _event(
        db_session,
        wsl_source,
        CURRENT,
        fetched_at=datetime.now(UTC),
        content_hash=b"\x00" * 32,
        body=b"",
    )

    provider = _provider(db_session, wsl_source)

    assert await provider.archived_rosters() == {(CURRENT, CID): []}
    assert (CURRENT, CID) in await provider.fetch_event_map()


async def test_event_with_no_payload_at_all_is_not_a_roster(db_session, usa_wa, wsl_source):
    """A resource whose only event never stored bytes contributes nothing (no phantom key)."""
    await _event(
        db_session,
        wsl_source,
        CURRENT,
        fetched_at=datetime.now(UTC),
        content_hash=b"\x01" * 32,
        body=None,
    )

    provider = _provider(db_session, wsl_source)

    assert await provider.archived_rosters() == {}
    assert await provider.fetch_event_map() == {}


async def test_latest_events_is_memoized_across_both_reads(db_session, usa_wa, wsl_source):
    """``archived_rosters`` + ``fetch_event_map`` are both called per build — the underlying
    ``fetch_events`` scan must run once. Counted via emitted SQL rather than a monkeypatch, so
    the test doesn't couple to a private method name."""
    await _event(
        db_session,
        wsl_source,
        CURRENT,
        fetched_at=datetime.now(UTC),
        content_hash=b"\x01" * 32,
        body=b"<r:100/>",
    )
    provider = _provider(db_session, wsl_source)

    scans = 0

    def _count(conn, cursor, statement, parameters, context, executemany):
        nonlocal scans
        if "fetch_events" in statement.lower():
            scans += 1

    sync_engine = db_session.get_bind().engine
    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        await provider.archived_rosters()
        await provider.fetch_event_map()
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    assert scans == 1  # the second accessor reads the memoized result
