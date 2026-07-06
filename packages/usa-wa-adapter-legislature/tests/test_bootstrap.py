"""Tests for bootstrap.py — idempotent DB seed of WSL anchor rows."""

import pytest
from sqlalchemy import select

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.sessions import LegislativeSession
from usa_wa_adapter_legislature.bootstrap import (
    BootstrapAnchors,
    bootstrap_synthetic_anchors,
)


@pytest.fixture
async def anchors(db_session, usa_wa) -> BootstrapAnchors:
    return await bootstrap_synthetic_anchors(
        db_session,
        biennium="2025-26",
        jurisdiction_id=usa_wa.id,
    )


async def test_bootstrap_writes_one_legislature_two_chambers(db_session, anchors):
    """Legislature + 2 chambers + 2 party orgs = 5 Organizations after one call."""
    orgs = (await db_session.execute(select(Organization))).scalars().all()
    assert len(orgs) == 5
    by_type = {o.org_type: o for o in orgs} | {
        ("chamber", o.short_name): o for o in orgs if o.org_type == "chamber"
    }
    legislature = next(o for o in orgs if o.org_type == "legislature")
    chambers = [o for o in orgs if o.org_type == "chamber"]
    assert legislature.name == "Washington State Legislature"
    assert legislature.id == anchors.legislature_id
    assert {c.short_name for c in chambers} == {"House", "Senate"}
    assert all(c.parent_organization_id == legislature.id for c in chambers)
    assert by_type[("chamber", "House")].id == anchors.house_id
    assert by_type[("chamber", "Senate")].id == anchors.senate_id


async def test_bootstrap_writes_two_party_orgs(db_session, anchors):
    """Two ``org_type='party'`` orgs — Republican + Democratic — parented to nothing."""
    parties = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "party")))
        .scalars()
        .all()
    )
    assert len(parties) == 2
    by_source_id = {p.source_id: p for p in parties}
    assert set(by_source_id) == {"party-republican", "party-democratic"}
    assert by_source_id["party-republican"].name == "Washington State Republican Party"
    assert by_source_id["party-democratic"].name == "Washington State Democratic Party"
    assert all(p.parent_organization_id is None for p in parties)
    assert all(p.jurisdiction_id is not None for p in parties)
    # anchors expose the party ids keyed by canonical slug
    assert anchors.party_ids["republican"] == by_source_id["party-republican"].id
    assert anchors.party_ids["democratic"] == by_source_id["party-democratic"].id
    # No Independent party (independent = absence of a party Assignment).
    assert "independent" not in anchors.party_ids


async def test_bootstrap_writes_biennium_and_two_regular_sessions(db_session, anchors):
    """1 biennium parent + 2 regular sessions = 3 LegislativeSessions."""
    sessions = (await db_session.execute(select(LegislativeSession))).scalars().all()
    assert len(sessions) == 3
    biennium = next(s for s in sessions if s.classification == "biennium")
    regulars = sorted((s for s in sessions if s.classification == "regular"), key=lambda s: s.slug)

    assert biennium.slug == "usa-wa-2025-26"
    assert biennium.biennium_label == "2025-26"
    assert biennium.parent_legislative_session_id is None
    assert biennium.id == anchors.biennium_session_id

    assert [r.slug for r in regulars] == ["usa-wa-2025", "usa-wa-2026"]
    assert all(r.parent_legislative_session_id == biennium.id for r in regulars)
    assert all(r.biennium_label == "2025-26" for r in regulars)
    assert anchors.regular_session_ids == {2025: regulars[0].id, 2026: regulars[1].id}


async def test_bootstrap_is_idempotent(db_session, usa_wa):
    """Re-running yields the same anchor IDs and writes no new rows."""
    first = await bootstrap_synthetic_anchors(
        db_session, biennium="2025-26", jurisdiction_id=usa_wa.id
    )
    second = await bootstrap_synthetic_anchors(
        db_session, biennium="2025-26", jurisdiction_id=usa_wa.id
    )

    assert first == second
    org_count = len((await db_session.execute(select(Organization))).scalars().all())
    sess_count = len((await db_session.execute(select(LegislativeSession))).scalars().all())
    assert org_count == 5
    assert sess_count == 3


async def test_bootstrap_fk_integrity(db_session, anchors):
    """The biennium → regular chain and legislature → chamber chain hold."""
    leg = (
        await db_session.execute(
            select(Organization).where(Organization.id == anchors.legislature_id)
        )
    ).scalar_one()
    house = (
        await db_session.execute(select(Organization).where(Organization.id == anchors.house_id))
    ).scalar_one()
    biennium = (
        await db_session.execute(
            select(LegislativeSession).where(LegislativeSession.id == anchors.biennium_session_id)
        )
    ).scalar_one()
    regular_2025 = (
        await db_session.execute(
            select(LegislativeSession).where(
                LegislativeSession.id == anchors.regular_session_ids[2025]
            )
        )
    ).scalar_one()

    assert house.parent_organization_id == leg.id
    assert biennium.organization_id == leg.id
    assert regular_2025.organization_id == leg.id
    assert regular_2025.parent_legislative_session_id == biennium.id
