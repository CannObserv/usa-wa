"""Phase A committee-membership harvest (#82): fan GetCommitteeMembers over archived rosters.

Enumerates each biennium's House/Senate standing committees from the roster archive, pulls
one historical roster per committee, archives the wire (#54), and materializes Persons only —
membership is a Phase B span. Joint/`Other` committees are skipped (no membership op).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from clearinghouse_core.provenance import FetchEvent, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person
from usa_wa_adapter_legislature.harvest_committee_members import (
    harvest_committee_members,
    standing_committees,
)
from usa_wa_adapter_legislature.transport import WireFetch


def _member(mid, first="Timm", last="Ormsby", agency="House"):
    return {
        "Id": mid,
        "FirstName": first,
        "LastName": last,
        "Name": f"{first} {last}",
        "Agency": agency,
        "Party": "Democrat",
        "District": "3",
    }


class _FakeCommitteeClient:
    """No roster archive → the provider falls back to a live GetCommittees pull."""

    def __init__(self, records_by_biennium):
        self._records = records_by_biennium

    async def fetch_committees(self, biennium):
        return WireFetch(
            records=self._records.get(biennium, []), wire=b"<c/>", content_type="text/xml"
        )

    async def parse_committees(self, wire):  # pragma: no cover - archive path unused here
        raise AssertionError("no roster archive in this test")


class _FakeMemberClient:
    def __init__(self, roster_by_key, *, missing=()):
        self._roster = roster_by_key  # {(biennium, agency, name): [rows]}
        self.calls = []
        self._missing = set(missing)

    async def fetch_historical_committee_members(self, biennium, agency, name):
        self.calls.append((biennium, agency, name))
        if (biennium, agency, name) in self._missing:
            # the benign "no roster that biennium" Fault, swallowed in the transport
            return WireFetch(records=[], wire=b"", content_type="text/xml")
        rows = self._roster.get((biennium, agency, name), [])
        return WireFetch(records=rows, wire=b"<m/>", content_type="text/xml")


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(
        jurisdiction_id=usa_wa.id,
        name="WSL",
        slug="usa_wa_legislature",
        kind="soap",
        reliability=1.0,
        cache_ttl_days=1,
    )
    db_session.add(row)
    await db_session.flush()
    return row


def test_standing_committees_keeps_chamber_committees_only():
    records = [
        {"Id": 1, "Agency": "House", "Name": "Appropriations"},
        {"Id": 2, "Agency": "Joint", "Name": "JTC"},  # no membership op (#39)
        {"Id": 3, "Agency": "Senate", "Name": "Ways & Means"},
        {"Id": 4, "Agency": "House", "Name": "   "},  # unusable name
        {"Id": None, "Agency": "House", "Name": "Rules"},  # no id
    ]
    assert standing_committees(records) == [
        ("1", "House", "Appropriations"),
        ("3", "Senate", "Ways & Means"),
    ]


async def test_harvest_archives_one_roster_per_committee_and_materializes_persons(
    db_session, usa_wa, wsl_source
):
    committee_client = _FakeCommitteeClient(
        {
            "2023-24": [
                {"Id": 31635, "Agency": "House", "Name": "Appropriations"},
                {"Id": -140, "Agency": "Joint", "Name": "JTC"},  # skipped
            ]
        }
    )
    member_client = _FakeMemberClient(
        {("2023-24", "House", "Appropriations"): [_member(100), _member(200, last="Reeves")]}
    )

    summary = await harvest_committee_members(
        db_session,
        bienniums=["2023-24"],
        committee_client=committee_client,
        member_client=member_client,
    )

    # only the House committee was fanned (Joint has no membership op)
    assert member_client.calls == [("2023-24", "House", "Appropriations")]
    assert summary.rosters_pulled == 1
    # the wire is archived under the historical resource key (#54)
    [event] = (await db_session.execute(select(FetchEvent))).scalars().all()
    assert event.resource_id == "committee-members-hist:2023-24:31635:House:Appropriations"
    assert (
        await db_session.execute(select(func.count()).select_from(RawPayload))
    ).scalar_one() == 1
    # Persons materialized; membership is a Phase B span, so ZERO Assignments here
    persons = {p.source_id for p in (await db_session.execute(select(Person))).scalars().all()}
    assert persons == {"100", "200"}
    assert (
        await db_session.execute(select(func.count()).select_from(Assignment))
    ).scalar_one() == 0


async def test_harvest_skips_biennium_with_no_committees(db_session, usa_wa, wsl_source):
    committee_client = _FakeCommitteeClient({"1995-96": []})
    member_client = _FakeMemberClient({})

    summary = await harvest_committee_members(
        db_session,
        bienniums=["1995-96"],
        committee_client=committee_client,
        member_client=member_client,
    )

    assert member_client.calls == []
    assert summary.rosters_pulled == 0


async def test_harvest_tolerates_a_committee_absent_that_biennium(db_session, usa_wa, wsl_source):
    """A sub-floor / not-yet-existing committee faults → empty roster, sweep continues."""
    committee_client = _FakeCommitteeClient(
        {
            "1999-00": [
                {"Id": 1, "Agency": "House", "Name": "Gone"},
                {"Id": 2, "Agency": "House", "Name": "Appropriations"},
            ]
        }
    )
    member_client = _FakeMemberClient(
        {("1999-00", "House", "Appropriations"): [_member(100)]},
        missing=[("1999-00", "House", "Gone")],
    )

    summary = await harvest_committee_members(
        db_session,
        bienniums=["1999-00"],
        committee_client=committee_client,
        member_client=member_client,
    )

    assert summary.rosters_pulled == 2  # both attempted
    assert {p.source_id for p in (await db_session.execute(select(Person))).scalars().all()} == {
        "100"
    }
