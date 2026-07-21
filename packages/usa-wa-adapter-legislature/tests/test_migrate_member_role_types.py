"""Tests for the #110 member-role reclassification migration."""

from clearinghouse_domain_legislative.identity import Organization, Role
from usa_wa_adapter_legislature.migrate_member_role_types import migrate_member_role_types


async def _role(session, *, source_id, role_type):
    # Each membership Role lives under its own org (a committee/party), matching prod —
    # the uq_roles_org_name index forbids two same-name roles under one org.
    org = Organization(
        source="usa_wa_legislature",
        source_id=f"org-{source_id}",
        name=f"Org {source_id}",
        org_type="committee",
    )
    session.add(org)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id=source_id,
        organization_id=org.id,
        name="Member",
        role_type=role_type,
    )
    session.add(role)
    await session.flush()
    return role


async def test_reclassifies_member_roles_by_source_id_prefix(db_session):
    committee = await _role(db_session, source_id="committee-member-role:31634", role_type="member")
    party = await _role(db_session, source_id="party-role:democratic", role_type="member")
    # A non-member role is untouched; a member role with an unexpected prefix is skipped.
    senator = await _role(db_session, source_id="senate-seat-role:1", role_type="state_senator")
    weird = await _role(db_session, source_id="mystery:1", role_type="member")

    result = await migrate_member_role_types(db_session)

    assert committee.role_type == "committee_member"
    assert party.role_type == "party_member"
    assert senator.role_type == "state_senator"  # not a member row, untouched
    assert weird.role_type == "member"  # unknown prefix, left alone
    assert result["checked"] == 3  # the three `member` rows (senator excluded)
    assert result["reclassified"] == {"committee_member": 1, "party_member": 1}
    assert result["reclassified_total"] == 2
    assert result["skipped_unknown_prefix"] == 1


async def test_idempotent_second_run_is_a_noop(db_session):
    await _role(db_session, source_id="committee-member-role:1", role_type="member")

    first = await migrate_member_role_types(db_session)
    assert first["reclassified_total"] == 1

    second = await migrate_member_role_types(db_session)
    assert second["checked"] == 0
    assert second["reclassified_total"] == 0
