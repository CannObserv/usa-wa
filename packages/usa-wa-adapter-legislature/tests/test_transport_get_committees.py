"""Unit tests for ``WSLClient.get_committees(biennium)``.

The parameterized historical form of the committee pull (``GetCommittees(biennium)``)
is the explicit-membership source for biennium-absence retirement (#44). Unlike
``GetActiveCommittees`` it takes a biennium argument, so these tests inject a fake
zeep client rather than replay a cassette (no live network to record one).
"""

from __future__ import annotations

import pytest
from zeep.exceptions import Fault

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


# --- fetch_committees (archival GetCommittees, sub-project 3) ------------------


async def test_fetch_committees_returns_records_and_wire() -> None:
    """The archival sibling of get_committees: parsed rows + the pristine wire for
    #54 hashing (the wire the harvest archives under committees-roster:<biennium>)."""
    rows = [{"Id": 31635, "Name": "Capital Budget", "LongName": "House Capital Budget"}]
    client = WSLClient("CommitteeService")
    client._client = _FakeClient(rows)  # bypass lazy WSDL load
    client._transport.last_wire = b"<committees/>"  # the capturing transport's stash
    client._transport.last_content_type = "text/xml"

    fetched = await client.fetch_committees("2023-24")

    assert client._client.service.calls == ["2023-24"]
    assert [r["Id"] for r in fetched.records] == [31635]
    assert fetched.wire == b"<committees/>"
    assert fetched.content_type == "text/xml"


async def test_fetch_committees_empty_wire_defaults() -> None:
    client = WSLClient("CommitteeService")
    client._client = _FakeClient([])
    fetched = await client.fetch_committees("2099-00")
    assert fetched.records == []
    assert fetched.wire == b""  # no captured wire → empty, never None


async def test_fetch_committees_wrong_service_raises() -> None:
    client = WSLClient("CommitteeMeetingService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.fetch_committees("2025-26")


# --- out-of-range biennium (below WSL's 1991 floor) ---------------------------

_OUT_OF_RANGE_MSG = (
    "Invalid Input. ---> You have not submitted a valid biennium. Please enter biennium in "
    "the following format: 2005-06. Information is only available back to 1991."
)


class _FloorFaultService:
    """GetCommittees raises the WSL out-of-range Fault (the pre-1991 boundary)."""

    def GetCommittees(self, biennium):  # noqa: N802
        raise Fault(_OUT_OF_RANGE_MSG)


class _FloorFaultClient:
    def __init__(self):
        self.service = _FloorFaultService()


async def test_get_committees_out_of_range_returns_empty() -> None:
    """Below WSL's 1991 floor GetCommittees Faults; treat it as 'no committees' so the
    floor probe / harvest stop cleanly instead of crashing."""
    client = WSLClient("CommitteeService")
    client._client = _FloorFaultClient()
    assert await client.get_committees("1989-90") == []


async def test_fetch_committees_out_of_range_returns_empty_wire() -> None:
    client = WSLClient("CommitteeService")
    client._client = _FloorFaultClient()
    fetched = await client.fetch_committees("1989-90")
    assert fetched.records == []
    assert fetched.wire == b""


async def test_get_committees_reraises_unrelated_fault() -> None:
    """A Fault that isn't the out-of-range boundary must propagate, not be swallowed."""

    class _BoomService:
        def GetCommittees(self, biennium):  # noqa: N802
            raise Fault("Server error: something else entirely")

    class _BoomClient:
        service = _BoomService()

    client = WSLClient("CommitteeService")
    client._client = _BoomClient()
    with pytest.raises(Fault, match="something else"):
        await client.get_committees("2023-24")
