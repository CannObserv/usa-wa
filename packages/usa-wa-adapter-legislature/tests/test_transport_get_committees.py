"""Unit tests for ``WSLClient.get_committees(biennium)``.

The parameterized historical form of the committee pull (``GetCommittees(biennium)``)
is the explicit-membership source for biennium-absence retirement (#44). Unlike
``GetActiveCommittees`` it takes a biennium argument, so these tests inject a fake
zeep client rather than replay a cassette (no live network to record one).
"""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.transport import WSLClient


class _FakeService:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def GetCommittees(self, biennium):  # noqa: N802 — mirrors the SOAP op name
        self.calls.append(biennium)
        return self._rows


class _FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self.service = _FakeService(rows)


async def test_get_committees_passes_biennium_and_serializes() -> None:
    rows = [
        {"Id": 31635, "Name": "Capital Budget", "Agency": "House", "Acronym": "CB"},
        {"Id": 30000, "Name": "Ways & Means", "Agency": "Senate", "Acronym": "WAYS"},
    ]
    client = WSLClient("CommitteeService")
    client._client = _FakeClient(rows)  # bypass lazy WSDL load

    result = await client.get_committees("2023-24")

    assert client._client.service.calls == ["2023-24"]
    assert [r["Id"] for r in result] == [31635, 30000]


async def test_get_committees_empty_response_is_empty_list() -> None:
    client = WSLClient("CommitteeService")
    client._client = _FakeClient([])

    assert await client.get_committees("2099-00") == []


async def test_get_committees_wrong_service_raises() -> None:
    client = WSLClient("LegislationService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.get_committees("2025-26")
