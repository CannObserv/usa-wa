"""The registry seed (#308): canonical identity → registry, ULIDs preserved."""

from datetime import UTC, datetime

import pytest

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.registry import (
    KIND_ORG,
    KIND_PERSON,
    KIND_ROLE,
    apply_decision,
    decide,
    registered_view,
)
from clearinghouse_domain_legislative.identity import (
    Organization,
    Person,
    PersonIdentifier,
    Role,
)
from usa_wa_pipeline.adjudicate import adjudicate_merge
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
    role = Role(
        source="usa_wa_legislature",
        source_id="committee-member-role:1754",
        organization_id=org.id,
        name="Member",
        role_type="committee_member",
    )
    db_session.add(role)
    await db_session.flush()
    return person, org, role


async def test_seed_registers_clusters_under_canonical_ulids(db_session) -> None:
    person, org, role = await _seed_canonical(db_session)
    summary = await seed_registry(db_session)
    assert summary["persons_minted"] == 1
    assert summary["orgs_minted"] == 1
    assert summary["roles_minted"] == 1
    assert summary["conflicts"] == 0

    persons = await registered_view(db_session, KIND_PERSON)
    assert persons["usa_wa_legislature:27992"] == str(person.id)
    assert persons["wa_pdc:7710"] == str(person.id)
    orgs = await registered_view(db_session, KIND_ORG)
    assert orgs["usa_wa_legislature:1754"] == str(org.id)
    roles = await registered_view(db_session, KIND_ROLE)
    assert roles["usa_wa_legislature:committee-member-role:1754"] == str(role.id)


async def test_the_seed_preserves_the_role_ulid_power_map_already_holds(db_session) -> None:
    """#313: the reason roles get a registry at all is that the API needs a
    stable handle, and PM already holds 312 role anchors from the #312 export.
    Minting fresh ULIDs would invalidate them mid-cutover — so the seed carries
    the canonical id across exactly as it does for persons and orgs, and
    `role_key` stays the natural key so nothing is mediated away."""
    _person, _org, role = await _seed_canonical(db_session)
    await seed_registry(db_session)
    roles = await registered_view(db_session, KIND_ROLE)
    assert roles["usa_wa_legislature:committee-member-role:1754"] == str(role.id)


async def test_seed_is_idempotent(db_session) -> None:
    await _seed_canonical(db_session)
    await seed_registry(db_session)
    summary = await seed_registry(db_session)
    assert summary["persons_minted"] == 0
    assert summary["orgs_minted"] == 0
    assert summary["noops"] >= 2


async def test_foreign_owned_key_is_a_conflict_not_a_silent_append(db_session) -> None:
    """A cluster key already registered to a DIFFERENT entity must surface as a
    conflict at seed time (CR 3): the append row of the decision table would
    otherwise bind the person's whole cluster to that entity and never mint
    their canonical ULID — a silent cross-person merge."""
    person, _org, _role = await _seed_canonical(db_session)
    foreign = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wa_pdc:7710"}), {}), registered_by="test"
    )

    summary = await seed_registry(db_session)
    assert summary["conflicts"] == 1
    persons = await registered_view(db_session, KIND_PERSON)
    # the person's own key must NOT be silently bound to the foreign entity
    assert persons.get("usa_wa_legislature:27992") != foreign
    assert "usa_wa_legislature:27992" not in persons  # unresolved = triage, not a guess


async def test_reseed_after_adjudicated_merge_is_not_a_conflict(db_session) -> None:
    """A canonical row whose entity was merged away by adjudication re-seeds as
    a noop: the cluster resolves to the survivor the canonical ULID itself
    resolves to — sanctioned policy, not a duplicate (CR 3/4 symmetry)."""
    person, _org, _role = await _seed_canonical(db_session)
    await seed_registry(db_session)
    survivor = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"other:1"}), {}), registered_by="test"
    )
    await adjudicate_merge(
        db_session, KIND_PERSON, loser=str(person.id), survivor=survivor, note="same human"
    )

    summary = await seed_registry(db_session)
    assert summary["conflicts"] == 0
    assert summary["persons_minted"] == 0
