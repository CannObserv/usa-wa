"""C4 committee lineage coherence invariants (usa-wa#124)."""

import json
from datetime import date
from unittest.mock import patch

from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from clearinghouse_domain_legislative.identity import Assignment, Organization, Role
from usa_wa_adapter_legislature.committees import lineage_invariants as inv_module
from usa_wa_adapter_legislature.committees.lineage_invariants import (
    LineageInvariantResult,
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


async def test_inv_completes_on_a_lineage_cycle(db_session, usa_wa):
    """#126: a round-trip 2-cycle (924 ⇄ 966) must not hang or error the C4 check — it is
    a flat set query, so cycles are inert. Regression: a future naive graph-walk here
    would loop and fail this test."""
    await _committee(db_session, "924", active=False)
    await _committee(db_session, "966", active=False)
    await _link(db_session, "924", "966", slug="succeeded_by")
    await _link(db_session, "966", "924", slug="succeeded_by")  # the cycle
    result = await check_committee_lineage_invariants(db_session)
    assert result.ok  # both predecessors inactive; the cycle is coherent


# --- CLI (#179b: the shared job harness) --------------------------------------


def test_main_exit_zero_when_coherent(monkeypatch, capsys):
    patch_job_runtime(monkeypatch)

    async def _clean(_session):
        return LineageInvariantResult()

    with patch.object(inv_module, "check_committee_lineage_invariants", _clean):
        code = inv_module.main(["--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["job"] == inv_module.JOB_SLUG
    assert payload["outcome"] == "ok"


def test_main_exit_one_on_a_violation(monkeypatch, capsys):
    """Unchanged daily contract: exit 1 on drift is what OnFailure= emails on."""
    patch_job_runtime(monkeypatch)

    async def _violating(_session):
        result = LineageInvariantResult()
        result.inactive_with_live_members = ["924"]
        return result

    with patch.object(inv_module, "check_committee_lineage_invariants", _violating):
        code = inv_module.main(["--json"])

    assert code == 1
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["outcome"] == "failed"
    assert payload["counters"]["inactive_with_live_members"] == ["924"]
