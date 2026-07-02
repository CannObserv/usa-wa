"""Cassette-replayed transport tests — default tier; no live network."""

from __future__ import annotations

from datetime import datetime

import pytest

from usa_wa_adapter_legislature.transport import WSLClient


def _committee_refs(meeting: dict) -> list[dict]:
    """Flatten the nested ``Committees.Committee[]`` off one meeting dict.

    zeep renders a single child as a dict and multiple as a list; normalize both
    to a list so callers don't branch."""
    block = meeting.get("Committees") or {}
    coms = block.get("Committee") if isinstance(block, dict) else None
    if coms is None:
        return []
    return [coms] if isinstance(coms, dict) else list(coms)


async def test_fetch_active_committees_returns_committee_rows(wsl_vcr):
    """Cassette replay yields the recorded committee set with expected shape."""
    cassette = "committee_service_get_active_committees_2025-26.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeService")
        rows = (await client.fetch_active_committees()).records

    # Recorded snapshot: 34 active committees for the 2025-26 biennium.
    assert len(rows) == 34
    expected_keys = {"Id", "Name", "LongName", "Agency", "Acronym", "Phone"}
    for row in rows:
        assert expected_keys.issubset(row.keys())
        assert isinstance(row["Id"], int)
        assert row["Agency"] in {"House", "Senate"}

    agencies = {row["Agency"] for row in rows}
    assert agencies == {"House", "Senate"}


async def test_fetch_active_committees_phone_is_string_when_present(wsl_vcr):
    """Phone strings round-trip as plain text (zeep doesn't coerce to a type)."""
    cassette = "committee_service_get_active_committees_2025-26.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeService")
        rows = (await client.fetch_active_committees()).records

    phones = [r["Phone"] for r in rows if r["Phone"]]
    assert phones, "expected at least one committee with a Phone"
    assert all(isinstance(p, str) for p in phones)


async def test_fetch_active_committees_captures_pristine_wire(wsl_vcr):
    """The archival fetch returns the raw SOAP envelope bytes alongside the parse.

    Provenance baseline (#54): ``wire`` is what WSL actually sent (the SOAP-XML
    response body), not our re-serialization. ``committees`` is the derived parse.
    """
    cassette = "committee_service_get_active_committees_2025-26.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_active_committees()

    # Wire is non-empty SOAP XML, not JSON.
    assert fetched.wire
    assert isinstance(fetched.wire, bytes)
    assert b"GetActiveCommittees" in fetched.wire
    assert fetched.wire.lstrip().startswith(b"<")
    assert "xml" in fetched.content_type.lower()

    # Parsed committees match the legacy list shape (same recorded snapshot).
    assert len(fetched.records) == 34
    expected_keys = {"Id", "Name", "LongName", "Agency", "Acronym", "Phone"}
    for row in fetched.records:
        assert expected_keys.issubset(row.keys())


async def test_fetch_active_committees_wrong_service_raises():
    """The archival fetch enforces the same service-name dispatch guard."""
    client = WSLClient("LegislationService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.fetch_active_committees()


async def test_fetch_committee_meetings_returns_wire_and_parsed(wsl_vcr):
    """Meeting pull yields committee-bearing meeting dicts + the pristine SOAP wire.

    ``GetCommitteeMeetings`` is the only source of Joint/``Other`` committee orgs
    (#39); the transport just fetches + archives, so this asserts shape, not the
    dedup/parenting the normalizer owns."""
    cassette = "committee_meeting_service_get_committee_meetings_2024-01-16.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeMeetingService")
        fetched = await client.fetch_committee_meetings(
            datetime(2024, 1, 16), datetime(2024, 1, 16, 23, 59, 59)
        )

    # Pristine SOAP wire, not our re-serialization (#54).
    assert isinstance(fetched.wire, bytes)
    assert b"GetCommitteeMeetings" in fetched.wire
    assert fetched.wire.lstrip().startswith(b"<")
    assert "xml" in fetched.content_type.lower()

    # records = meeting dicts, each exposing a nested committee ref with the
    # WSDL Committee shape.
    assert fetched.records, "expected at least one meeting on the recorded day"
    refs = [c for m in fetched.records for c in _committee_refs(m)]
    assert refs, "expected at least one committee ref across the meetings"
    assert {"Id", "Name", "LongName", "Agency", "Acronym"}.issubset(refs[0].keys())


async def test_fetch_committee_meetings_wrong_service_raises():
    """Service-name dispatch guard mirrors the committee-service methods."""
    client = WSLClient("CommitteeService")
    with pytest.raises(ValueError, match="CommitteeMeetingService"):
        await client.fetch_committee_meetings(datetime(2024, 1, 1), datetime(2024, 1, 2))


async def test_parse_committee_meetings_round_trips_archived_wire(wsl_vcr):
    """Re-parsing the archived wire offline (the #56 cache path) recovers the **same** meeting
    dicts as the live pull — it replays the stored bytes through the identical operation
    binding, so the cache can't drift from ``fetch_committee_meetings``."""
    cassette = "committee_meeting_service_get_committee_meetings_2024-01-16.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeMeetingService")
        fetched = await client.fetch_committee_meetings(
            datetime(2024, 1, 16), datetime(2024, 1, 16, 23, 59, 59)
        )
        reparsed = await client.parse_committee_meetings(fetched.wire)

    live_refs = [c for m in fetched.records for c in _committee_refs(m)]
    cached_refs = [c for m in reparsed for c in _committee_refs(m)]
    assert cached_refs and len(cached_refs) == len(live_refs)
    assert {c["Id"] for c in cached_refs} == {c["Id"] for c in live_refs}


async def test_parse_committee_meetings_wrong_service_raises():
    """Service-name dispatch guard mirrors the fetch method."""
    client = WSLClient("CommitteeService")
    with pytest.raises(ValueError, match="CommitteeMeetingService"):
        await client.parse_committee_meetings(b"<x/>")


async def test_parse_committees_round_trips_archived_wire(wsl_vcr):
    """Re-parsing an archived GetCommittees roster offline (Phase B) recovers the **same**
    Committee dicts as the live pull — replayed through the identical binding so the
    offline parse can't drift from ``fetch_committees`` (#54 fidelity)."""
    cassette = "committee_service_get_committees_2023-24.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_committees("2023-24")
        reparsed = await client.parse_committees(fetched.wire)

    assert reparsed and len(reparsed) == len(fetched.records)
    assert {c["Id"] for c in reparsed} == {c["Id"] for c in fetched.records}
    assert {c["LongName"] for c in reparsed} == {c["LongName"] for c in fetched.records}


async def test_parse_committees_wrong_service_raises():
    client = WSLClient("CommitteeMeetingService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.parse_committees(b"<x/>")
