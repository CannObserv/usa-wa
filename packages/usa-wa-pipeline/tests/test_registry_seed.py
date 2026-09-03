"""The registry seed (#308): canonical identity → registry, ULIDs preserved."""

from datetime import UTC, datetime

import pytest

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.registry import KIND_ORG, KIND_PERSON, registered_view
from clearinghouse_domain_legislative.identity import Organization, Person, PersonIdentifier
from usa_wa_pipeline.registry_seed import seed_registry

pytestmark = pytest.mark.db


async def _seed_canonical(db_session):
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()
    db_session.add(
        Jurisdiction(slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC))
    )
    await db_session.flush()
    person = Person(source="usa_wa_legislature", source_id="27992", name_full="Dana Whitfield")
    db_session.add(person)
    await db_session.flush()
    db_session.add(
        PersonIdentifier(
            person_id=person.id,
            scheme="wa_pdc",
            value="7710",
            source="usa_wa_pdc",
            source_id="7710:wa_pdc",
        )
    )
    org = Organization(
        source="usa_wa_legislature", source_id="1754", name="Agriculture", org_type="committee"
    )
    db_session.add(org)
    await db_session.flush()
    return person, org


async def test_seed_registers_clusters_under_canonical_ulids(db_session) -> None:
    person, org = await _seed_canonical(db_session)
    summary = await seed_registry(db_session)
    assert summary["persons_minted"] == 1
    assert summary["orgs_minted"] == 1
    assert summary["conflicts"] == 0

    persons = await registered_view(db_session, KIND_PERSON)
    assert persons["usa_wa_legislature:27992"] == str(person.id)
    assert persons["wa_pdc:7710"] == str(person.id)
    orgs = await registered_view(db_session, KIND_ORG)
    assert orgs["usa_wa_legislature:1754"] == str(org.id)


async def test_seed_is_idempotent(db_session) -> None:
    await _seed_canonical(db_session)
    await seed_registry(db_session)
    summary = await seed_registry(db_session)
    assert summary["persons_minted"] == 0
    assert summary["orgs_minted"] == 0
    assert summary["noops"] >= 2
