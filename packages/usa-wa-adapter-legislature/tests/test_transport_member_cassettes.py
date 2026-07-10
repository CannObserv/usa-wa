"""Cassette-replayed transport tests for the member pulls (P1b step 1).

Replays recorded SOAP envelopes for ``SponsorService.GetSponsors`` and
``CommitteeService.GetActiveCommitteeMembers`` — no live WSL. These pin the live
``Member`` field names and, critically, the **per-endpoint party encoding** the sponsor
normalizer must reconcile: single letters (``"R"``/``"D"``) on the sponsor endpoint vs
full words (``"Democrat"``/``"Republican"``) on the committee endpoint (step 0 finding).
The round-trip tests prove the offline re-parsers recover the same records as the live
pull (the #56 cache path can't drift from #54's archival parse).
"""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.transport import WSLClient

_MEMBER_KEYS = {"Id", "Name", "LongName", "Agency", "Party", "District", "FirstName", "LastName"}

SPONSORS_CASSETTE = "sponsor_service_get_sponsors_2025-26.yaml"
HOUSE_MEMBERS_CASSETTE = "committee_service_get_active_committee_members_house_appropriations.yaml"
SENATE_MEMBERS_CASSETTE = (
    "committee_service_get_active_committee_members_senate_ways_and_means.yaml"
)


# --- fetch_sponsors -----------------------------------------------------------


async def test_fetch_sponsors_returns_member_rows_and_wire(wsl_vcr):
    """Recorded snapshot: 158 sponsor rows for 2025-26, each a Member; wire is SOAP XML."""
    with wsl_vcr.use_cassette(SPONSORS_CASSETTE):
        client = WSLClient("SponsorService")
        fetched = await client.fetch_sponsors("2025-26")

    assert len(fetched.records) == 158
    for row in fetched.records:
        assert _MEMBER_KEYS.issubset(row.keys())
        assert isinstance(row["Id"], int)

    # Pristine SOAP wire (#54), not our re-serialization.
    assert isinstance(fetched.wire, bytes)
    assert b"GetSponsors" in fetched.wire
    assert fetched.wire.lstrip().startswith(b"<")
    assert "xml" in fetched.content_type.lower()


async def test_fetch_sponsors_party_is_single_letter(wsl_vcr):
    """The sponsor endpoint spells party as single letters ``R``/``D`` (step 0 finding)."""
    with wsl_vcr.use_cassette(SPONSORS_CASSETTE):
        client = WSLClient("SponsorService")
        fetched = await client.fetch_sponsors("2025-26")

    parties = {r["Party"] for r in fetched.records if r["Party"]}
    assert parties == {"R", "D"}


async def test_fetch_sponsors_includes_named_persons_and_blanked_stubs(wsl_vcr):
    """Named legislators carry FirstName/LastName/District; step 0's blanked stubs don't."""
    with wsl_vcr.use_cassette(SPONSORS_CASSETTE):
        client = WSLClient("SponsorService")
        fetched = await client.fetch_sponsors("2025-26")

    named = [r for r in fetched.records if (r["FirstName"] or "").strip()]
    blanked = [r for r in fetched.records if not (r["FirstName"] or "").strip()]
    assert len(named) == 153
    assert len(blanked) == 5
    # a named row recomposes a full name and carries a district
    sample = named[0]
    assert sample["LastName"] and sample["District"]
    # blanked stubs keep a real Id + chamber-typed LongName but no name
    assert all(isinstance(r["Id"], int) and r["Agency"] in {"House", "Senate"} for r in blanked)


async def test_parse_sponsors_round_trips_archived_wire(wsl_vcr):
    """Re-parsing the archived GetSponsors wire offline recovers the same Member rows."""
    with wsl_vcr.use_cassette(SPONSORS_CASSETTE):
        client = WSLClient("SponsorService")
        fetched = await client.fetch_sponsors("2025-26")
        reparsed = await client.parse_sponsors(fetched.wire)

    assert reparsed and len(reparsed) == len(fetched.records)
    assert [r["Id"] for r in reparsed] == [r["Id"] for r in fetched.records]


async def test_fetch_sponsors_wrong_service_raises():
    client = WSLClient("CommitteeService")
    with pytest.raises(ValueError, match="SponsorService"):
        await client.fetch_sponsors("2025-26")


async def test_parse_sponsors_wrong_service_raises():
    client = WSLClient("CommitteeService")
    with pytest.raises(ValueError, match="SponsorService"):
        await client.parse_sponsors(b"<x/>")


# --- fetch_committee_members --------------------------------------------------


async def test_fetch_committee_members_returns_member_rows_and_wire(wsl_vcr):
    """House Appropriations roster: 31 members; wire is the GetActiveCommitteeMembers SOAP."""
    with wsl_vcr.use_cassette(HOUSE_MEMBERS_CASSETTE):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_committee_members("House", "Appropriations")

    assert len(fetched.records) == 31
    for row in fetched.records:
        assert _MEMBER_KEYS.issubset(row.keys())
        assert isinstance(row["Id"], int)
        assert row["Agency"] == "House"

    assert isinstance(fetched.wire, bytes)
    assert b"GetActiveCommitteeMembers" in fetched.wire
    assert "xml" in fetched.content_type.lower()


async def test_committee_members_party_is_full_word(wsl_vcr):
    """The committee endpoint spells party in full — ``Democrat``/``Republican`` — unlike
    the sponsor endpoint's ``D``/``R`` (the encoding split the normalizer must reconcile)."""
    with wsl_vcr.use_cassette(HOUSE_MEMBERS_CASSETTE):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_committee_members("House", "Appropriations")

    parties = {r["Party"] for r in fetched.records if r["Party"]}
    assert parties <= {"Democrat", "Republican"}
    assert parties  # non-empty — the roster carries party labels


async def test_parse_committee_members_round_trips_archived_wire(wsl_vcr):
    """Offline re-parse of the archived roster wire recovers the same members."""
    with wsl_vcr.use_cassette(HOUSE_MEMBERS_CASSETTE):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_committee_members("House", "Appropriations")
        reparsed = await client.parse_committee_members(fetched.wire)

    assert reparsed and len(reparsed) == len(fetched.records)
    assert {r["Id"] for r in reparsed} == {r["Id"] for r in fetched.records}


async def test_fetch_committee_members_senate_committee(wsl_vcr):
    """A Senate committee (Ways & Means) resolves under agency=Senate — cross-chamber shape."""
    with wsl_vcr.use_cassette(SENATE_MEMBERS_CASSETTE):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_committee_members("Senate", "Ways & Means")

    assert len(fetched.records) == 24
    assert all(r["Agency"] == "Senate" for r in fetched.records)


async def test_fetch_committee_members_wrong_service_raises():
    client = WSLClient("SponsorService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.fetch_committee_members("House", "Rules")


async def test_parse_committee_members_wrong_service_raises():
    client = WSLClient("SponsorService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.parse_committee_members(b"<x/>")


# --- fetch_historical_committee_members (GetCommitteeMembers, #82) --------------

HIST_MEMBERS_CASSETTE = "committee_service_get_committee_members_house_appropriations_2013-14.yaml"
HIST_MEMBERS_MISSING_CASSETTE = "committee_service_get_committee_members_missing.yaml"


async def test_fetch_historical_committee_members_returns_biennium_roster(wsl_vcr):
    """GetCommitteeMembers(biennium, agency, name) — a *past* biennium's roster (2013-14
    House Appropriations = 37 members). Same member-row shape as the active op; the
    committee is query context, not a field on the row."""
    with wsl_vcr.use_cassette(HIST_MEMBERS_CASSETTE):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_historical_committee_members(
            "2013-14", "House", "Appropriations"
        )

    assert len(fetched.records) == 37
    for row in fetched.records:
        assert _MEMBER_KEYS.issubset(row.keys())
        assert isinstance(row["Id"], int)
        assert row["Agency"] == "House"

    # the wire is the historical op's envelope — NOT GetActiveCommitteeMembers
    assert isinstance(fetched.wire, bytes)
    assert b"GetCommitteeMembers" in fetched.wire
    assert "xml" in fetched.content_type.lower()


async def test_parse_historical_committee_members_round_trips_archived_wire(wsl_vcr):
    """The offline re-parse (span builder's archive-first path) recovers the same members."""
    with wsl_vcr.use_cassette(HIST_MEMBERS_CASSETTE):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_historical_committee_members(
            "2013-14", "House", "Appropriations"
        )
        reparsed = await client.parse_historical_committee_members(fetched.wire)

    assert reparsed and len(reparsed) == len(fetched.records)
    assert {r["Id"] for r in reparsed} == {r["Id"] for r in fetched.records}


async def test_fetch_historical_committee_members_missing_committee_yields_empty(wsl_vcr):
    """A committee absent from that biennium raises a benign Fault → swallowed to empty,
    so the harvest fan-out skips and continues (also covers the sub-1999-00 floor)."""
    with wsl_vcr.use_cassette(HIST_MEMBERS_MISSING_CASSETTE):
        client = WSLClient("CommitteeService")
        fetched = await client.fetch_historical_committee_members(
            "2013-14", "House", "NoSuchCommitteeXYZ"
        )

    assert fetched.records == []
    assert fetched.wire == b""


async def test_fetch_historical_committee_members_wrong_service_raises():
    client = WSLClient("SponsorService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.fetch_historical_committee_members("2013-14", "House", "Rules")


async def test_parse_historical_committee_members_wrong_service_raises():
    client = WSLClient("SponsorService")
    with pytest.raises(ValueError, match="CommitteeService"):
        await client.parse_historical_committee_members(b"<x/>")


# --- the empty-roster Fault matcher is narrow (must not swallow real faults) ----


@pytest.mark.parametrize(
    "message,expected",
    [
        # the two live messages (probed 2026-07)
        (
            "No committee members have been found. Please ensure that you have a valid "
            "biennium, agency, and committee name.",
            True,
        ),
        (
            "No committee was found. Please ensure that you have a valid biennium, agency, "
            "and committee name.",
            True,
        ),
        # an unrelated fault must NOT be swallowed — even though real faults can mention
        # a biennium, only the two known "nothing here" messages count
        ("Server was unable to process request.", False),
        ("Object reference not set to an instance of an object.", False),
        ("Please ensure that you have a valid biennium.", False),
    ],
)
def test_empty_committee_roster_matcher_is_narrow(message, expected):
    from zeep.exceptions import Fault

    from usa_wa_adapter_legislature.transport import _is_empty_committee_roster

    assert _is_empty_committee_roster(Fault(message)) is expected
