"""Seat-Role model (usa-wa#68) — local Role aligned with Power Map's seat model.

PM (power-map#261/#263) models each legislative seat as a durable ``roles`` row
keyed on the structural tuple ``(organization_id, role_type, jurisdiction_id,
qualifier)`` — House = 2 seats/LD (``qualifier`` "Position 1"/"Position 2"),
Senate = 1 seat/LD (``qualifier`` NULL). usa-wa's local ``Role`` gains
``jurisdiction_id`` + ``qualifier`` so a produced seat structurally matches PM's
and the observation auto-attaches instead of minting a duplicate.

Uniqueness splits in two, mirroring PM:
- **Districted seats** (``jurisdiction_id`` NOT NULL): unique on
  ``(org, role_type, jurisdiction, qualifier)`` — NULLS NOT DISTINCT so a Senate
  seat's NULL qualifier is still one-per-district.
- **Non-districted roles** (``jurisdiction_id`` NULL): keep title identity
  ``(org, name)`` — committee/leadership/etc.
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from clearinghouse_domain_legislative.identity import Organization, Role


async def _house(db_session, usa_wa) -> Organization:
    org = Organization(
        source="usa_wa_legislature",
        source_id="house",
        name="Washington State House of Representatives",
        org_type="chamber",
        jurisdiction_id=usa_wa.id,
    )
    db_session.add(org)
    await db_session.flush()
    return org


def _seat(org, *, source_id, jurisdiction_id, qualifier, role_type="elected_member", name="seat"):
    return Role(
        source="usa_wa_legislature",
        source_id=source_id,
        organization_id=org.id,
        name=name,
        role_type=role_type,
        jurisdiction_id=jurisdiction_id,
        qualifier=qualifier,
    )


def test_role_carries_seat_columns():
    cols = inspect(Role).columns
    assert "jurisdiction_id" in cols
    assert "qualifier" in cols
    assert cols["jurisdiction_id"].nullable is True
    assert cols["qualifier"].nullable is True


async def test_seat_role_persists(db_session, usa_wa):
    org = await _house(db_session, usa_wa)
    seat = _seat(org, source_id="ld-21-pos-1", jurisdiction_id=usa_wa.id, qualifier="Position 1")
    db_session.add(seat)
    await db_session.flush()
    assert seat.id is not None
    assert seat.jurisdiction_id == usa_wa.id
    assert seat.qualifier == "Position 1"


async def test_two_house_positions_coexist(db_session, usa_wa):
    """Same (org, role_type, jurisdiction) with distinct qualifiers are two seats."""
    org = await _house(db_session, usa_wa)
    db_session.add(
        _seat(org, source_id="ld-21-pos-1", jurisdiction_id=usa_wa.id, qualifier="Position 1")
    )
    db_session.add(
        _seat(org, source_id="ld-21-pos-2", jurisdiction_id=usa_wa.id, qualifier="Position 2")
    )
    await db_session.flush()  # no collision


async def test_duplicate_seat_tuple_collides(db_session, usa_wa):
    """Two rows sharing the full seat tuple collide, even with distinct natural keys."""
    org = await _house(db_session, usa_wa)
    db_session.add(_seat(org, source_id="a", jurisdiction_id=usa_wa.id, qualifier="Position 1"))
    await db_session.flush()
    db_session.add(_seat(org, source_id="b", jurisdiction_id=usa_wa.id, qualifier="Position 1"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_senate_null_qualifier_is_one_per_district(db_session, usa_wa):
    """NULL qualifier (Senate) is treated as a single value — NULLS NOT DISTINCT."""
    org = await _house(db_session, usa_wa)
    db_session.add(
        _seat(
            org,
            source_id="sen-a",
            jurisdiction_id=usa_wa.id,
            qualifier=None,
            role_type="state_senator",
        )
    )
    await db_session.flush()
    db_session.add(
        _seat(
            org,
            source_id="sen-b",
            jurisdiction_id=usa_wa.id,
            qualifier=None,
            role_type="state_senator",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_non_seat_roles_keep_title_identity(db_session, usa_wa):
    """jurisdiction NULL → uniqueness is (org, name); duplicate titles collide."""
    org = await _house(db_session, usa_wa)
    db_session.add(
        _seat(
            org,
            source_id="chair-a",
            jurisdiction_id=None,
            qualifier=None,
            role_type="committee_leadership",
            name="Chair",
        )
    )
    await db_session.flush()
    db_session.add(
        _seat(
            org,
            source_id="chair-b",
            jurisdiction_id=None,
            qualifier=None,
            role_type="committee_leadership",
            name="Chair",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_seat_and_nonseat_same_name_coexist(db_session, usa_wa):
    """A districted seat and a non-districted role may share a name — the title
    index is partial (only jurisdiction IS NULL), the seat index only fires on
    districted rows."""
    org = await _house(db_session, usa_wa)
    db_session.add(
        _seat(
            org,
            source_id="seat",
            jurisdiction_id=usa_wa.id,
            qualifier="Position 1",
            name="Member",
        )
    )
    db_session.add(
        _seat(org, source_id="role", jurisdiction_id=None, qualifier=None, name="Member")
    )
    await db_session.flush()  # no collision
