"""Roster span→Assignment emission (#228 Phase B) — minted-member tenure.

Same generic emitter as the sponsor path, bound differently: Persons resolve from the
**roster** source space, Assignments carry the roster source, and one archived roster
edition attests every biennium of every span (the document IS the per-biennium evidence —
unlike the sponsor wires there is one edition, not one roster per biennium).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from clearinghouse_domain_legislative.span_kinds import KIND_PARTY, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import Observation, build_tenure_spans
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.emit import emit_roster_spans

CURRENT = "2025-26"


@pytest.fixture
async def anchors(db_session, usa_wa):
    return await bootstrap_synthetic_anchors(
        db_session, biennium=CURRENT, jurisdiction_id=usa_wa.id
    )


@pytest.fixture
async def roster_citation(db_session, usa_wa):
    source = Source(
        jurisdiction_id=usa_wa.id, name="Roster", slug=ROSTER_SOURCE_SLUG, kind="document"
    )
    db_session.add(source)
    await db_session.flush()
    event = FetchEvent(
        source_id=source.id,
        resource_id="legroster:2025-06-05",
        url="https://x/roster.pdf",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x01" * 32,
        status=FetchStatus.ok,
    )
    db_session.add(event)
    await db_session.flush()
    return (event.id, event.fetched_at, "legroster:2025-06-05")


async def test_emits_party_and_senate_assignments_for_a_minted_person(
    db_session, usa_wa, anchors, roster_citation
) -> None:
    """A People's Party senator of 1897: the minor-party Org anchor (#228 synthesis) and
    the Senate seat Role both bind, in the roster source space."""
    db_session.add(
        Jurisdiction(
            slug="usa-wa-ld-5",
            name="LD 5",
            type_id=usa_wa.type_id,
            pm_jurisdiction_id=_ULID(),
            recorded_at=datetime.now(UTC),
        )
    )
    db_session.add(
        Person(source=ROSTER_SOURCE_SLUG, source_id="werunner:1897", name_full="W. E. Runner")
    )
    await db_session.flush()
    spans = build_tenure_spans(
        [
            Observation("werunner:1897", KIND_SENATE, "5", "1897-98"),
            Observation("werunner:1897", KIND_PARTY, "peoples", "1897-98"),
        ],
        current_biennium=CURRENT,
    )
    emitted = await emit_roster_spans(
        db_session, spans, anchors=anchors, reliability=1.0, citation=roster_citation
    )
    assert emitted == 2
    assignments = (
        (
            await db_session.execute(
                select(Assignment).where(Assignment.source == ROSTER_SOURCE_SLUG)
            )
        )
        .scalars()
        .all()
    )
    assert len(assignments) == 2
    roles = {(await db_session.get(Role, a.role_id)).source_id for a in assignments}
    assert roles == {"party-role:peoples", "seat:senate:ld-5"}
    citations = (await db_session.execute(select(Citation))).scalars().all()
    assert len(citations) == 2  # one per Assignment — the single edition dedups per entity
    assert {c.entity_id for c in citations} == {a.id for a in assignments}
    assert all(c.fetch_event_id == roster_citation[0] for c in citations)


async def test_span_for_an_unknown_member_is_skipped(db_session, anchors, roster_citation) -> None:
    spans = build_tenure_spans(
        [Observation("nobody:1899", KIND_PARTY, "democratic", "1899-00")],
        current_biennium=CURRENT,
    )
    emitted = await emit_roster_spans(
        db_session, spans, anchors=anchors, reliability=1.0, citation=roster_citation
    )
    assert emitted == 0
