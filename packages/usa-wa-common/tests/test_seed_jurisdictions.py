"""The jurisdiction ownership transfer (#310): vocabulary → the mirror table."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from usa_wa_common.jurisdictions import WA_JURISDICTIONS
from usa_wa_common.seed_jurisdictions import seed_jurisdictions

pytestmark = pytest.mark.db


def test_vocabulary_shape() -> None:
    assert len(WA_JURISDICTIONS) == 100
    by_type: dict[str, int] = {}
    for fact in WA_JURISDICTIONS:
        by_type[fact.type_slug] = by_type.get(fact.type_slug, 0) + 1
    assert by_type == {
        "country": 1,
        "state": 1,
        "legislative_district": 49,
        "county": 39,
        "congressional_district": 10,
    }
    assert len({f.slug for f in WA_JURISDICTIONS}) == 100


async def test_seed_creates_then_noops(db_session) -> None:
    summary = await seed_jurisdictions(db_session)
    assert summary["created"] == 100
    assert summary["updated"] == 0

    again = await seed_jurisdictions(db_session)
    assert again["created"] == 0
    assert again["updated"] == 0
    assert again["unchanged"] == 100


async def test_seed_asserts_drifted_names_and_keeps_strangers(db_session) -> None:
    await seed_jurisdictions(db_session)
    ld1 = (
        await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa-ld-1"))
    ).scalar_one()
    ld1.name = "Renamed By Someone"
    city_type = JurisdictionType(slug="city", display_name="City")
    db_session.add(city_type)
    await db_session.flush()
    db_session.add(
        Jurisdiction(
            slug="usa-wa-city-seattle",
            name="Seattle",
            type_id=city_type.id,
            recorded_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    summary = await seed_jurisdictions(db_session)
    assert summary["updated"] == 1
    assert summary["unknown_local"] == 1
    refreshed = (
        await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa-ld-1"))
    ).scalar_one()
    assert refreshed.name == "Washington Legislative District 1"
    seattle = (
        await db_session.execute(
            select(Jurisdiction).where(Jurisdiction.slug == "usa-wa-city-seattle")
        )
    ).scalar_one()
    assert seattle.name == "Seattle"
