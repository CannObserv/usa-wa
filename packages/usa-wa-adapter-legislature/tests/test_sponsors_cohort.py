"""Archive-first sponsor-roster cohort provider (#78 2b): re-parse sponsors:<biennium>
offline, live fallback only for an un-archived biennium; enumerate the archived span domain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from usa_wa_adapter_legislature.sponsor_cohort import SponsorRosterCohortProvider
from usa_wa_adapter_legislature.transport import WireFetch


class _FakeClient:
    def __init__(self, *, parsed=None, live=None):
        self._parsed = parsed or []
        self._live = live or []
        self.parse_calls = 0
        self.fetch_calls = 0

    async def parse_sponsors(self, wire):
        self.parse_calls += 1
        return self._parsed

    async def fetch_sponsors(self, biennium):
        self.fetch_calls += 1
        return WireFetch(records=self._live, wire=b"<live/>", content_type="text/xml")


def _member(mid, last="Rivers"):
    return {"Id": mid, "FirstName": "Ann", "LastName": last, "Agency": "Senate", "District": "5"}


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
    db_session.add(row)
    await db_session.flush()
    return row


async def _archive(db_session, source, biennium, wire=b"<roster/>", *, fetched_at=None):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=f"sponsors:{biennium}",
        url="https://x",
        fetched_at=fetched_at or datetime.now(UTC),
        http_status=200,
        content_hash=b"\x01" * 32,
        status=FetchStatus.ok,
    )
    db_session.add(ev)
    await db_session.flush()
    if wire is not None:  # wire=None models the runner's dedup skip (no RawPayload written)
        db_session.add(
            RawPayload(
                fetch_event_id=ev.id, content_type="text/xml", body=wire, size_bytes=len(wire)
            )
        )
        await db_session.flush()
    return ev


async def test_cohort_reads_archive_offline(db_session, usa_wa, wsl_source):
    await _archive(db_session, wsl_source, "2023-24")
    client = _FakeClient(parsed=[_member(100), _member(200, "Nguyen")])
    provider = SponsorRosterCohortProvider(client, session=db_session, source_id=wsl_source.id)

    cohort = await provider.cohort("2023-24")

    assert [m["Id"] for m in cohort] == [100, 200]
    assert client.parse_calls == 1  # archive re-parsed offline
    assert client.fetch_calls == 0  # no live pull


async def test_cohort_live_fallback_when_unarchived(db_session, usa_wa, wsl_source):
    client = _FakeClient(live=[_member(9)])
    provider = SponsorRosterCohortProvider(client, session=db_session, source_id=wsl_source.id)

    cohort = await provider.cohort("2099-00")  # nothing archived

    assert [m["Id"] for m in cohort] == [9]
    assert client.fetch_calls == 1 and client.parse_calls == 0


async def test_archived_bienniums_enumerates_span_domain(db_session, usa_wa, wsl_source):
    for b in ("2025-26", "2021-22", "2023-24"):
        await _archive(db_session, wsl_source, b)
    provider = SponsorRosterCohortProvider(
        _FakeClient(), session=db_session, source_id=wsl_source.id
    )
    assert await provider.archived_bienniums() == ["2021-22", "2023-24", "2025-26"]


async def test_roster_map_across_bienniums(db_session, usa_wa, wsl_source):
    await _archive(db_session, wsl_source, "2023-24")
    await _archive(db_session, wsl_source, "2025-26")
    client = _FakeClient(parsed=[_member(100)])
    provider = SponsorRosterCohortProvider(client, session=db_session, source_id=wsl_source.id)

    roster = await provider.roster_map(["2023-24", "2025-26"])

    assert set(roster) == {"2023-24", "2025-26"}
    assert roster["2023-24"][0]["Id"] == 100


async def test_fetch_event_map_cites_the_payload_bearing_event(db_session, usa_wa, wsl_source):
    """A forced daily re-pull re-records a payload-less FetchEvent (the dedup marker). The
    citation target must be the earlier event that actually stored bytes — otherwise a span
    first emitted on such a day would cite an event with no recoverable wire (CR round 6,
    symmetric with the committee provider)."""
    now = datetime.now(UTC)
    day1 = await _archive(db_session, wsl_source, "2025-26", fetched_at=now - timedelta(days=1))
    await _archive(db_session, wsl_source, "2025-26", wire=None, fetched_at=now)  # dedup marker
    provider = SponsorRosterCohortProvider(
        _FakeClient(), session=db_session, source_id=wsl_source.id
    )

    events = await provider.fetch_event_map(["2025-26"])

    assert events["2025-26"][0] == day1.id


async def test_no_session_always_pulls_live():
    client = _FakeClient(live=[_member(1)])
    provider = SponsorRosterCohortProvider(client)  # off-box: no session
    assert await provider.archived_bienniums() == []
    await provider.cohort("2025-26")
    assert client.fetch_calls == 1
