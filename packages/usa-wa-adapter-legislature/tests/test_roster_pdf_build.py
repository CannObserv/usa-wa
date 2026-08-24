"""The #228 Phase B builder — archive → identities → Persons + spans, oracle-gated.

Runs against the real D2 fixture archived as the roster edition: the parse, identity
resolution, minting, projection and emission all exercise production code paths; only the
WSL seating index is empty (no sponsor archive here), so crossing folds refuse their join
and the build proceeds on the minted majority — which is itself the oracle's point: a
refusal is a tallied outcome, never an abort.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload
from clearinghouse_domain_legislative.identity import Assignment, Person
from clearinghouse_domain_legislative.span_emit import ASSIGNMENT_CITATION_TYPE, span_key_parts
from usa_wa_adapter_legislature.operators.store import (
    get_or_create_operator_source,
    record_operator_event,
)
from usa_wa_adapter_legislature.roster_pdf.adapter import ROSTER_RESOURCE_PREFIX
from usa_wa_adapter_legislature.roster_pdf.build import (
    OracleViolation,
    build_pre1991,
    verify_pre1991,
)
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_MINTED,
    RosterIdentity,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source

CURRENT = "2025-26"


@pytest.fixture
async def archived_roster(db_session, usa_wa, roster_pdf_bytes):
    source = await get_or_create_roster_source(db_session, usa_wa)
    event = FetchEvent(
        source_id=source.id,
        resource_id=f"{ROSTER_RESOURCE_PREFIX}2025-06-05",
        url="https://x/roster.pdf",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x03" * 32,
        status=FetchStatus.ok,
    )
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        RawPayload(
            fetch_event_id=event.id,
            content_type="application/pdf",
            body=roster_pdf_bytes,
            size_bytes=len(roster_pdf_bytes),
        )
    )
    await db_session.flush()
    return source


async def test_build_mints_persons_and_emits_spans(db_session, usa_wa, archived_roster) -> None:
    summary = await build_pre1991(db_session, current_biennium=CURRENT)

    assert summary.persons_created > 0
    persons = (
        await db_session.execute(
            select(func.count()).select_from(Person).where(Person.source == ROSTER_SOURCE_SLUG)
        )
    ).scalar()
    assert persons == summary.persons_created
    assignments = (
        await db_session.execute(
            select(func.count())
            .select_from(Assignment)
            .where(Assignment.source == ROSTER_SOURCE_SLUG)
        )
    ).scalar()
    assert assignments == summary.assignments_emitted > 0
    # every emitted span is pre-1991 and closed — nothing the roster asserts is open
    open_count = (
        await db_session.execute(
            select(func.count())
            .select_from(Assignment)
            .where(Assignment.source == ROSTER_SOURCE_SLUG, Assignment.is_active.is_(True))
        )
    ).scalar()
    assert open_count == 0


async def test_build_is_idempotent(db_session, usa_wa, archived_roster) -> None:
    first = await build_pre1991(db_session, current_biennium=CURRENT)
    second = await build_pre1991(db_session, current_biennium=CURRENT)
    assert second.persons_created == 0
    assert second.persons_existing == first.persons_created
    assert second.assignments_emitted == first.assignments_emitted  # upserts, not duplicates


def _rec(name: str, year: int, **kw) -> RosterRecord:
    defaults = dict(district=1, chamber="senate", order=1, party_token="D", annotation=None)
    defaults.update(kw)
    return RosterRecord(year=year, name=name, page_number=1, **defaults)


def test_oracle_rejects_person_side_senate_simultaneity() -> None:
    """Oracle item 3, the person side: one member covering two Senate seats in one session
    year is corrupt data, and nothing downstream checks it — abort, with subjects."""
    identity = RosterIdentity(
        disposition=IDENTITY_MINTED,
        fold="xdouble",
        key="xdouble:1901",
        wsl_member_id=None,
        records=(
            _rec("X. Double", 1901, district=1),
            _rec("X. Double", 1901, district=2),
        ),
    )
    with pytest.raises(OracleViolation, match="xdouble"):
        verify_pre1991([identity], [r for r in identity.records])


async def test_build_retires_a_span_a_rederivation_no_longer_asserts(
    db_session, usa_wa, archived_roster
) -> None:
    """CR #86 at the build seam: a roster Assignment left over from an earlier derivation
    (here, a hand-planted row under a key no identity produces) is soft-deleted, and the
    count is reported. Idempotent — a second run finds nothing left to retire."""
    await build_pre1991(db_session, current_biennium=CURRENT)
    live = (
        (
            await db_session.execute(
                select(Assignment).where(
                    Assignment.source == ROSTER_SOURCE_SLUG, Assignment.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    stranded = Assignment(
        source=ROSTER_SOURCE_SLUG,
        source_id="ghostmember:party:republican:1899-00",
        person_id=live[0].person_id,
        role_id=live[0].role_id,
        valid_from=date(1899, 1, 1),
        valid_to=date(1900, 12, 31),
        is_active=False,
    )
    db_session.add(stranded)
    await db_session.flush()

    summary = await build_pre1991(db_session, current_biennium=CURRENT)

    assert summary.spans_retired == 1
    assert stranded.deleted_at is not None
    again = await build_pre1991(db_session, current_biennium=CURRENT)
    assert again.spans_retired == 0


async def test_summary_reports_the_deepening_sweep_and_the_full_residue(
    db_session, usa_wa, archived_roster
) -> None:
    """CR #84/#87: the deepening build's stale-sweep outcome reaches the summary (an
    aborted #83 guard must not exit 0 silently), and every tallied residue reaches
    ``counters`` — the durable #178 ledger record, not just a log line."""
    summary = await build_pre1991(db_session, current_biennium=CURRENT)

    assert summary.spans_closed == 0
    assert summary.sweep_aborted is False
    for key in (
        "records_pre1991",
        "persons_created",
        "persons_existing",
        "persons_renamed",
        "assignments_emitted",
        "deepened_emitted",
        "spans_closed",
        "spans_retired",
        "declined_parties",
        "uncovered_rows",
        "seat_overlaps",
    ):
        assert key in summary.counters, key
    # refusal reasons are data, not a fixed vocabulary — each one gets its own counter.
    # Guarded (CR #99): whether this fixture refuses anything is not this test's subject.
    for reason, count in summary.refusals.items():
        assert summary.counters[f"refusals_{reason}"] == count


async def test_an_operator_event_dates_a_roster_span(db_session, usa_wa, archived_roster) -> None:
    """#226's other half. The backfill resolves 238 dated boundaries and writes them to the
    operator store, but only the sponsor/SOS-house/committee builders ever applied the
    overlay — and every pre-1991 span belongs to *this* builder. Without this step the
    boundaries are correct, provenanced, and inert: a `vacated` on a 1923 Senate seat leaves
    the span ending at its biennium floor exactly as before.

    Both halves are asserted (CR 30): the boundary moving, and the **field-level citation**
    that makes the moved boundary auditable under #54. Asserting only the date left the
    citation call site untested — deleting it outright kept the suite green.
    """
    first = await build_pre1991(db_session, current_biennium=CURRENT)
    assert first.assignments_emitted > 0
    assert first.operator_cites == 0, "no events on record yet, so nothing to cite"

    # Deterministic subject (CR 31): the earliest-keyed span whose window is wide enough to
    # hold a boundary strictly inside it. Taking whichever span sorts first and assuming a
    # fixed mid-year date lands in its window couples the test to the fixture's ordering — a
    # late-starting single-year tenure would clamp in ``_close`` and fail for a reason that
    # has nothing to do with the overlay.
    spans = (
        (
            await db_session.execute(
                select(Assignment)
                .join(Person, Person.id == Assignment.person_id)
                .where(Assignment.source == ROSTER_SOURCE_SLUG)
                .order_by(Assignment.source_id)
            )
        )
        .scalars()
        .all()
    )
    span = next(
        s for s in spans if s.valid_to is not None and (s.valid_to - s.valid_from).days > 365
    )
    member_id, kind, discriminator, _biennium = span_key_parts(span.source_id)
    # A resignation partway through the tenure — the fact the roster states and the biennium
    # floor cannot express. Derived from the span's own window, so it is inside by construction.
    mid = span.valid_from + (span.valid_to - span.valid_from) / 2
    assert span.valid_from < mid < span.valid_to
    source = await get_or_create_operator_source(db_session, usa_wa)
    await record_operator_event(
        db_session,
        source,
        member_id=member_id,
        kind="vacated",
        reason="resigned",
        effective_date=mid,
        evidence_url="https://x/roster.pdf#page=7",
        seat_kind=kind,
        seat_discriminator=discriminator,
        entered_by="test",
    )
    await db_session.flush()

    second = await build_pre1991(db_session, current_biennium=CURRENT)

    await db_session.refresh(span)
    assert span.valid_to == mid, "the operator boundary must beat the biennium floor"
    assert second.operator_events_loaded >= 1
    # The applied signal (CR 29): ``operator_events_loaded`` only proves the event was read.
    assert second.operator_cites >= 1
    cited = (
        (
            await db_session.execute(
                select(Citation).where(
                    Citation.entity_type == ASSIGNMENT_CITATION_TYPE,
                    Citation.entity_id == span.id,
                    Citation.field_path == "valid_to",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(cited) == 1, "the moved end date must cite the operator attestation that set it"
