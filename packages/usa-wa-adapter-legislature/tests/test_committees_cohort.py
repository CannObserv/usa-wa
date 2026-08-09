"""Archive-first committee-roster cohort provider (sub-project 3, Phase B).

Turns a biennium into ``{source_id: LongName}`` by re-parsing the archived
committees-roster:<biennium> wire offline (the harvest already wrote it), falling
back to a live GetCommittees pull only for an un-archived biennium.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from usa_wa_adapter_legislature.committee_roster_cohort import CommitteeRosterCohortProvider
from usa_wa_adapter_legislature.transport import WireFetch


class _FakeClient:
    def __init__(self, *, parsed=None, live=None):
        self._parsed = parsed or []
        self._live = live or []
        self.parse_calls = 0
        self.fetch_calls = 0

    async def parse_committees(self, wire):
        self.parse_calls += 1
        return self._parsed

    async def fetch_committees(self, biennium):
        self.fetch_calls += 1
        return WireFetch(records=self._live, wire=b"<live/>", content_type="text/xml")


def _rec(cid, longname):
    return {"Id": cid, "LongName": longname}


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
    db_session.add(row)
    await db_session.flush()
    return row


async def _archive(db_session, source, biennium, wire=b"<roster/>"):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=f"committees-roster:{biennium}",
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
    client = _FakeClient(parsed=[_rec(1, "House A Committee"), _rec(2, "Senate B Committee")])
    provider = CommitteeRosterCohortProvider(client, session=db_session, source_id=wsl_source.id)

    cohort = await provider.cohort("2023-24")

    assert cohort == {"1": "House A Committee", "2": "Senate B Committee"}
    assert client.parse_calls == 1  # archive re-parsed offline
    assert client.fetch_calls == 0  # no live pull


async def test_cohort_live_fallback_when_unarchived(db_session, usa_wa, wsl_source):
    client = _FakeClient(live=[_rec(9, "Joint C Committee")])
    provider = CommitteeRosterCohortProvider(client, session=db_session, source_id=wsl_source.id)

    cohort = await provider.cohort("2099-00")  # never archived

    assert cohort == {"9": "Joint C Committee"}
    assert client.fetch_calls == 1
    assert client.parse_calls == 0


async def test_cohort_drops_blank_longname(db_session, usa_wa, wsl_source):
    await _archive(db_session, wsl_source, "2023-24")
    client = _FakeClient(parsed=[_rec(1, "Real Committee"), _rec(2, None), _rec(3, "")])
    provider = CommitteeRosterCohortProvider(client, session=db_session, source_id=wsl_source.id)
    cohort = await provider.cohort("2023-24")
    assert cohort == {"1": "Real Committee"}


async def test_archived_bienniums_enumerates_roster_keys(db_session, usa_wa, wsl_source):
    await _archive(db_session, wsl_source, "2021-22")
    await _archive(db_session, wsl_source, "2023-24")
    client = _FakeClient()
    provider = CommitteeRosterCohortProvider(client, session=db_session, source_id=wsl_source.id)
    assert await provider.archived_bienniums() == ["2021-22", "2023-24"]


async def test_provider_needs_session_for_archive(db_session, usa_wa, wsl_source):
    # no session → always live (e.g. an off-box dry preview)
    client = _FakeClient(live=[_rec(9, "X Committee")])
    provider = CommitteeRosterCohortProvider(client)
    await provider.cohort("2023-24")
    assert client.fetch_calls == 1
