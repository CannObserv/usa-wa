"""C4 committee lineage coherence invariants (usa-wa#124)."""

from datetime import date

from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from clearinghouse_domain_legislative.identity import Assignment, Organization, Role
from usa_wa_adapter_legislature.committee_lineage_invariants import (
    check_committee_lineage_invariants,
)


async def _committee(session, source_id, *, active=True):
    org = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=f"C{source_id}",
        org_type="committee",
        active=active,
    )
    session.add(org)
    await session.flush()
    return org


async def _live_member(session, org):
    role = Role(
        source="usa_wa_legislature",
        source_id=f"member:{org.source_id}",
        organization_id=org.id,
        name="member",
        role_type="committee_member",
    )
    session.add(role)
    await session.flush()
    session.add(
        Assignment(
            source="usa_wa_legislature",
            source_id=f"a:{org.source_id}",
            role_id=role.id,
            holder_name_raw="A Member",
            valid_from=date(2001, 1, 1),
            is_active=True,
        )
    )
    await session.flush()


async def _link(session, subject, linked, slug="succeeded_by"):
    row = CommitteeSuccessionEvent(
        source="usa_wa_operator",
        source_id=f"{slug}:{subject}:{linked}",
        subject_source_id=subject,
        linked_source_id=linked,
        slug=slug,
        evidence_url="https://x",
    )
    session.add(row)
    await session.flush()
    return row


async def test_clean_state_passes(db_session, usa_wa):
    await _committee(db_session, "28244", active=True)  # current head, no dead members
    old = await _committee(db_session, "14294", active=False)  # retired, no live members
    await _link(db_session, "14294", "28244")  # predecessor inactive → ok
    result = await check_committee_lineage_invariants(db_session)
    assert result.ok
    assert old.active is False


async def test_inv1_inactive_committee_with_live_member_flagged(db_session, usa_wa):
    old = await _committee(db_session, "14294", active=False)
    await _live_member(db_session, old)  # a live member on a dissolved committee
    result = await check_committee_lineage_invariants(db_session)
    assert not result.ok
    assert result.inactive_with_live_members == [("14294", 1)]


async def test_inv2_active_predecessor_flagged(db_session, usa_wa):
    await _committee(db_session, "28244", active=True)
    await _committee(db_session, "14294", active=True)  # still active but was succeeded
    await _link(db_session, "14294", "28244", slug="succeeded_by")
    result = await check_committee_lineage_invariants(db_session)
    assert not result.ok
    assert result.active_predecessors == ["14294"]


async def test_inv2_split_child_may_stay_active(db_session, usa_wa):
    """split_from does not retire its subject — a split's live child is permitted (OQ3)."""
    await _committee(db_session, "parent", active=True)
    await _committee(db_session, "child", active=True)
    await _link(db_session, "child", "parent", slug="split_from")
    result = await check_committee_lineage_invariants(db_session)
    assert result.ok


async def test_inv2_superseded_link_ignored(db_session, usa_wa):
    """A superseded (corrected-away) retiring link does not constrain its subject."""
    await _committee(db_session, "14294", active=True)
    await _committee(db_session, "99999", active=True)
    stale = await _link(db_session, "14294", "99999", slug="succeeded_by")
    # The operator superseded the wrong-successor link (no current retiring link remains).
    corrected = await _link(db_session, "14294", "99999", slug="split_from")
    stale.superseded_by_id = corrected.id
    await db_session.flush()
    result = await check_committee_lineage_invariants(db_session)
    assert result.ok  # only the superseded succeeded_by pointed at 14294 as predecessor
