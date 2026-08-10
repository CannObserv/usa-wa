"""Senate odd-year ballot corroboration + citation (#123 §2).

The SOS Senate consumers of the odd-year ``senate_winners()`` cohort: 2a field-cites an elected
senator's open span, 2b asserts no odd-year winner is missing an open seat (a silent missing
operator event). Fully offline — an archived results wire + hand-built open Senate seats.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from unittest.mock import patch

from sqlalchemy import func, select

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload
from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.normalize.members import senate_seat_role_source_id
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_common.jurisdiction import resolve_jurisdiction
from usa_wa_facts_seats import senate_corroboration as corroboration_module
from usa_wa_facts_seats.senate_corroboration import (
    SenateCorroborationResult,
    corroborate_senate_winners,
)

CURRENT = "2025-26"
ODD_RESOURCE = "sos-legresults:20251104"


async def _archive(session, source, resource_id, body):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes(32),
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(RawPayload(fetch_event_id=ev.id, content_type="x", body=body, size_bytes=len(body)))
    await session.flush()
    return ev


def _senate_csv(*rows):
    """``(ld, candidate, party, votes)`` → a Senate-only legislative-results CSV."""
    header = '"Race","Candidate","Party","Votes"\r\n'
    body = "".join(
        f'"LEGISLATIVE DISTRICT {ld} - State Senator","{name}","{party}",{votes}\r\n'
        for ld, name, party, votes in rows
    )
    return (header + body).encode()


async def _open_senate_seat(session, anchors, jurisdiction, *, ld, person_name, member_id):
    person = Person(source="usa_wa_legislature", source_id=str(member_id), name_full=person_name)
    session.add(person)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id=senate_seat_role_source_id(ld),
        organization_id=anchors.senate_id,
        name="State Senator",
        role_type="state_senator",
        jurisdiction_id=None,
        qualifier=None,
    )
    session.add(role)
    await session.flush()
    assignment = Assignment(
        source="usa_wa_legislature",
        source_id=f"{member_id}:chamber-senate:{ld}:{CURRENT}",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2025, 6, 3),
        valid_to=None,
        is_active=True,
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def _setup(session, usa_wa):
    jurisdiction = await resolve_jurisdiction(session)
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=CURRENT, jurisdiction_id=jurisdiction.id
    )
    sos = await get_or_create_results_source(session, jurisdiction)
    return jurisdiction, anchors, sos


async def test_elected_senator_open_span_gets_a_valid_from_field_citation(db_session, usa_wa):
    """2a: Hunt won the LD5 special (Nov 2025); her open Senate span carries a ``valid_from`` field
    citation to the odd wire — the ballot attestation the appointment-dated boundary lacked."""
    jurisdiction, anchors, sos = await _setup(db_session, usa_wa)
    assignment = await _open_senate_seat(
        db_session, anchors, jurisdiction, ld=5, person_name="Victoria Hunt", member_id=35410
    )
    await _archive(
        db_session,
        sos,
        ODD_RESOURCE,
        _senate_csv((5, "Victoria Hunt", "(Prefers Democratic Party)", "28466")),
    )

    result = await corroborate_senate_winners(db_session, biennium=CURRENT)

    assert result.odd_year == 2025 and result.winners == 1
    assert result.citations_added == 1 and result.ok
    field_cites = await db_session.scalar(
        select(func.count())
        .select_from(Citation)
        .where(Citation.entity_id == assignment.id, Citation.field_path == "valid_from")
    )
    assert field_cites == 1


async def test_missing_operator_event_is_a_violation(db_session, usa_wa):
    """2b (the higher-value half): an odd-year winner with no open Senate seat at that LD is a
    silent missing operator ``seated`` event → named + gate fails (exit 1)."""
    _jurisdiction, _anchors, sos = await _setup(db_session, usa_wa)
    # A winner archived, but NO open Senate seat exists for LD5.
    await _archive(
        db_session,
        sos,
        ODD_RESOURCE,
        _senate_csv((5, "Victoria Hunt", "(Prefers Democratic Party)", "28466")),
    )

    result = await corroborate_senate_winners(db_session, biennium=CURRENT)

    assert result.missing_lds == [5] and not result.ok
    assert result.citations_added == 0


async def test_citation_is_idempotent_across_reruns(db_session, usa_wa):
    jurisdiction, anchors, sos = await _setup(db_session, usa_wa)
    await _open_senate_seat(
        db_session, anchors, jurisdiction, ld=5, person_name="Victoria Hunt", member_id=35410
    )
    await _archive(
        db_session,
        sos,
        ODD_RESOURCE,
        _senate_csv((5, "Victoria Hunt", "(Prefers Democratic Party)", "28466")),
    )

    first = await corroborate_senate_winners(db_session, biennium=CURRENT)
    second = await corroborate_senate_winners(db_session, biennium=CURRENT)

    assert first.citations_added == 1
    assert second.citations_added == 0  # dedup on (entity, field, resource)


async def test_mismatched_occupant_is_reported_not_cited(db_session, usa_wa):
    """A seat held by someone other than the ballot winner (a name change or a missing
    succession) is NOT a missing-LD violation (the seat is occupied) but is surfaced as
    ``mismatched`` and never cited to the wrong person."""
    jurisdiction, anchors, sos = await _setup(db_session, usa_wa)
    await _open_senate_seat(
        db_session, anchors, jurisdiction, ld=5, person_name="Mark Mullet", member_id=999
    )
    await _archive(
        db_session,
        sos,
        ODD_RESOURCE,
        _senate_csv((5, "Victoria Hunt", "(Prefers Democratic Party)", "28466")),
    )

    result = await corroborate_senate_winners(db_session, biennium=CURRENT)

    assert result.mismatched_lds == [5]
    assert result.citations_added == 0
    assert result.ok  # the seat IS occupied — not a missing-event violation


async def test_no_odd_cohort_is_a_clean_no_op(db_session, usa_wa):
    """Before the odd November (or a race-less biennium) there is no archived odd cohort — a clean
    zero-winner pass, never a false violation."""
    await _setup(db_session, usa_wa)
    result = await corroborate_senate_winners(db_session, biennium=CURRENT)
    assert result.winners == 0 and result.ok and result.missing_lds == []


async def test_noncurrent_biennium_pin_warns(db_session, usa_wa, caplog):
    """A biennium that differs from the date-current one (a stale $USA_WA_BIENNIUM / --biennium
    pin) logs a non-current WARNING breadcrumb — the parity with the WSL/PDC/SOS refreshes (#123
    CR). A current-biennium run stays quiet."""
    await _setup(db_session, usa_wa)
    with caplog.at_level(logging.WARNING):
        await corroborate_senate_winners(db_session, biennium="2019-20")
    assert "senate_corroboration_noncurrent_biennium" in [r.message for r in caplog.records]

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await corroborate_senate_winners(db_session, biennium=CURRENT)  # today's biennium
    assert "senate_corroboration_noncurrent_biennium" not in [r.message for r in caplog.records]


# --- CLI (#179b: the shared job harness) --------------------------------------


def test_main_commits_the_citations_even_on_a_violation(monkeypatch, capsys):
    """This gate **writes** (Citations) and then exits 1 on a missing winner. The commit
    was unconditional-unless-dry-run before #179b, so the handler keeps the transaction
    (``commit=False``) — a ``JobResult.failed`` under a committing harness would roll the
    citations back behind an unchanged exit code."""
    recording = patch_job_runtime(monkeypatch)

    async def _missing(_session, **_kwargs):
        return SenateCorroborationResult(
            odd_year=2025, winners=1, citations_added=1, missing_lds=[5]
        )

    with patch.object(corroboration_module, "corroborate_senate_winners", _missing):
        code = corroboration_module.main(["--json"])

    assert code == 1
    assert (recording.committed, recording.rolled_back) == (1, 0)
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["job"] == corroboration_module.JOB_SLUG
    assert payload["outcome"] == "failed"
    assert payload["counters"]["citations_added"] == 1


def test_main_dry_run_rolls_back(monkeypatch):
    recording = patch_job_runtime(monkeypatch)

    async def _clean(_session, **_kwargs):
        return SenateCorroborationResult(odd_year=2025, winners=1, citations_added=1)

    with patch.object(corroboration_module, "corroborate_senate_winners", _clean):
        assert corroboration_module.main(["--dry-run"]) == 0

    assert (recording.committed, recording.rolled_back) == (0, 1)
