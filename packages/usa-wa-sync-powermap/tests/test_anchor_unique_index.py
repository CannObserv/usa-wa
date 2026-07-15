"""Partial-unique-index enforcement of the one-row-per-PM-anchor invariant (#86).

A second local row stamped with an already-used PM anchor must fail loudly at the
DB layer (partial unique index ``WHERE pm_*_id IS NOT NULL``), so the duplicate is
caught at write time instead of silently arming a reconcile crash loop days later
(the #84 shape). NULL anchors (unsynced rows) must still coexist freely.
"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    Role,
)
from usa_wa_sync_powermap.descriptors import (
    AssignmentDescriptor,
    OrganizationDescriptor,
    PersonDescriptor,
    RoleDescriptor,
)


async def _make_person(session, anchor):
    return Person(source="wsl", source_id=str(ULID()), name_full="X", pm_person_id=anchor)


async def _make_org(session, anchor):
    return Organization(
        source="wsl",
        source_id=str(ULID()),
        name="X",
        org_type="committee",
        pm_organization_id=anchor,
    )


async def _make_role(session, anchor):
    org = await _make_org(session, None)
    session.add(org)
    await session.flush()
    return Role(
        source="wsl",
        source_id=str(ULID()),
        organization_id=org.id,
        name=f"Member {ULID()}",
        role_type="elected_member",
        pm_role_id=anchor,
    )


async def _make_assignment(session, anchor):
    person = await _make_person(session, None)
    role = await _make_role(session, None)
    session.add_all([person, role])
    await session.flush()
    return Assignment(
        source="wsl",
        source_id=str(ULID()),
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2025, 1, 1),
        is_active=True,
        pm_assignment_id=anchor,
    )


_BUILDERS = {
    "pm_person_id": _make_person,
    "pm_organization_id": _make_org,
    "pm_role_id": _make_role,
    "pm_assignment_id": _make_assignment,
}


@pytest.mark.parametrize("column", list(_BUILDERS))
async def test_duplicate_anchor_rejected(db_session, column) -> None:
    build = _BUILDERS[column]
    anchor = ULID()

    db_session.add(await build(db_session, anchor))
    await db_session.flush()

    db_session.add(await build(db_session, anchor))
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


@pytest.mark.parametrize("column", list(_BUILDERS))
async def test_null_anchors_coexist(db_session, column) -> None:
    """The partial index only constrains non-NULL anchors — many unsynced rows are fine."""
    build = _BUILDERS[column]
    db_session.add(await build(db_session, None))
    db_session.add(await build(db_session, None))
    await db_session.flush()  # no IntegrityError


def test_all_four_descriptors_are_anchor_keyed() -> None:
    """Guards the delegation surface: exactly the descriptors whose local_match keys
    on an anchor route through the tolerant base helper."""
    for cls in (AssignmentDescriptor, PersonDescriptor, RoleDescriptor, OrganizationDescriptor):
        assert cls.anchor_column.startswith("pm_")
