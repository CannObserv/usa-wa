"""Read-side ``retired_at`` filtering for the identity cluster (usa-wa#38).

Retired rows (PM deleted the entity with no surviving merge-winner) are kept as
provenance but must not leak into *live* reads. These tests pin the shared
``exclude_retired`` helper + the ``RetirableMixin.not_retired()`` contract that
every read endpoint routes through.
"""

import pytest
from sqlalchemy import select

from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    Role,
)
from clearinghouse_domain_legislative.queries import exclude_retired


def test_all_identity_models_expose_not_retired():
    """The four identity models share the retirable contract via the mixin."""
    for model in (Person, Organization, Role, Assignment):
        assert "retired_at" in model.__table__.columns
        # not_retired() is a bound filter expression, not a no-op.
        assert model.not_retired().compare(model.retired_at.is_(None))


async def _org(db_session, source_id, *, retired):
    from datetime import UTC, datetime

    org = Organization(
        source="wsl",
        source_id=source_id,
        name="Acme",
        org_type="committee",
        retired_at=datetime.now(UTC) if retired else None,
    )
    db_session.add(org)
    await db_session.flush()
    return org


async def test_exclude_retired_drops_tombstoned_rows(db_session):
    """A live read over orgs omits the retired one by default."""
    live = await _org(db_session, "live-1", retired=False)
    await _org(db_session, "dead-1", retired=True)

    stmt = exclude_retired(select(Organization), Organization)
    rows = (await db_session.execute(stmt)).scalars().all()

    ids = {r.id for r in rows}
    assert live.id in ids
    assert all(r.retired_at is None for r in rows)
    assert len(ids) == 1


async def test_include_retired_escape_hatch_returns_all(db_session):
    """The audit/provenance path opts back into retired rows."""
    await _org(db_session, "live-2", retired=False)
    await _org(db_session, "dead-2", retired=True)

    stmt = exclude_retired(select(Organization), Organization, include_retired=True)
    rows = (await db_session.execute(stmt)).scalars().all()

    assert len(rows) == 2
    assert any(r.retired_at is not None for r in rows)


async def test_exclude_retired_filters_each_join_hop(db_session):
    """A join through org → role filters retired rows at every supplied model."""
    from datetime import UTC, datetime

    live_org = await _org(db_session, "live-3", retired=False)
    dead_org = await _org(db_session, "dead-3", retired=True)

    live_role = Role(
        source="wsl",
        source_id="role-live",
        organization_id=live_org.id,
        name="Chair",
        role_type="leadership",
    )
    # A live role hanging off a retired org — must still drop via the org hop.
    orphan_role = Role(
        source="wsl",
        source_id="role-orphan",
        organization_id=dead_org.id,
        name="Member",
        role_type="committee_member",
    )
    # A retired role hanging off a live org — drops via the role hop.
    dead_role = Role(
        source="wsl",
        source_id="role-dead",
        organization_id=live_org.id,
        name="Vice Chair",
        role_type="leadership",
        retired_at=datetime.now(UTC),
    )
    db_session.add_all([live_role, orphan_role, dead_role])
    await db_session.flush()

    stmt = exclude_retired(
        select(Role).join(Organization, Role.organization_id == Organization.id),
        Role,
        Organization,
    )
    rows = (await db_session.execute(stmt)).scalars().all()

    assert {r.id for r in rows} == {live_role.id}


def test_exclude_retired_requires_a_model():
    """A model-less call is a programming error — silently filtering nothing would
    leak retired rows, so we fail loudly instead."""
    with pytest.raises(ValueError):
        exclude_retired(select(Organization))
