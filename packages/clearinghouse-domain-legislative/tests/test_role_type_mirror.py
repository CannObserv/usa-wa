"""RoleType catalog mirror (power-map#268, usa-wa#68) — local cache of PM's
role_types catalog so the sync descriptor can decide seat-vs-title observation
shape at runtime instead of a hardcoded slug map."""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from clearinghouse_domain_legislative.role_types import RoleType


def test_role_type_columns():
    cols = inspect(RoleType).columns
    expected = {"id", "pm_role_type_id", "slug", "display_name", "expects_jurisdiction"}
    assert expected <= set(cols.keys())
    assert cols["expects_jurisdiction"].nullable is False
    assert cols["pm_role_type_id"].nullable is True


async def test_role_type_persists(db_session):
    pm_id = ULID()
    rt = RoleType(
        pm_role_type_id=pm_id,
        slug="state_senator",
        display_name="State Senator",
        expects_jurisdiction=True,
    )
    db_session.add(rt)
    await db_session.flush()
    assert rt.id is not None
    fetched = (
        await db_session.execute(select(RoleType).where(RoleType.slug == "state_senator"))
    ).scalar_one()
    assert fetched.expects_jurisdiction is True
    assert fetched.pm_role_type_id == pm_id


async def test_role_type_slug_unique(db_session):
    db_session.add(RoleType(slug="state_senator", display_name="A", expects_jurisdiction=True))
    await db_session.flush()
    db_session.add(RoleType(slug="state_senator", display_name="B", expects_jurisdiction=True))
    with pytest.raises(IntegrityError):
        await db_session.flush()
