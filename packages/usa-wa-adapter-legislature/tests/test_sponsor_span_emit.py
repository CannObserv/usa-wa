"""Span→Assignment emission (#78 2b-ii): merged Assignment per span + per-biennium citations.

Resolve Person/Role, upsert one Assignment per tenure with the span's validity window, and
cite every biennium in range (idempotent re-assert). Person/Role that can't resolve → skip.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.sponsor_observations import build_sponsor_observations
from usa_wa_adapter_legislature.sponsor_span_emit import emit_sponsor_spans
from usa_wa_adapter_legislature.tenure_spans import build_tenure_spans

CURRENT = "2025-26"


@pytest.fixture
async def anchors(db_session, usa_wa):
    return await bootstrap_synthetic_anchors(
        db_session, biennium=CURRENT, jurisdiction_id=usa_wa.id
    )


async def _add_ld(session, usa_wa, n):
    session.add(
        Jurisdiction(
            slug=f"usa-wa-ld-{n}",
            name=f"LD {n}",
            type_id=usa_wa.type_id,
            pm_jurisdiction_id=_ULID(),
            recorded_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _add_person(session, member_id, name):
    row = Person(source="usa_wa_legislature", source_id=str(member_id), name_full=name)
    session.add(row)
    await session.flush()
    return row


async def _fetch_events(session, usa_wa, bienniums):
    """Create an archived sponsors FetchEvent per biennium; return the emission's map."""
    source = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
    session.add(source)
    await session.flush()
    out = {}
    for b in bienniums:
        ev = FetchEvent(
            source_id=source.id,
            resource_id=f"sponsors:{b}",
            url="https://x",
            fetched_at=datetime.now(UTC),
            http_status=200,
            content_hash=b"\x01" * 32,
            status=FetchStatus.ok,
        )
        session.add(ev)
        await session.flush()
        out[b] = (ev.id, ev.fetched_at)
    return out


def _member(mid, *, agency="Senate", district="5", party="D"):
    return {
        "Id": mid,
        "FirstName": "Ann",
        "LastName": "Rivers",
        "District": district,
        "Party": party,
        "Agency": agency,
        "Name": "Ann Rivers",
    }


async def _count(session, model, **where):
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalar()


async def test_emits_merged_open_assignment_with_per_biennium_citations(
    db_session, usa_wa, anchors
):
    await _add_ld(db_session, usa_wa, 5)
    person = await _add_person(db_session, 100, "Ann Rivers")
    fetch_events = await _fetch_events(db_session, usa_wa, ["2023-24", "2025-26"])
    obs = build_sponsor_observations({"2023-24": [_member(100)], "2025-26": [_member(100)]})
    spans = build_tenure_spans(obs, current_biennium=CURRENT)

    emitted = await emit_sponsor_spans(
        db_session, spans, anchors=anchors, reliability=1.0, fetch_events=fetch_events
    )

    assert emitted == 2  # party + Senate seat
    # The Senate seat span is one merged, open (current-reaching) row.
    seat = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2023-24")
        )
    ).scalar_one()
    assert seat.person_id == person.id
    assert seat.valid_from == date(2023, 1, 1)
    assert seat.valid_to is None and seat.is_active is True
    # Cite-every-biennium: one Citation per biennium in range (2023-24, 2025-26).
    assert await _count(db_session, Citation, entity_id=seat.id) == 2


async def test_reemission_is_idempotent(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100, "Ann Rivers")
    fetch_events = await _fetch_events(db_session, usa_wa, ["2023-24", "2025-26"])
    obs = build_sponsor_observations({"2023-24": [_member(100)], "2025-26": [_member(100)]})
    spans = build_tenure_spans(obs, current_biennium=CURRENT)

    await emit_sponsor_spans(
        db_session, spans, anchors=anchors, reliability=1.0, fetch_events=fetch_events
    )
    await emit_sponsor_spans(  # second pass — must converge, not pile up
        db_session, spans, anchors=anchors, reliability=1.0, fetch_events=fetch_events
    )
    seat = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2023-24")
        )
    ).scalar_one()  # exactly one row
    assert await _count(db_session, Citation, entity_id=seat.id) == 2  # not 4


async def test_person_absent_is_skipped(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 5)  # LD exists but the Person was never ingested
    fetch_events = await _fetch_events(db_session, usa_wa, ["2025-26"])
    spans = build_tenure_spans(
        build_sponsor_observations({"2025-26": [_member(100)]}), current_biennium=CURRENT
    )
    emitted = await emit_sponsor_spans(
        db_session, spans, anchors=anchors, reliability=1.0, fetch_events=fetch_events
    )
    assert emitted == 0
    assert await _count(db_session, Assignment) == 0


async def test_unsynced_ld_senate_span_is_skipped(db_session, usa_wa, anchors):
    # Person exists + party Org exists, but LD 5 isn't synced → the Senate seat span skips,
    # while the party span still emits.
    await _add_person(db_session, 100, "Ann Rivers")
    fetch_events = await _fetch_events(db_session, usa_wa, ["2025-26"])
    spans = build_tenure_spans(
        build_sponsor_observations({"2025-26": [_member(100)]}), current_biennium=CURRENT
    )
    emitted = await emit_sponsor_spans(
        db_session, spans, anchors=anchors, reliability=1.0, fetch_events=fetch_events
    )
    assert emitted == 1  # party only (Senate seat skipped: unsynced LD)
    role = (
        await db_session.execute(select(Role).join(Assignment, Assignment.role_id == Role.id))
    ).scalar_one()
    assert role.role_type == "party_member"  # the party Role (PM catalog slug, #110)


async def test_citations_are_append_only_across_a_fresh_fetch_event(db_session, usa_wa, anchors):
    """The daily current-biennium re-pull records a FRESH FetchEvent each run (#63/#65). Since
    citations key on the biennium (not the fetch_event id), a second emission with a new
    FetchEvent for the same biennium must NOT add a duplicate citation — append-only, one per
    covered biennium (the app role is REVOKEd DELETE on citations, #54)."""
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100, "Ann Rivers")
    source = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
    db_session.add(source)
    await db_session.flush()

    async def _fresh_event() -> tuple:
        ev = FetchEvent(
            source_id=source.id,
            resource_id=f"sponsors:{CURRENT}",
            url="https://x",
            fetched_at=datetime.now(UTC),
            http_status=200,
            content_hash=b"\x01" * 32,
            status=FetchStatus.ok,
        )
        db_session.add(ev)
        await db_session.flush()
        return (ev.id, ev.fetched_at)

    spans = build_tenure_spans(
        build_sponsor_observations({CURRENT: [_member(100)]}), current_biennium=CURRENT
    )

    # Day 1 — first FetchEvent for the biennium.
    await emit_sponsor_spans(
        db_session,
        spans,
        anchors=anchors,
        reliability=1.0,
        fetch_events={CURRENT: await _fresh_event()},
    )
    # Day 2 — a DIFFERENT FetchEvent for the SAME biennium (a byte-identical daily re-pull).
    await emit_sponsor_spans(
        db_session,
        spans,
        anchors=anchors,
        reliability=1.0,
        fetch_events={CURRENT: await _fresh_event()},
    )

    seat = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2025-26")
        )
    ).scalar_one()
    assert await _count(db_session, Citation, entity_id=seat.id) == 1  # one per biennium, not two
