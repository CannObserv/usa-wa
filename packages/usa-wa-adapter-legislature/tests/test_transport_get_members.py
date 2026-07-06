"""Unit tests for the member-pull transport methods (P1b step 0/1).

``WSLClient.get_sponsors(biennium)`` (SponsorService.GetSponsors) and
``WSLClient.get_active_committee_members(agency, committee_name)``
(CommitteeService.GetActiveCommitteeMembers) are the non-archival parsed-dict
siblings the write-free member-identity probe (step 0) calls directly — mirroring
``get_committees``. Like it, they inject a fake zeep client rather than replay a
cassette (the archival ``fetch_*`` forms + cassettes arrive in step 1).
"""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.transport import WSLClient


class _FakeSponsorService:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def GetSponsors(self, biennium):  # noqa: N802 — mirrors the SOAP op name
        self.calls.append(biennium)
        return self._rows


class _FakeMemberService:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, str]] = []

    def GetActiveCommitteeMembers(self, agency, committeeName):  # noqa: N802, N803
        self.calls.append((agency, committeeName))
        return self._rows


class _FakeClient:
    def __init__(self, service) -> None:
        self.service = service


# --- get_sponsors -------------------------------------------------------------


async def test_get_sponsors_passes_biennium_and_serializes() -> None:
    rows = [
        {"Id": 31526, "Name": "Peter Abbarno", "Agency": "House", "Party": "R", "District": "20"},
        {"Id": 27182, "Name": "Kristine Reeves", "Agency": "House", "Party": "D", "District": "30"},
    ]
    client = WSLClient("SponsorService")
    client._client = _FakeClient(_FakeSponsorService(rows))  # bypass lazy WSDL load

    result = await client.get_sponsors("2025-26")

    assert client._client.service.calls == ["2025-26"]
    assert [r["Id"] for r in result] == [31526, 27182]


async def test_get_sponsors_empty_response_is_empty_list() -> None:
    client = WSLClient("SponsorService")
    client._client = _FakeClient(_FakeSponsorService([]))
    assert await client.get_sponsors("2099-00") == []


async def test_get_sponsors_wrong_service_raises() -> None:
    client = WSLClient("CommitteeService")
    with pytest.raises(ValueError, match="SponsorService"):
        await client.get_sponsors("2025-26")


# --- get_active_committee_members ---------------------------------------------


async def test_get_active_committee_members_passes_args_and_serializes() -> None:
    rows = [{"Id": 27182, "Name": "Kristine Reeves", "Agency": "House", "Party": "Democrat"}]
    client = WSLClient("CommitteeService")
    client._client = _FakeClient(_FakeMemberService(rows))

    result = await client.get_active_committee_members("House", "Agriculture & Natural Resources")

    assert client._client.service.calls == [("House", "Agriculture & Natural Resources")]
    assert [r["Id"] for r in result] == [27182]


async def test_get_active_committee_members_empty_is_empty_list() -> None:
    client = WSLClient("CommitteeService")
    client._client = _FakeClient(_FakeMemberService([]))
    assert await client.get_active_committee_members("Senate", "Nope") == []


async def test_get_active_committee_members_wrong_service_raises() -> None:
    client = WSLClient("SponsorService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.get_active_committee_members("House", "Rules")
