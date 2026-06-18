"""Cassette-replayed transport tests — default tier; no live network."""

from __future__ import annotations

from usa_wa_adapter_legislature.transport import WSLClient


def test_get_active_committees_returns_committee_rows(wsl_vcr):
    """Cassette replay yields the recorded committee set with expected shape."""
    cassette = "committee_service_get_active_committees_2025-26.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeService")
        rows = client.get_active_committees()

    # Recorded snapshot: 34 active committees for the 2025-26 biennium.
    assert len(rows) == 34
    expected_keys = {"Id", "Name", "LongName", "Agency", "Acronym", "Phone"}
    for row in rows:
        assert expected_keys.issubset(row.keys())
        assert isinstance(row["Id"], int)
        assert row["Agency"] in {"House", "Senate"}

    agencies = {row["Agency"] for row in rows}
    assert agencies == {"House", "Senate"}


def test_get_active_committees_phone_is_string_when_present(wsl_vcr):
    """Phone strings round-trip as plain text (zeep doesn't coerce to a type)."""
    cassette = "committee_service_get_active_committees_2025-26.yaml"
    with wsl_vcr.use_cassette(cassette):
        client = WSLClient("CommitteeService")
        rows = client.get_active_committees()

    phones = [r["Phone"] for r in rows if r["Phone"]]
    assert phones, "expected at least one committee with a Phone"
    assert all(isinstance(p, str) for p in phones)


def test_get_active_committees_wrong_service_raises():
    """Misuse-guard: the service-name dispatch is enforced."""
    import pytest

    client = WSLClient("LegislationService")
    with pytest.raises(ValueError, match="CommitteeService"):
        client.get_active_committees()
