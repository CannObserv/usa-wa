"""Provenance-model tests.

Citation is polymorphic: it points at any canonical entity by
``(entity_type, entity_id)`` without a DB-level FK on ``entity_id``. This test
exercises that pattern using a Source row as the cited entity — concrete
canonical entities live in domain packages and aren't loaded yet.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_core.provenance import (
    Citation,
    FetchEvent,
    FetchStatus,
    Jurisdiction,
    JurisdictionLevel,
    RawPayload,
    Source,
)


@pytest.fixture
async def seeded(db_session):
    """A Jurisdiction + Source + FetchEvent + RawPayload chain ready for citation tests."""
    jurisdiction = Jurisdiction(
        slug="usa-wa", name="Washington State", level=JurisdictionLevel.state
    )
    db_session.add(jurisdiction)
    await db_session.flush()

    source = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA Legislature SOAP",
        slug="usa_wa_legislature",
        kind="soap",
        base_url="https://wslwebservices.leg.wa.gov/",
        reliability=1.0,
        cache_ttl_days=30,
    )
    db_session.add(source)
    await db_session.flush()

    fetch = FetchEvent(
        source_id=source.id,
        resource_id="HB-1234-2025-26",
        url="https://wslwebservices.leg.wa.gov/LegislationService.asmx",
        fetched_at=datetime.now(UTC),
        http_status=200,
        status=FetchStatus.ok,
    )
    db_session.add(fetch)
    await db_session.flush()

    payload = RawPayload(
        fetch_event_id=fetch.id,
        content_type="text/xml; charset=utf-8",
        body=b"<soap:Envelope/>",
        size_bytes=16,
    )
    db_session.add(payload)
    await db_session.flush()

    return {"jurisdiction": jurisdiction, "source": source, "fetch": fetch, "payload": payload}


async def test_full_provenance_chain_persists(seeded):
    """All four provenance rows round-trip with correct FK relationships."""
    s = seeded
    assert s["source"].jurisdiction_id == s["jurisdiction"].id
    assert s["fetch"].source_id == s["source"].id
    assert s["payload"].fetch_event_id == s["fetch"].id
    assert s["payload"].size_bytes == 16


async def test_citation_polymorphic_insert(db_session, seeded):
    """A Citation can point at any entity_type/entity_id without DB FK enforcement."""
    fetch_id = seeded["fetch"].id
    fake_bill_id = ULID()  # a domain entity that doesn't exist yet — that's the point

    citation = Citation(
        entity_type="bill",
        entity_id=fake_bill_id,
        fetch_event_id=fetch_id,
        field_path="current_status",
        confidence=0.95,
        asserted_at=datetime.now(UTC),
    )
    db_session.add(citation)
    await db_session.flush()

    result = await db_session.execute(
        select(Citation).where(
            Citation.entity_type == "bill",
            Citation.entity_id == fake_bill_id,
        )
    )
    fetched = result.scalar_one()
    assert fetched.entity_type == "bill"
    assert isinstance(fetched.entity_id, ULID)
    assert fetched.entity_id == fake_bill_id
    assert fetched.field_path == "current_status"
    assert fetched.confidence == pytest.approx(0.95)
    assert fetched.fetch_event_id == fetch_id


async def test_citations_index_supports_lookup_by_entity(db_session, seeded):
    """The (entity_type, entity_id) index supports cheap citation lookups for a given entity."""
    fetch_id = seeded["fetch"].id
    legislator_id = ULID()
    bill_id = ULID()

    db_session.add_all(
        [
            Citation(
                entity_type="legislator",
                entity_id=legislator_id,
                fetch_event_id=fetch_id,
                confidence=1.0,
                asserted_at=datetime.now(UTC),
            ),
            Citation(
                entity_type="bill",
                entity_id=bill_id,
                fetch_event_id=fetch_id,
                confidence=1.0,
                asserted_at=datetime.now(UTC),
            ),
            Citation(
                entity_type="bill",
                entity_id=bill_id,
                fetch_event_id=fetch_id,
                field_path="sponsors",
                confidence=0.8,
                asserted_at=datetime.now(UTC),
            ),
        ]
    )
    await db_session.flush()

    result = await db_session.execute(
        select(Citation).where(Citation.entity_type == "bill", Citation.entity_id == bill_id)
    )
    bill_citations = result.scalars().all()
    assert len(bill_citations) == 2
    assert {c.field_path for c in bill_citations} == {None, "sponsors"}
