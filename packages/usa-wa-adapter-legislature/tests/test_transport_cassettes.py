"""Cassette-replayed transport tests — default tier; no live network."""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.transport import WSLClient


async def test_get_active_committees_returns_committee_rows(wsl_vcr):
    """Cassette replay yields the recorded committee set with expected shape."""
    cassette = "committee_service_get_active_committees_2025-26.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeService")
        rows = await client.get_active_committees()

    # Recorded snapshot: 34 active committees for the 2025-26 biennium.
    assert len(rows) == 34
    expected_keys = {"Id", "Name", "LongName", "Agency", "Acronym", "Phone"}
    for row in rows:
        assert expected_keys.issubset(row.keys())
        assert isinstance(row["Id"], int)
        assert row["Agency"] in {"House", "Senate"}

    agencies = {row["Agency"] for row in rows}
    assert agencies == {"House", "Senate"}


async def test_get_active_committees_phone_is_string_when_present(wsl_vcr):
    """Phone strings round-trip as plain text (zeep doesn't coerce to a type)."""
    cassette = "committee_service_get_active_committees_2025-26.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeService")
        rows = await client.get_active_committees()

    phones = [r["Phone"] for r in rows if r["Phone"]]
    assert phones, "expected at least one committee with a Phone"
    assert all(isinstance(p, str) for p in phones)


async def test_get_active_committees_wrong_service_raises():
    """Misuse-guard: the service-name dispatch is enforced."""
    client = WSLClient("LegislationService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.get_active_committees()


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
    assert len(fetched.committees) == 34
    expected_keys = {"Id", "Name", "LongName", "Agency", "Acronym", "Phone"}
    for row in fetched.committees:
        assert expected_keys.issubset(row.keys())


async def test_fetch_active_committees_wrong_service_raises():
    """The archival fetch enforces the same service-name dispatch guard."""
    client = WSLClient("LegislationService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.fetch_active_committees()
