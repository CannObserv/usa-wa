"""Migration (#78-3): collapse pre-#78 per-biennium sponsor Assignments into merged spans.

The legacy per-biennium party/Senate rows (each carrying a ``pm_assignment_id``) are retired
onto the span that shares their ``(person_id, role_id)`` — the anchor moves to the span so
the local cache holds ONE row per PM assignment. chamber-house (PDC) + committee rows are
left untouched; a legacy row with no successor span is left + counted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person
from usa_wa_adapter_legislature.harvest_sponsor_spans import build_sponsor_spans
from usa_wa_adapter_legislature.migrate_sponsor_spans import migrate_sponsor_spans

CURRENT = "2025-26"


class _FakeSponsorClient:
    def __init__(self, roster):
        self._roster = roster

    async def parse_sponsors(self, wire):
        return self._roster

    async def fetch_sponsors(self, biennium):
        raise AssertionError("archive-first — no live pull")


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


async def _archive(db_session, source, biennium):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=f"sponsors:{biennium}",
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes([hash(biennium) & 0xFF]) * 32,
        status=FetchStatus.ok,
    )
    db_session.add(ev)
    await db_session.flush()
    db_session.add(
        RawPayload(fetch_event_id=ev.id, content_type="text/xml", body=b"<r/>", size_bytes=4)
    )
    await db_session.flush()
    return ev.id


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


async def _spans_for(session, person_id):
    """The person's built span Assignments, keyed by dimension (source_id part[1])."""
    rows = (
        (await session.execute(select(Assignment).where(Assignment.person_id == person_id)))
        .scalars()
        .all()
    )
    return {a.source_id.split(":")[1]: a for a in rows if len(a.source_id.split(":")) == 4}


async def _add_legacy(session, *, source_id, person_id, role_id, pm_id, fetch_event_id=None):
    row = Assignment(
        source="usa_wa_legislature",
        source_id=source_id,
        person_id=person_id,
        role_id=role_id,
        valid_from=date(2025, 1, 1),
        valid_to=None,
        is_active=True,
        pm_assignment_id=pm_id,
    )
    session.add(row)
    await session.flush()
    if fetch_event_id is not None:
        session.add(
            Citation(
                entity_type="assignment",
                entity_id=row.id,
                fetch_event_id=fetch_event_id,
                asserted_at=datetime.now(UTC),
            )
        )
        await session.flush()
    return row


async def _count(session, model, **where):
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalar()


async def _setup_person_and_spans(db_session, usa_wa, wsl_source):
    """Person 100 (Senate, D, LD5) with archived rosters + built party/Senate spans."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2023-24")
    fe_id = await _archive(db_session, wsl_source, "2025-26")
    await build_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )
    person = (
        await db_session.execute(select(Person).where(Person.source_id == "100"))
    ).scalar_one()
    return person, await _spans_for(db_session, person.id), fe_id


async def test_legacy_rows_collapse_and_transfer_anchor(db_session, usa_wa, wsl_source):
    person, spans, fe_id = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    pm_party, pm_senate = _ULID(), _ULID()
    party_legacy = await _add_legacy(
        db_session,
        source_id="100:party:2025-26",
        person_id=person.id,
        role_id=spans["party"].role_id,
        pm_id=pm_party,
        fetch_event_id=fe_id,
    )
    await _add_legacy(
        db_session,
        source_id="100:chamber-senate:2025-26",
        person_id=person.id,
        role_id=spans["chamber-senate"].role_id,
        pm_id=pm_senate,
        fetch_event_id=fe_id,
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert (result.legacy_found, result.anchors_transferred, result.legacy_retired) == (2, 2, 2)
    assert result.orphans_no_span == 0
    # legacy rows are gone
    assert await _count(db_session, Assignment, source_id="100:party:2025-26") == 0
    assert await _count(db_session, Assignment, source_id="100:chamber-senate:2025-26") == 0
    # the spans now carry the transferred PM anchors
    party_span = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:party:democratic:2023-24")
        )
    ).scalar_one()
    senate_span = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2023-24")
        )
    ).scalar_one()
    assert party_span.pm_assignment_id == pm_party
    assert senate_span.pm_assignment_id == pm_senate
    # exactly one live assignment per PM anchor (the descriptor's local_match invariant)
    assert await _count(db_session, Assignment, pm_assignment_id=pm_party) == 1
    assert await _count(db_session, Assignment, pm_assignment_id=pm_senate) == 1
    # the retired legacy row's citations are cleaned (no dangling entity_id)
    assert await _count(db_session, Citation, entity_id=party_legacy.id) == 0


async def test_chamber_house_and_committee_rows_untouched(db_session, usa_wa, wsl_source):
    """chamber-house (PDC/#69) + committee (#82) per-biennium rows are NOT sponsor spans."""
    person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    # Selector keys on source_id, not role — reuse an existing role FK for both rows.
    role_id = spans["party"].role_id
    await _add_legacy(
        db_session,
        source_id="100:chamber-house:2025-26",
        person_id=person.id,
        role_id=role_id,
        pm_id=_ULID(),
    )
    await _add_legacy(
        db_session,
        source_id="100:committee:31635:2025-26",
        person_id=person.id,
        role_id=role_id,
        pm_id=_ULID(),
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert result.legacy_found == 0  # neither dim is a sponsor-span legacy row
    assert await _count(db_session, Assignment, source_id="100:chamber-house:2025-26") == 1
    assert await _count(db_session, Assignment, source_id="100:committee:31635:2025-26") == 1


async def test_orphan_legacy_row_with_no_successor_is_left(db_session, usa_wa, wsl_source):
    """A legacy row whose (person, role) has no built span is left in place + counted."""
    _person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    # A DIFFERENT person, absent from the roster → no span is built for them.
    ghost = Person(source="usa_wa_legislature", source_id="999", name_full="Departed Member")
    db_session.add(ghost)
    await db_session.flush()
    await _add_legacy(
        db_session,
        source_id="999:party:2025-26",
        person_id=ghost.id,
        role_id=spans["party"].role_id,
        pm_id=_ULID(),
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert result.legacy_found == 1
    assert result.orphans_no_span == 1
    assert result.legacy_retired == 0
    assert await _count(db_session, Assignment, source_id="999:party:2025-26") == 1  # left alone


async def test_migration_is_idempotent(db_session, usa_wa, wsl_source):
    person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    await _add_legacy(
        db_session,
        source_id="100:party:2025-26",
        person_id=person.id,
        role_id=spans["party"].role_id,
        pm_id=_ULID(),
    )

    first = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )
    second = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert first.legacy_retired == 1
    assert second.legacy_found == 0  # nothing left to migrate on the second pass
    assert second.anchors_transferred == 0
