"""Dated org-name sync helper tests (usa-wa#45).

The org descriptor mirrors PM's dated ``OrgName`` variants (power-map#239) into
``canonical.organization_names`` — the queryable footing for associating
historical WSL data that references *former* committee names. ``Organization.name``
stays the resolved current scalar; this table is the history/association surface.

Covers the pure PM→local mapping (``OrgName`` dict → columns, ISO date parse,
natural key) and the upsert/prune behaviour against an org's name set, plus the
descriptor wiring (``upsert_from_pm`` mirrors the embedded ``names[]``).
"""

from datetime import date

from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization, OrganizationName
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor
from usa_wa_sync_powermap.descriptors.org_names import (
    NAME_SOURCE,
    map_pm_org_name,
    sync_org_names,
)


def _pm_name(
    name_id: str,
    *,
    name="House Appropriations Committee",
    name_type="legal",
    is_canonical=True,
    effective_start=None,
    effective_end=None,
) -> dict:
    """A PM read ``OrgName`` dict (as produced by the generated model's to_dict)."""
    return {
        "id": name_id,
        "name": name,
        "name_type": name_type,
        "is_canonical": is_canonical,
        "effective_start": effective_start,
        "effective_end": effective_end,
    }


def test_map_pm_org_name_columns_and_natural_key():
    nid = str(ULID())
    org_id = ULID()
    mapped = map_pm_org_name(_pm_name(nid), organization_id=org_id)

    assert mapped["source"] == NAME_SOURCE == "powermap"
    assert mapped["source_id"] == nid
    assert mapped["pm_org_name_id"] == ULID.from_str(nid)
    assert mapped["organization_id"] == org_id
    assert mapped["name"] == "House Appropriations Committee"
    assert mapped["name_type"] == "legal"
    assert mapped["is_canonical"] is True
    assert mapped["effective_start"] is None
    assert mapped["effective_end"] is None


def test_map_pm_org_name_parses_iso_dates():
    mapped = map_pm_org_name(
        _pm_name(
            str(ULID()),
            name="House Health Care Committee",
            name_type="former",
            is_canonical=False,
            effective_start="2021-01-11",
            effective_end="2023-01-09",
        ),
        organization_id=ULID(),
    )
    assert mapped["effective_start"] == date(2021, 1, 11)
    assert mapped["effective_end"] == date(2023, 1, 9)
    assert mapped["name_type"] == "former"
    assert mapped["is_canonical"] is False


def test_map_pm_org_name_accepts_date_objects():
    """A record already carrying ``date`` objects (not ISO strings) passes through."""
    mapped = map_pm_org_name(
        _pm_name(str(ULID()), effective_start=date(2025, 1, 13)),
        organization_id=ULID(),
    )
    assert mapped["effective_start"] == date(2025, 1, 13)


async def _add_org(session, *, source_id="C-1", name="Org") -> Organization:
    org = Organization(
        source="usa_wa_legislature", source_id=source_id, name=name, org_type="committee"
    )
    session.add(org)
    await session.flush()
    return org


async def _names_for(session, org_id) -> list[OrganizationName]:
    return list(
        (
            await session.execute(
                select(OrganizationName).where(OrganizationName.organization_id == org_id)
            )
        )
        .scalars()
        .all()
    )


async def test_sync_inserts_new_names(db_session):
    org = await _add_org(db_session)
    a, b = str(ULID()), str(ULID())
    await sync_org_names(
        db_session,
        organization_id=org.id,
        pm_names=[
            _pm_name(a, name="House Appropriations Committee"),
            _pm_name(
                b,
                name="House Ways & Means Committee",
                name_type="former",
                is_canonical=False,
                effective_end="2019-01-13",
            ),
        ],
    )
    rows = await _names_for(db_session, org.id)
    assert {r.source_id for r in rows} == {a, b}
    assert all(r.source == "powermap" for r in rows)
    assert all(r.pm_org_name_id is not None for r in rows)
    former = next(r for r in rows if r.name_type == "former")
    assert former.effective_end == date(2019, 1, 13)
    assert former.is_canonical is False


async def test_sync_updates_existing_by_anchor(db_session):
    org = await _add_org(db_session)
    nid = str(ULID())
    await sync_org_names(db_session, organization_id=org.id, pm_names=[_pm_name(nid)])
    # Re-sync the same anchor with a changed window → update in place, no dup.
    await sync_org_names(
        db_session,
        organization_id=org.id,
        pm_names=[_pm_name(nid, effective_end="2027-01-11")],
    )
    rows = await _names_for(db_session, org.id)
    assert len(rows) == 1
    assert rows[0].effective_end == date(2027, 1, 11)


async def test_sync_prunes_names_absent_from_pm(db_session):
    org = await _add_org(db_session)
    keep, drop = str(ULID()), str(ULID())
    await sync_org_names(
        db_session, organization_id=org.id, pm_names=[_pm_name(keep), _pm_name(drop)]
    )
    await sync_org_names(db_session, organization_id=org.id, pm_names=[_pm_name(keep)])
    rows = await _names_for(db_session, org.id)
    assert {r.source_id for r in rows} == {keep}


# --- descriptor wiring -------------------------------------------------------


def _pm_org_with_names(pm_id, *, name="PM Canonical", names=None):
    return {
        "id": str(pm_id),
        "name": name,
        "parent_id": None,
        "jurisdiction_affiliations": [],
        "updated_at": "2030-01-01T00:00:00Z",
        "active": True,
        "names": names or [],
    }


async def test_upsert_mirrors_embedded_names(db_session):
    """``upsert_from_pm`` mirrors the embedded ``names[]`` into the child table while
    ``Organization.name`` still adopts PM's resolved current scalar."""
    pm_id = ULID()
    org = await _add_org(db_session, source_id="C-7", name="Adapter Name")
    org.pm_organization_id = pm_id
    await db_session.flush()

    current, former = str(ULID()), str(ULID())
    record = _pm_org_with_names(
        pm_id,
        name="House Health Care & Wellness Committee",
        names=[
            _pm_name(current, name="House Health Care & Wellness Committee"),
            _pm_name(
                former,
                name="House Health Care Committee",
                name_type="former",
                is_canonical=False,
                effective_end="2017-01-09",
            ),
        ],
    )
    result = await OrganizationDescriptor().upsert_from_pm(db_session, record, existing=org)

    assert result is org
    assert org.name == "House Health Care & Wellness Committee"  # resolved scalar adopted
    rows = await _names_for(db_session, org.id)
    assert {r.source_id for r in rows} == {current, former}


async def test_upsert_handles_record_without_names(db_session):
    """A record with no ``names`` key (e.g. a search-shaped feed bump) is a no-op for
    the name mirror — it must not crash or wipe existing rows."""
    pm_id = ULID()
    org = await _add_org(db_session, source_id="C-8", name="X")
    org.pm_organization_id = pm_id
    await db_session.flush()
    record = _pm_org_with_names(pm_id, name="X")
    del record["names"]

    result = await OrganizationDescriptor().upsert_from_pm(db_session, record, existing=org)
    assert result is org
    assert await _names_for(db_session, org.id) == []
