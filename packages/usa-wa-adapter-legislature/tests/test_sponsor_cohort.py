"""Archive-first sponsor-roster cohort provider (#78 2b): re-parse sponsors:<biennium>
offline, live fallback only for an un-archived biennium; enumerate the archived span domain.
"""

from __future__ import annotations

from datetime import UTC, datetime

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


async def _archive(db_session, source, biennium, wire=b"<roster/>"):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=f"sponsors:{biennium}",
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x01" * 32,
        status=FetchStatus.ok,
    )
    db_session.add(ev)
    await db_session.flush()
    db_session.add(
        RawPayload(fetch_event_id=ev.id, content_type="text/xml", body=wire, size_bytes=len(wire))
    )
    await db_session.flush()


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


async def test_no_session_always_pulls_live():
    client = _FakeClient(live=[_member(1)])
    provider = SponsorRosterCohortProvider(client)  # off-box: no session
    assert await provider.archived_bienniums() == []
    await provider.cohort("2025-26")
    assert client.fetch_calls == 1
