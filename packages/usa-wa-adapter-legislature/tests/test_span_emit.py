"""Generic span emitter (#82) — the source-parameterization the PDC caller needs (#79).

The emitter is shared by three callers with different source semantics. Sponsor + committee
spans are wholly ``usa_wa_legislature`` (Person and Assignment). PDC House-position spans bind
a ``usa_wa_pdc`` Assignment onto a Person that WSL sourced — so the Person lookup source and
the Assignment source **differ**. These tests pin that split without disturbing the default
(both = ``usa_wa_legislature``) the existing callers rely on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from usa_wa_adapter_legislature.span_emit import close_stale_spans, emit_spans, resolve_person
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

CURRENT = "2025-26"


def _span(member_id="100", *, kind="chamber-house", disc="ld-5-position-1", start=CURRENT):
    return TenureSpan(
        member_id=member_id,
        kind=kind,
        discriminator=disc,
        start_biennium=start,
        end_biennium=CURRENT,
        valid_from=date(2025, 1, 1),
        valid_to=None,
        is_active=True,
    )


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(
        jurisdiction_id=usa_wa.id,
        name="WSL",
        slug="usa_wa_legislature",
        kind="soap",
        reliability=1.0,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _add_role(session, usa_wa):
    org = Organization(
        source="usa_wa_legislature",
        source_id="house",
        jurisdiction_id=usa_wa.id,
        name="House",
        short_name="House",
        org_type="chamber",
    )
    session.add(org)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id="seat:house:ld-5:position-1",
        organization_id=org.id,
        name="State Representative",
        role_type="state_representative",
    )
    session.add(role)
    await session.flush()
    return role


async def _fetch_event(session, source):
    ev = FetchEvent(
        source_id=source.id,
        resource_id="house-winners:2024",
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x01" * 32,
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    return ev


async def test_assignment_source_differs_from_person_lookup_source(db_session, usa_wa, wsl_source):
    """A PDC House span: resolve the WSL-sourced Person but write a usa_wa_pdc Assignment."""
    person = Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers")
    db_session.add(person)
    await db_session.flush()
    role = await _add_role(db_session, usa_wa)
    ev = await _fetch_event(db_session, wsl_source)

    async def _resolve_role(_session, _span):
        return role

    def _citation_target(_span, biennium):
        return (ev.id, ev.fetched_at, f"house-winners:{biennium[:4]}")

    emitted = await emit_spans(
        db_session,
        [_span()],
        resolve_role=_resolve_role,
        citation_target=_citation_target,
        reliability=1.0,
        person_source="usa_wa_legislature",
        assignment_source="usa_wa_pdc",
    )

    assert emitted == 1
    row = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.source_id == "100:chamber-house:ld-5-position-1:2025-26"
            )
        )
    ).scalar_one()
    assert row.source == "usa_wa_pdc"  # the Assignment is PDC-sourced
    assert row.person_id == person.id  # but bound to the WSL Person


async def test_person_resolved_under_the_given_person_source(db_session, usa_wa, wsl_source):
    """resolve_person honours an explicit person_source distinct from the assignment source."""
    person = Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers")
    db_session.add(person)
    await db_session.flush()

    found = await resolve_person(db_session, "100", source="usa_wa_legislature")
    assert found is not None and found.id == person.id
    # a PDC-sourced lookup would miss the WSL person (no such row) — proving the param bites
    assert await resolve_person(db_session, "100", source="usa_wa_pdc") is None


async def test_default_source_is_legislature_for_existing_callers(db_session, usa_wa, wsl_source):
    """Omitting the source params keeps both = usa_wa_legislature (sponsor/committee behaviour)."""
    person = Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers")
    db_session.add(person)
    await db_session.flush()
    role = await _add_role(db_session, usa_wa)
    ev = await _fetch_event(db_session, wsl_source)

    async def _resolve_role(_session, _span):
        return role

    def _citation_target(_span, biennium):
        return (ev.id, ev.fetched_at, "sponsors:2025-26")

    emitted = await emit_spans(
        db_session,
        [_span(kind="party", disc="democratic")],
        resolve_role=_resolve_role,
        citation_target=_citation_target,
        reliability=1.0,
    )

    assert emitted == 1
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source == "usa_wa_legislature")
        )
    ).scalar_one()
    assert row.source_id == "100:party:democratic:2025-26"
    assert (
        (await db_session.execute(select(Citation).where(Citation.entity_id == row.id)))
        .scalars()
        .all()
    )  # cited


# --- close_stale_spans (#83) -------------------------------------------------------------


async def _open_assignment(session, usa_wa, source_id, *, source="usa_wa_legislature", frm=None):
    """An open (is_active, valid_to NULL) Assignment row, as a prior daily run left it."""
    person = Person(source="usa_wa_legislature", source_id=source_id.split(":")[0], name_full="M")
    session.add(person)
    await session.flush()
    role = (
        await session.execute(select(Role).where(Role.source_id == "seat:house:ld-5:position-1"))
    ).scalar_one_or_none() or await _add_role(session, usa_wa)
    row = Assignment(
        source=source,
        source_id=source_id,
        person_id=person.id,
        role_id=role.id,
        valid_from=frm or date(2021, 1, 1),
        valid_to=None,
        is_active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def test_close_stale_spans_closes_unasserted_open_row(db_session, usa_wa):
    """An open span the rebuild no longer asserts (departed member) closes at the end of the
    biennium before the current one (#83)."""
    row = await _open_assignment(db_session, usa_wa, "100:party:democratic:2021-22")

    closed = await close_stale_spans(
        db_session,
        assignment_source="usa_wa_legislature",
        kinds={"party", "chamber-senate"},
        asserted_source_ids={"200:party:democratic:2021-22"},
        current_biennium="2027-28",
    )

    assert closed == 1
    assert row.is_active is False
    assert row.valid_to == date(2026, 12, 31)


async def test_close_stale_spans_leaves_asserted_closed_other_kind_and_other_source(
    db_session, usa_wa
):
    """Selectivity: asserted rows, already-closed rows, foreign kinds, and foreign sources
    are all untouched — as is a malformed (non-4-part) legacy source_id."""
    asserted = await _open_assignment(db_session, usa_wa, "100:party:democratic:2021-22")
    already_closed = await _open_assignment(db_session, usa_wa, "300:party:republican:2019-20")
    already_closed.is_active = False
    already_closed.valid_to = date(2020, 12, 31)
    other_kind = await _open_assignment(db_session, usa_wa, "400:committee:31635:2021-22")
    other_source = await _open_assignment(
        db_session, usa_wa, "500:party:democratic:2021-22", source="usa_wa_pdc"
    )
    legacy_3part = await _open_assignment(db_session, usa_wa, "600:party:2021-22")
    await db_session.flush()

    closed = await close_stale_spans(
        db_session,
        assignment_source="usa_wa_legislature",
        kinds={"party", "chamber-senate"},
        asserted_source_ids={"100:party:democratic:2021-22"},
        current_biennium="2027-28",
    )

    assert closed == 0
    assert asserted.is_active is True and asserted.valid_to is None
    assert already_closed.valid_to == date(2020, 12, 31)
    assert other_kind.is_active is True
    assert other_source.is_active is True
    assert legacy_3part.is_active is True


async def test_close_stale_spans_clamps_valid_to_at_valid_from(db_session, usa_wa):
    """A span that started in the current biennium and vanished (superseded-wire orphan)
    closes at its own valid_from, never before it."""
    row = await _open_assignment(
        db_session, usa_wa, "100:party:democratic:2027-28", frm=date(2027, 1, 1)
    )

    closed = await close_stale_spans(
        db_session,
        assignment_source="usa_wa_legislature",
        kinds={"party"},
        asserted_source_ids={"other:party:democratic:2027-28"},
        current_biennium="2027-28",
    )

    assert closed == 1
    assert row.valid_to == date(2027, 1, 1)  # clamped to valid_from, not 2026-12-31


async def test_close_stale_spans_empty_assertion_set_is_a_guarded_noop(db_session, usa_wa):
    """An empty asserted set means the rebuild saw nothing — an anomaly that must not read
    as mass departure. The sweep declines to close anything."""
    row = await _open_assignment(db_session, usa_wa, "100:party:democratic:2021-22")

    closed = await close_stale_spans(
        db_session,
        assignment_source="usa_wa_legislature",
        kinds={"party"},
        asserted_source_ids=set(),
        current_biennium="2027-28",
    )

    assert closed == 0
    assert row.is_active is True


async def test_close_stale_spans_is_idempotent(db_session, usa_wa):
    await _open_assignment(db_session, usa_wa, "100:party:democratic:2021-22")
    kwargs = dict(
        assignment_source="usa_wa_legislature",
        kinds={"party"},
        asserted_source_ids={"200:party:democratic:2021-22"},
        current_biennium="2027-28",
    )

    assert await close_stale_spans(db_session, **kwargs) == 1
    assert await close_stale_spans(db_session, **kwargs) == 0  # already closed — nothing left
