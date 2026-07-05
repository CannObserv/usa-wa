"""Role-type catalog sync (power-map#268, usa-wa#68) — fetch PM's role_types
catalog and upsert the local :class:`RoleType` mirror keyed on slug."""

from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.role_types import RoleType
from usa_wa_sync_powermap.role_type_catalog import sync_role_type_catalog


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    async def list_role_types(self):
        return self._rows


async def test_sync_inserts_catalog_rows(db_session):
    rep_id, sen_id = str(ULID()), str(ULID())
    client = _FakeClient(
        [
            {
                "id": rep_id,
                "slug": "state_representative",
                "display_name": "State Representative",
                "is_seat": True,
            },
            {
                "id": sen_id,
                "slug": "state_senator",
                "display_name": "State Senator",
                "is_seat": True,
            },
        ]
    )

    count = await sync_role_type_catalog(db_session, client)

    assert count == 2
    rows = (await db_session.execute(select(RoleType).order_by(RoleType.slug))).scalars().all()
    assert [r.slug for r in rows] == ["state_representative", "state_senator"]
    assert all(r.is_seat for r in rows)
    assert str(rows[1].pm_role_type_id) == sen_id


async def test_sync_updates_existing_row_by_slug(db_session):
    db_session.add(RoleType(slug="state_senator", display_name="Old Name", is_seat=False))
    await db_session.flush()

    pm_id = str(ULID())
    client = _FakeClient(
        [{"id": pm_id, "slug": "state_senator", "display_name": "State Senator", "is_seat": True}]
    )
    await sync_role_type_catalog(db_session, client)

    row = (
        await db_session.execute(select(RoleType).where(RoleType.slug == "state_senator"))
    ).scalar_one()
    assert row.display_name == "State Senator"  # updated
    assert row.is_seat is True  # updated
    assert str(row.pm_role_type_id) == pm_id  # anchored


async def test_sync_demotes_slug_dropped_by_pm(db_session):
    """A seat type PM no longer lists is demoted (is_seat=False), not deleted, so the
    descriptor stops treating a retired type as a seat (usa-wa#68 CR)."""
    db_session.add(RoleType(slug="retired_seat", display_name="Retired", is_seat=True))
    await db_session.flush()

    client = _FakeClient(
        [
            {
                "id": str(ULID()),
                "slug": "state_senator",
                "display_name": "State Senator",
                "is_seat": True,
            }
        ]
    )
    await sync_role_type_catalog(db_session, client)

    retired = (
        await db_session.execute(select(RoleType).where(RoleType.slug == "retired_seat"))
    ).scalar_one()
    assert retired.is_seat is False  # demoted, still present
    assert retired.slug == "retired_seat"


async def test_sync_is_idempotent(db_session):
    client = _FakeClient(
        [
            {
                "id": str(ULID()),
                "slug": "state_senator",
                "display_name": "State Senator",
                "is_seat": True,
            }
        ]
    )
    await sync_role_type_catalog(db_session, client)
    await sync_role_type_catalog(db_session, client)

    rows = (await db_session.execute(select(RoleType))).scalars().all()
    assert len(rows) == 1
