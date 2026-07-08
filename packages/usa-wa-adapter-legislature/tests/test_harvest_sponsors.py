"""Phase A sponsor harvest (#77): sweep GetSponsors, archive wire, materialize Persons only.

Persons + `wa_legislature_member_id` identifiers only — no party/seat Assignments (those
are Phase B spans, #78). Fill-only + dedup by stable WSL `Id` across biennia; one archive
per biennium under `sponsors:<biennium>`; closed biennia cache-hit on re-run.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from clearinghouse_core.provenance import FetchEvent, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier
from usa_wa_adapter_legislature.harvest_sponsors import harvest_sponsors
from usa_wa_adapter_legislature.transport import WireFetch


class _FakeSponsorClient:
    def __init__(self, by_biennium: dict[str, tuple[list[dict], bytes]]) -> None:
        self._by = by_biennium
        self.calls: list[str] = []

    async def fetch_sponsors(self, biennium: str) -> WireFetch:
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


def _member(mid, first, last, *, agency="Senate", district="5", party="D"):
    return {
        "Id": mid,
        "FirstName": first,
        "LastName": last,
        "District": district,
        "Party": party,
        "Agency": agency,
        "Name": f"{first} {last}",
    }


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar()


async def test_harvest_persons_only_dedups_by_id(db_session, usa_wa, wsl_source):
    # Rivers serves both biennia (one Person by stable Id); a newcomer appears in 2025-26.
    client = _FakeSponsorClient(
        {
            "2023-24": ([_member(100, "Ann", "Rivers")], b"<b23/>"),
            "2025-26": (
                [_member(100, "Ann", "Rivers"), _member(200, "Joe", "New", agency="House")],
                b"<b25/>",
            ),
        }
    )
    summary = await harvest_sponsors(
        db_session, bienniums=["2023-24", "2025-26"], sponsor_client=client
    )

    assert summary.windows == 2
    assert client.calls == ["2023-24", "2025-26"]
    # One archive per biennium.
    assert await _count(db_session, FetchEvent) == 2
    assert await _count(db_session, RawPayload) == 2
    # Two distinct Persons (Rivers deduped across biennia by Id) + their identifiers.
    assert await _count(db_session, Person) == 2
    assert await _count(db_session, PersonIdentifier) == 2
    # Persons-only: NO party/seat Assignments (those are Phase B spans, #78).
    assert await _count(db_session, Assignment) == 0
    rivers = (
        await db_session.execute(select(Person).where(Person.source_id == "100"))
    ).scalar_one()
    assert rivers.name_full == "Ann Rivers"


async def test_harvest_is_idempotent_cache_hit(db_session, usa_wa, wsl_source):
    client = _FakeSponsorClient({"2025-26": ([_member(100, "Ann", "Rivers")], b"<b25/>")})
    await harvest_sponsors(db_session, bienniums=["2025-26"], sponsor_client=client)
    # Second run within TTL is a cache hit — no second FetchEvent, no duplicate Person.
    await harvest_sponsors(db_session, bienniums=["2025-26"], sponsor_client=client)
    assert await _count(db_session, FetchEvent) == 1
    assert await _count(db_session, Person) == 1


async def test_harvest_skips_name_blanked_stubs(db_session, usa_wa, wsl_source):
    # A name-blanked departed-tenure stub (real Id, no name) is not a Person.
    stub = {"Id": 999, "Name": " ", "FirstName": None, "LastName": None, "Agency": "Senate"}
    client = _FakeSponsorClient({"2025-26": ([_member(100, "Ann", "Rivers"), stub], b"<b25/>")})
    await harvest_sponsors(db_session, bienniums=["2025-26"], sponsor_client=client)
    assert await _count(db_session, Person) == 1  # only the named member
