"""Source-model tests — the configured feeds (``provenance.py`` until #412 PR F)."""

from datetime import UTC, datetime

import pytest

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.sources import RetentionPolicy, Source


@pytest.fixture
async def seeded(db_session):
    """A Jurisdiction + Source pair."""
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

    return {"jurisdiction": jurisdiction, "source": source}


async def test_a_source_names_its_jurisdiction(seeded):
    assert seeded["source"].jurisdiction_id == seeded["jurisdiction"].id


async def test_source_retention_policy_defaults_to_operational_cache(db_session, seeded):
    """A Source defaults to the short-TTL operational-cache retention (#54); archival
    sources opt out explicitly."""
    source = seeded["source"]
    await db_session.refresh(source)
    assert source.retention_policy == RetentionPolicy.operational_cache


async def test_source_retention_policy_archival_is_settable(db_session, seeded):
    """A provenance-critical source can be marked archival (#54): the forward contract
    that any GC skips it."""
    source = seeded["source"]
    source.retention_policy = RetentionPolicy.archival
    await db_session.flush()
    await db_session.refresh(source)
    assert source.retention_policy == RetentionPolicy.archival
