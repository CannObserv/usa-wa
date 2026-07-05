"""Jurisdiction decoupling (2026-06-09) — identity/vote/bill/session clusters.

Jurisdiction is stored only on ``organizations`` (nullable, the binding root);
people never have one; roles/assignments/events/votes/bills/sessions derive it
transitively via their public org. See
``docs/specs/2026-06-09-canonical-jurisdiction-decoupling-design.md``.
"""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from clearinghouse_domain_legislative.identity import (
    Assignment,
    EntityEvent,
    Organization,
    Person,
    Role,
)
from clearinghouse_domain_legislative.sessions import LegislativeSession
from clearinghouse_domain_legislative.votes import PersonVote, VoteEvent


@pytest.mark.parametrize(
    "model",
    [Person, Assignment, EntityEvent, VoteEvent, PersonVote],
)
def test_decoupled_models_drop_jurisdiction_id(model):
    """Decoupled entities no longer carry a ``jurisdiction_id`` column.

    ``Role`` is intentionally excluded: it regained a ``jurisdiction_id`` for
    the seat model (power-map#261/usa-wa#68), but with different semantics — the
    seat's *enduring district identity*, not the org-level binding root dropped
    here. See :func:`test_role_seat_jurisdiction_is_district_not_binding_root`.
    """
    assert "jurisdiction_id" not in inspect(model).columns


def test_role_seat_jurisdiction_is_district_not_binding_root():
    """Role's ``jurisdiction_id`` is back as the seat's district (nullable)."""
    col = inspect(Role).columns["jurisdiction_id"]
    assert col.nullable is True


def test_organization_keeps_nullable_jurisdiction():
    """Organization remains the binding root: jurisdiction_id present but nullable."""
    col = inspect(Organization).columns["jurisdiction_id"]
    assert col.nullable is True


async def test_person_persists_without_jurisdiction(db_session):
    person = Person(source="wsl", source_id="p-100", name_full="No Jurisdiction Person")
    db_session.add(person)
    await db_session.flush()
    assert person.id is not None


async def test_organization_persists_null_and_set_jurisdiction(db_session, usa_wa):
    private = Organization(source="ftm", source_id="o-priv", name="Private LLC", org_type="other")
    public = Organization(
        source="usa_wa_legislature",
        source_id="o-senate",
        name="WA State Senate",
        org_type="chamber",
        jurisdiction_id=usa_wa.id,
    )
    db_session.add_all([private, public])
    await db_session.flush()
    assert private.jurisdiction_id is None
    assert public.jurisdiction_id == usa_wa.id


async def test_person_natural_key_is_source_source_id(db_session):
    """Uniqueness now keys on (source, source_id) — no jurisdiction component."""
    db_session.add(Person(source="wsl", source_id="dup", name_full="First"))
    await db_session.flush()
    db_session.add(Person(source="wsl", source_id="dup", name_full="Second"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_legislative_session_requires_organization(db_session, usa_wa):
    """A session belongs to the legislature org (NOT NULL organization_id)."""
    legislature = Organization(
        source="usa_wa_legislature",
        source_id="o-leg",
        name="Washington State Legislature",
        org_type="government_agency",
        jurisdiction_id=usa_wa.id,
    )
    db_session.add(legislature)
    await db_session.flush()

    session = LegislativeSession(
        source="usa_wa_legislature",
        source_id="2025-regular",
        slug="2025-regular",
        name="2025 Regular Session",
        classification="primary",
        organization_id=legislature.id,
    )
    db_session.add(session)
    await db_session.flush()
    assert session.organization_id == legislature.id


async def test_role_jurisdiction_reachable_via_org(db_session, usa_wa):
    """A non-seat role has NULL seat jurisdiction; its governing jurisdiction is
    still reached by joining to its org (the seat ``jurisdiction_id`` on Role is
    the district, only set for districted legislator seats — power-map#261)."""
    org = Organization(
        source="usa_wa_legislature",
        source_id="o-house",
        name="WA State House",
        org_type="chamber",
        jurisdiction_id=usa_wa.id,
    )
    db_session.add(org)
    await db_session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id="r-rep",
        organization_id=org.id,
        name="Representative",
        role_type="elected_member",
    )
    db_session.add(role)
    await db_session.flush()

    derived = (
        await db_session.execute(
            select(Organization.jurisdiction_id).where(Organization.id == role.organization_id)
        )
    ).scalar_one()
    assert derived == usa_wa.id
