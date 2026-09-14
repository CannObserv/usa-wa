"""get_or_create_role role_type reconciliation (usa-wa#110).

#110 had two halves. The *classifier* half is here: a Role stamped with the
pre-#110 generic `member` adopts the catalog slug a caller now asserts, once,
without churning the clock on re-assertion. The *convergence* half —
`RoleDescriptor.to_observation` then reading as a true no-op against PM's
record — went with the PM sync stack in #314. It was never adapter behaviour;
it lived here only because this is where the defect was found.
"""

from clearinghouse_domain_legislative.identity import Organization, Role
from usa_wa_adapter_legislature.normalize.members import get_or_create_role


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
