"""Read-side liveness filtering for the identity cluster (usa-wa#38/#42).

A row that PM archived (``archived_at``, reversible) or deleted with no surviving
winner (``deleted_at``, terminal) is kept as provenance but must not leak into
*live* reads. These tests pin the shared ``live_only`` helper + the
``LifecycleMixin.is_live()`` contract that every read endpoint routes through.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    Role,
)
from clearinghouse_domain_legislative.queries import live_only


def test_all_identity_models_expose_is_live():
    """The four identity models share the lifecycle contract via the mixin."""
    for model in (Person, Organization, Role, Assignment):
        assert "archived_at" in model.__table__.columns
        assert "deleted_at" in model.__table__.columns
        # is_live() is a bound filter expression over both axes, not a no-op.
        assert model.is_live().compare(model.archived_at.is_(None) & model.deleted_at.is_(None))


async def _org(db_session, source_id, *, archived=False, deleted=False, active=True):
    org = Organization(
        source="wsl",
        source_id=source_id,
        name="Acme",
        org_type="committee",
        active=active,
        archived_at=datetime.now(UTC) if archived else None,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db_session.add(org)
    await db_session.flush()
    return org


def test_active_is_orgs_only_and_not_a_lifecycle_axis():
    """``active`` is a domain flag on Organization alone — not on the other identity
    models, and not part of ``is_live()`` (it never hides a row from reads)."""
    assert "active" in Organization.__table__.columns
    for model in (Person, Role, Assignment):
        assert "active" not in model.__table__.columns
    # is_live() stays the two archived/deleted axes — active is absent from it.
    assert "active" not in str(Organization.is_live())


async def test_live_only_keeps_inactive_orgs_visible(db_session):
    """``active=false`` is a domain flag, NOT a live-read gate (usa-wa#43): a
    dissolved-but-not-archived committee stays in the read fan-out."""
    live = await _org(db_session, "active-1")
    inactive = await _org(db_session, "inactive-1", active=False)

    stmt = live_only(select(Organization), Organization)
    ids = {r.id for r in (await db_session.execute(stmt)).scalars().all()}

    assert ids == {live.id, inactive.id}


async def test_live_only_drops_archived_and_deleted_rows(db_session):
    """A live read over orgs omits both the archived and the deleted one by default."""
    live = await _org(db_session, "live-1")
    await _org(db_session, "archived-1", archived=True)
    await _org(db_session, "deleted-1", deleted=True)

    stmt = live_only(select(Organization), Organization)
    rows = (await db_session.execute(stmt)).scalars().all()

    ids = {r.id for r in rows}
    assert ids == {live.id}
    assert all(r.archived_at is None and r.deleted_at is None for r in rows)


async def test_include_hidden_escape_hatch_returns_all(db_session):
    """The audit/provenance path opts back into archived + deleted rows."""
    await _org(db_session, "live-2")
    await _org(db_session, "archived-2", archived=True)
    await _org(db_session, "deleted-2", deleted=True)

    stmt = live_only(select(Organization), Organization, include_hidden=True)
    rows = (await db_session.execute(stmt)).scalars().all()

    assert len(rows) == 3
    assert any(r.archived_at is not None for r in rows)
    assert any(r.deleted_at is not None for r in rows)


async def test_live_only_filters_each_join_hop(db_session):
    """A join through org → role filters non-live rows at every supplied model."""
    live_org = await _org(db_session, "live-3")
    archived_org = await _org(db_session, "archived-3", archived=True)

    live_role = Role(
        source="wsl",
        source_id="role-live",
        organization_id=live_org.id,
        name="Chair",
        role_type="leadership",
    )
    # A live role hanging off an archived org — must still drop via the org hop.
    orphan_role = Role(
        source="wsl",
        source_id="role-orphan",
        organization_id=archived_org.id,
        name="Member",
        role_type="committee_member",
    )
    # A deleted role hanging off a live org — drops via the role hop.
    dead_role = Role(
        source="wsl",
        source_id="role-dead",
        organization_id=live_org.id,
        name="Vice Chair",
        role_type="leadership",
        deleted_at=datetime.now(UTC),
    )
    db_session.add_all([live_role, orphan_role, dead_role])
    await db_session.flush()

    stmt = live_only(
        select(Role).join(Organization, Role.organization_id == Organization.id),
        Role,
        Organization,
    )
    rows = (await db_session.execute(stmt)).scalars().all()

    assert {r.id for r in rows} == {live_role.id}


def test_live_only_requires_a_model():
    """A model-less call is a programming error — silently filtering nothing would
    leak non-live rows, so we fail loudly instead."""
    with pytest.raises(ValueError):
        live_only(select(Organization))
