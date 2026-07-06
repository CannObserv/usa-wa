"""Tests for normalize/committee_members.py — committee-membership Assignments (P1b 6)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from clearinghouse_core.adapter import FetchedPayload
from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    PersonIdentifier,
    Role,
)
from usa_wa_adapter_legislature.normalize.committee_members import normalize_committee_members
from usa_wa_adapter_legislature.normalize.sponsors import normalize_sponsors

BIENNIUM = "2025-26"
COMMITTEE_ID = "31635"


def _member(id_, first, last, *, agency="House", party="Democrat", district="30"):
    return {
        "Id": id_,
        "Name": f"{first} {last}",
        "LongName": f"Representative {last}",
        "Agency": agency,
        "Party": party,
        "District": district,
        "FirstName": first,
        "LastName": last,
    }


def _payload(members):
    return FetchedPayload(
        url="https://wslwebservices.leg.wa.gov/CommitteeService.asmx#GetActiveCommitteeMembers",
        fetched_at=datetime.now(UTC),
        content_type="text/xml",
        body=b"",
        parsed=members,
    )


@pytest.fixture
async def committee(db_session, usa_wa):
    org = Organization(
        source="usa_wa_legislature",
        source_id=COMMITTEE_ID,
        jurisdiction_id=usa_wa.id,
        name="House Appropriations",
        short_name="Appropriations",
        org_type="committee",
    )
    db_session.add(org)
    await db_session.flush()
    return org


async def _run(session, members, committee_source_id=COMMITTEE_ID):
    return await normalize_committee_members(
        _payload(members),
        session=session,
        committee_source_id=committee_source_id,
        biennium=BIENNIUM,
    )


async def test_membership_assignment_emitted(db_session, usa_wa, committee):
    members = [_member(301, "Kristine", "Reeves"), _member(302, "Timm", "Ormsby")]

    batch = await _run(db_session, members)

    persons = [e for e in batch.entities if isinstance(e, Person)]
    assert {p.source_id for p in persons} == {"301", "302"}
    assert any(isinstance(e, PersonIdentifier) for e in batch.entities)

    roles = [e for e in batch.entities if isinstance(e, Role)]
    assert len(roles) == 1  # one shared committee Member role
    role = roles[0]
    assert role.organization_id == committee.id
    assert role.role_type == "member"
    assert role.name == "Member"
    assert role.jurisdiction_id is None  # title-keyed, not a seat

    asgs = [e for e in batch.entities if isinstance(e, Assignment)]
    assert len(asgs) == 2
    assert all(a.role_id == role.id for a in asgs)
    assert all(a.valid_from == date(2025, 1, 1) and a.is_active for a in asgs)
    assert all(f":committee:{COMMITTEE_ID}:" in a.source_id for a in asgs)


async def test_non_person_row_skipped(db_session, usa_wa, committee):
    members = [
        {"Id": 999, "Name": " ", "FirstName": None, "LastName": None},
        _member(301, "Kristine", "Reeves"),
    ]
    batch = await _run(db_session, members)
    assert {p.source_id for p in batch.entities if isinstance(p, Person)} == {"301"}


async def test_unknown_committee_returns_empty_batch(db_session, usa_wa):
    # No committee Org seeded for this id.
    batch = await _run(db_session, [_member(301, "Kristine", "Reeves")], committee_source_id="NOPE")
    assert batch.entities == []


async def test_person_deduped_against_sponsor_pull(db_session, usa_wa, committee):
    """A member already created by the sponsor pull resolves to the same Person row."""
    # First, the sponsor pull creates the Person (source_id 301).
    from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors

    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    sponsor_payload = FetchedPayload(
        url="https://wslwebservices.leg.wa.gov/SponsorService.asmx#GetSponsors",
        fetched_at=datetime.now(UTC),
        content_type="text/xml",
        body=b"",
        parsed=[_member(301, "Kristine", "Reeves", party="D")],
    )
    sponsor_batch = await normalize_sponsors(
        sponsor_payload,
        session=db_session,
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium=BIENNIUM,
    )
    sponsor_person_id = next(p.id for p in sponsor_batch.entities if isinstance(p, Person))

    # Then the committee pull for the same member reuses that Person id.
    committee_batch = await _run(db_session, [_member(301, "Kristine", "Reeves")])
    committee_person = next(p for p in committee_batch.entities if isinstance(p, Person))
    assert committee_person.id == sponsor_person_id
    # and its membership Assignment points at that same person
    asg = next(a for a in committee_batch.entities if isinstance(a, Assignment))
    assert asg.person_id == sponsor_person_id
