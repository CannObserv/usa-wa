"""get_or_create_role role_type reconciliation + gate-convergence (usa-wa#110)."""

from clearinghouse_domain_legislative.identity import Organization, Role
from clearinghouse_domain_legislative.role_types import RoleType
from usa_wa_adapter_legislature.normalize.members import get_or_create_role
from usa_wa_sync_powermap.descriptors import RoleDescriptor


async def _seed_catalog(session) -> None:
    """Seed the two membership slugs so to_observation carries role_type (it only sends a
    catalog-known slug) — prod has both, which is precisely why the stale `member` diverges."""
    for slug in ("member", "committee_member"):
        session.add(
            RoleType(
                slug=slug,
                display_name=slug.replace("_", " ").title(),
                expects_jurisdiction=False,
                requires_qualifier=False,
            )
        )
    await session.flush()


async def _org(session, source_id="C-1") -> Organization:
    org = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=f"Org {source_id}",
        org_type="committee",
    )
    session.add(org)
    await session.flush()
    return org


async def test_get_or_create_role_reconciles_stale_role_type(db_session):
    """A pre-existing Role stamped with the generic `member` is reclassified when a caller
    now asserts the catalog slug — the #110 finding-2 auto-heal for the current cohort."""
    org = await _org(db_session)
    stale = Role(
        source="usa_wa_legislature",
        source_id="committee-member-role:1",
        organization_id=org.id,
        name="Member",
        role_type="member",  # the pre-#110 generic slug
    )
    db_session.add(stale)
    await db_session.flush()

    got = await get_or_create_role(
        db_session,
        source_id="committee-member-role:1",
        organization_id=org.id,
        name="Member",
        role_type="committee_member",
    )

    assert got.id == stale.id  # same row (get, not create)
    assert got.role_type == "committee_member"  # classifier adopted


async def test_get_or_create_role_no_write_when_role_type_matches(db_session):
    """Idempotent: re-asserting the same role_type does not bump the clock (the differ-guard
    makes the reconcile a one-time write, not a per-refresh churn)."""
    org = await _org(db_session)
    role = await get_or_create_role(
        db_session,
        source_id="committee-member-role:2",
        organization_id=org.id,
        name="Member",
        role_type="committee_member",
    )
    await db_session.flush()
    clock = role.updated_at

    again = await get_or_create_role(
        db_session,
        source_id="committee-member-role:2",
        organization_id=org.id,
        name="Member",
        role_type="committee_member",
    )
    await db_session.flush()

    assert again.id == role.id
    assert again.updated_at == clock  # no reconcile write → clock unmoved


async def test_reclassified_role_makes_observation_a_noop(db_session):
    """Finding 3 — the convergence claim end-to-end: once a role carries PM's catalog slug,
    its title-shaped observation matches PM's `role_type_slug`, so the #109 no-op gate reads a
    true no-op (adopt clock, no re-send) instead of the perpetual re-enqueue of #110."""
    await _seed_catalog(db_session)
    org = await _org(db_session)
    role = await get_or_create_role(
        db_session,
        source_id="committee-member-role:3",
        organization_id=org.id,
        name="Member",
        role_type="committee_member",
    )
    org.pm_organization_id = __import__("ulid").ULID()
    await db_session.flush()

    descriptor = RoleDescriptor()
    obs = await descriptor.to_observation(db_session, role)
    # PM's record for this role: same org, same title, refined slug.
    pm_record = {
        "organization_id": obs["organization_id"],
        "title": "Member",
        "role_type_slug": "committee_member",
    }
    assert descriptor.observation_matches_record(obs, pm_record) is True

    # Sanity: the pre-#110 stale slug would NOT match (this is the churn it fixes).
    role.role_type = "member"
    await db_session.flush()
    stale_obs = await descriptor.to_observation(db_session, role)
    assert descriptor.observation_matches_record(stale_obs, pm_record) is False
