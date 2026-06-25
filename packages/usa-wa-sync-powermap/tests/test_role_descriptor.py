"""RoleDescriptor tests — (org, title) observation + dependency gating.

Roles observe by their PM org id + title (PM's structural match key), so the
duplicate-prevention is PM-native; the descriptor's job is to defer until the
org is anchored and to mirror PM's curated title update-only.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization, Role
from usa_wa_sync_powermap.descriptors import RoleDescriptor


@pytest.fixture
def descriptor() -> RoleDescriptor:
    return RoleDescriptor()


async def _add_org(session, *, source_id="HOUSE", name="House", anchor=None):
    org = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=name,
        org_type="chamber",
        pm_organization_id=anchor,
    )
    session.add(org)
    await session.flush()
    return org


async def _add_role(session, *, org, source_id="R-1", name="Chair", anchor=None):
    role = Role(
        source="usa_wa_legislature",
        source_id=source_id,
        organization_id=org.id,
        name=name,
        role_type="committee_leadership",
        pm_role_id=anchor,
    )
    session.add(role)
    await session.flush()
    return role


async def test_dependencies_ready_requires_anchored_org(db_session, descriptor):
    unanchored = await _add_org(db_session, anchor=None)
    role = await _add_role(db_session, org=unanchored)
    assert await descriptor.dependencies_ready(db_session, role) is False

    anchored = await _add_org(db_session, source_id="SENATE", name="Senate", anchor=ULID())
    role2 = await _add_role(db_session, org=anchored, source_id="R-2")
    assert await descriptor.dependencies_ready(db_session, role2) is True


async def test_to_observation_keys_on_org_pm_id_and_title(db_session, descriptor):
    org_pm = ULID()
    org = await _add_org(db_session, anchor=org_pm)
    role = await _add_role(db_session, org=org, name="Vice Chair")

    obs = await descriptor.to_observation(db_session, role)

    assert obs == {"organization_id": str(org_pm), "title": "Vice Chair"}


async def test_local_match_by_anchor(db_session, descriptor):
    org = await _add_org(db_session, anchor=ULID())
    pm_id = ULID()
    role = await _add_role(db_session, org=org, anchor=pm_id)

    matched = await descriptor.local_match(db_session, {"id": str(pm_id)})
    assert matched is not None and matched.id == role.id
    assert await descriptor.local_match(db_session, {"id": str(ULID())}) is None


async def test_upsert_adopts_title_and_anchor(db_session, descriptor):
    org = await _add_org(db_session, anchor=ULID())
    role = await _add_role(db_session, org=org, name="Adapter Title")
    pm_id = ULID()
    record = {"id": str(pm_id), "title": "Chair", "updated_at": "2030-01-01T00:00:00Z"}

    result = await descriptor.upsert_from_pm(db_session, record, existing=role)

    assert result is role
    assert role.name == "Chair"  # adopted PM's curated title
    assert role.pm_role_id == pm_id


async def test_upsert_update_only_skips_unknown_role(db_session, descriptor):
    result = await descriptor.upsert_from_pm(db_session, {"id": str(ULID()), "title": "Ghost"})
    assert result is None
    assert (await db_session.execute(select(Role))).scalars().all() == []


async def test_upsert_mirrors_pm_archived_at_to_retired_tombstone(db_session, descriptor):
    """PM archival on an anchored role mirrors onto ``retired_at`` (usa-wa#41)."""
    org = await _add_org(db_session, anchor=ULID())
    pm_id = ULID()
    role = await _add_role(db_session, org=org, anchor=pm_id)
    assert role.retired_at is None

    record = {"id": str(pm_id), "title": "Chair", "archived_at": "2026-06-20T00:00:00Z"}
    result = await descriptor.upsert_from_pm(db_session, record, existing=role)

    assert result is role
    assert role.retired_at == datetime(2026, 6, 20, tzinfo=UTC)


async def test_upsert_clears_tombstone_when_pm_unarchives(db_session, descriptor):
    """PM un-archiving a role clears the mirrored tombstone."""
    org = await _add_org(db_session, anchor=ULID())
    pm_id = ULID()
    role = await _add_role(db_session, org=org, anchor=pm_id)
    role.retired_at = datetime(2026, 6, 20, tzinfo=UTC)
    await db_session.flush()

    result = await descriptor.upsert_from_pm(
        db_session, {"id": str(pm_id), "title": "Chair"}, existing=role
    )

    assert result is role
    assert role.retired_at is None


async def test_last_updated_row_and_record(db_session, descriptor):
    org = await _add_org(db_session, anchor=ULID())
    role = await _add_role(db_session, org=org)
    role.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated(role) == datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated({"updated_at": "2026-06-02T00:00:00Z"}) == datetime(
        2026, 6, 2, tzinfo=UTC
    )
