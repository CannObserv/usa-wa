"""Phase A harvest: sweep GetCommittees rosters, archive wire, materialize by Id.

Fill-only (#65) so a re-observed committee is never clobbered; one archive per
biennium under committees-roster:<biennium>; an inter-request pause between windows.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from clearinghouse_core.provenance import FetchEvent, RawPayload, Source
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.harvest_committees import (
    bienniums_in_range,
    harvest_committees,
)
from usa_wa_adapter_legislature.transport import WireFetch


class _FakeCommitteeClient:
    def __init__(self, by_biennium: dict[str, tuple[list[dict], bytes]]) -> None:
        self._by = by_biennium
        self.calls: list[str] = []

    async def fetch_committees(self, biennium: str) -> WireFetch:
        self.calls.append(biennium)
        records, wire = self._by.get(biennium, ([], b""))
        return WireFetch(records=records, wire=wire, content_type="text/xml")


@pytest.fixture
async def wsl_source(db_session, usa_wa) -> Source:
    row = Source(
        jurisdiction_id=usa_wa.id,
        name="WSL",
        slug="usa_wa_legislature",
        kind="soap",
        base_url="https://x",
        reliability=1.0,
        cache_ttl_days=1,
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _committee(cid, longname, agency="House", acronym="X"):
    return {
        "Id": cid,
        "Name": longname,
        "LongName": longname,
        "Agency": agency,
        "Acronym": acronym,
        "Phone": None,
    }


def test_bienniums_in_range_walks_by_two():
    assert bienniums_in_range("2021-22", "2025-26") == ["2021-22", "2023-24", "2025-26"]


async def test_harvest_archives_each_biennium_and_materializes(db_session, usa_wa, wsl_source):
    client = _FakeCommitteeClient(
        {
            "2023-24": ([_committee(31635, "House Capital Budget Committee")], b"<b23/>"),
            "2025-26": ([_committee(31635, "House Capital Budget Committee")], b"<b25/>"),
        }
    )
    pauses: list[float] = []

    async def _fake_sleep(seconds):
        pauses.append(seconds)

    summary = await harvest_committees(
        db_session,
        bienniums=["2023-24", "2025-26"],
        committee_client=client,
        pause_seconds=1.5,
        sleep=_fake_sleep,
    )

    assert client.calls == ["2023-24", "2025-26"]
    assert summary.windows == 2
    # one archive per biennium, under the roster resource id
    resource_ids = {
        e.resource_id for e in (await db_session.execute(select(FetchEvent))).scalars().all()
    }
    assert resource_ids == {"committees-roster:2023-24", "committees-roster:2025-26"}
    # the committee is materialized once by stable Id (present in both bienniums)
    committees = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "committee")))
        .scalars()
        .all()
    )
    assert {c.source_id for c in committees} == {"31635"}
    # paused once (between the two windows, not after the last)
    assert pauses == [1.5]


async def test_harvest_is_fill_only_never_clobbers(db_session, usa_wa, wsl_source):
    # An existing committee with a PM-curated name (as the mirror would have set it).
    existing = Organization(
        source="usa_wa_legislature",
        source_id="31635",
        name="PM Curated Name",
        org_type="committee",
        jurisdiction_id=usa_wa.id,
    )
    db_session.add(existing)
    await db_session.flush()

    client = _FakeCommitteeClient({"2023-24": ([_committee(31635, "WSL Produced Name")], b"<b/>")})
    await harvest_committees(db_session, bienniums=["2023-24"], committee_client=client)

    refreshed = (
        await db_session.execute(select(Organization).where(Organization.source_id == "31635"))
    ).scalar_one()
    assert refreshed.name == "PM Curated Name"  # fill-only: not clobbered by the roster


async def test_harvest_re_run_is_cache_hit(db_session, usa_wa, wsl_source):
    client = _FakeCommitteeClient({"2023-24": ([_committee(31635, "X")], b"<b/>")})
    await harvest_committees(db_session, bienniums=["2023-24"], committee_client=client)
    await harvest_committees(db_session, bienniums=["2023-24"], committee_client=client)

    # second run inside TTL is a cache hit → only one fetch, one archived payload
    assert client.calls == ["2023-24"]
    payloads = (await db_session.execute(select(func.count()).select_from(RawPayload))).scalar_one()
    assert payloads == 1


async def test_harvest_force_re_materializes_despite_fresh_cache(db_session, usa_wa, wsl_source):
    """``force=True`` re-fetches + re-materializes even on a fresh cache hit — the
    re-materialization path after the incident rollback (the org rows were rolled back
    but the roster stayed archived, so a plain cache-hit re-run inserts nothing).
    The byte-identical wire still dedups to one RawPayload (revalidation, not re-store)."""
    client = _FakeCommitteeClient({"2023-24": ([_committee(31635, "X")], b"<b/>")})
    await harvest_committees(db_session, bienniums=["2023-24"], committee_client=client)

    # Simulate the incident rollback: roster stays archived, the org row is gone.
    org = (
        await db_session.execute(select(Organization).where(Organization.source_id == "31635"))
    ).scalar_one()
    await db_session.delete(org)
    await db_session.flush()

    async def _count() -> int:
        return (
            await db_session.execute(
                select(func.count())
                .select_from(Organization)
                .where(Organization.source_id == "31635")
            )
        ).scalar_one()

    # A plain re-run is a cache hit → re-materializes nothing (the plan's wrong premise).
    cache_run = await harvest_committees(db_session, bienniums=["2023-24"], committee_client=client)
    assert cache_run.upserted == 0
    assert await _count() == 0

    # force=True re-fetches and re-inserts the rolled-back row despite the fresh cache.
    forced = await harvest_committees(
        db_session, bienniums=["2023-24"], committee_client=client, force=True
    )
    assert forced.upserted == 1
    assert await _count() == 1

    # Re-fetched on the forced run only (cache-hit run didn't touch WSL); byte-identical
    # wire → still one archived payload (dedup guard).
    assert client.calls == ["2023-24", "2023-24"]
    payloads = (await db_session.execute(select(func.count()).select_from(RawPayload))).scalar_one()
    assert payloads == 1
