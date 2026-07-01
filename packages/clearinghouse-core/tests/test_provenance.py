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

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.provenance import (
    Citation,
    DocumentIdentifier,
    FetchEvent,
    FetchStatus,
    RawPayload,
    RetentionPolicy,
    Source,
)


@pytest.fixture
async def seeded(db_session):
    """A Jurisdiction + Source + FetchEvent + RawPayload chain ready for citation tests."""
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()

    jurisdiction = Jurisdiction(
        slug="usa-wa",
        name="Washington State",
        type_id=state_type.id,
        recorded_at=datetime.now(UTC),
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


def test_fetch_events_dedup_index_covers_archival_lookup():
    """A composite ``(source_id, resource_id, content_hash)`` index backs the runner's
    per-fetch archival dedup lookup (#59). The single-column ``source_id``/``resource_id``
    indexes narrow the scan but leave ``content_hash`` seq-filtered; this composite covers
    the exact predicate of ``AdapterRunner._payload_already_archived``."""
    by_cols = {tuple(c.name for c in idx.columns): idx for idx in FetchEvent.__table__.indexes}
    assert ("source_id", "resource_id", "content_hash") in by_cols


async def test_source_retention_policy_defaults_to_operational_cache(db_session, seeded):
    """A Source defaults to the short-TTL operational-cache retention (#54).

    The default keeps existing behaviour: payloads are eligible for the eventual
    cache GC. Archival sources opt out explicitly.
    """
    source = seeded["source"]
    await db_session.refresh(source)
    assert source.retention_policy == RetentionPolicy.operational_cache


async def test_source_retention_policy_archival_is_settable(db_session, seeded):
    """A provenance-critical source can be marked archival (#54).

    The flag is the forward contract: an eventual RawPayload GC deletes only
    operational_cache payloads past TTL and never touches archival ones.
    """
    source = seeded["source"]
    source.retention_policy = RetentionPolicy.archival
    await db_session.flush()
    await db_session.refresh(source)
    assert source.retention_policy == RetentionPolicy.archival


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


async def test_document_identifier_polymorphic_round_trip(db_session, seeded):
    """DocumentIdentifier attaches identifiers to bill_versions and amendments polymorphically.

    Exercises the motivating WA case: one Amendment carrying both a Code Reviser
    identifier and a committee-amendment identifier under different schemes, and
    a BillVersion carrying its own Code Reviser bill-text identifier.
    """
    fake_bill_version_id = ULID()
    fake_amendment_id = ULID()

    db_session.add_all(
        [
            DocumentIdentifier(
                jurisdiction_id=seeded["jurisdiction"].id,
                source="usa_wa_legislature",
                source_id="bv-H-0043.1",
                entity_type="bill_version",
                entity_id=fake_bill_version_id,
                scheme="usa_wa_code_reviser",
                value="H-0043.1",
            ),
            DocumentIdentifier(
                jurisdiction_id=seeded["jurisdiction"].id,
                source="usa_wa_legislature",
                source_id="amd-S-5276.3-26",
                entity_type="amendment",
                entity_id=fake_amendment_id,
                scheme="usa_wa_code_reviser",
                value="S-5276.3/26",
            ),
            DocumentIdentifier(
                jurisdiction_id=seeded["jurisdiction"].id,
                source="usa_wa_legislature",
                source_id="amd-1066-AMH-CPB-CLOD-295",
                entity_type="amendment",
                entity_id=fake_amendment_id,
                scheme="usa_wa_committee_amendment",
                value="1066 AMH CPB CLOD 295",
                parsed_components={
                    "bill_number": "1066",
                    "chamber": "H",
                    "committee_abbr": "CPB",
                    "drafter_initials": "CLOD",
                    "sequence": "295",
                },
            ),
        ]
    )
    await db_session.flush()

    amendment_ids = await db_session.execute(
        select(DocumentIdentifier).where(
            DocumentIdentifier.entity_type == "amendment",
            DocumentIdentifier.entity_id == fake_amendment_id,
        )
    )
    rows = amendment_ids.scalars().all()
    assert len(rows) == 2
    by_scheme = {r.scheme: r for r in rows}
    assert by_scheme["usa_wa_code_reviser"].value == "S-5276.3/26"
    assert by_scheme["usa_wa_code_reviser"].parsed_components is None
    assert by_scheme["usa_wa_committee_amendment"].value == "1066 AMH CPB CLOD 295"
    assert by_scheme["usa_wa_committee_amendment"].parsed_components == {
        "bill_number": "1066",
        "chamber": "H",
        "committee_abbr": "CPB",
        "drafter_initials": "CLOD",
        "sequence": "295",
    }

    bill_version_rows = await db_session.execute(
        select(DocumentIdentifier).where(
            DocumentIdentifier.entity_type == "bill_version",
            DocumentIdentifier.entity_id == fake_bill_version_id,
        )
    )
    bv_row = bill_version_rows.scalar_one()
    assert bv_row.scheme == "usa_wa_code_reviser"
    assert bv_row.value == "H-0043.1"
