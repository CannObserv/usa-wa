"""Org-acronym sync helper tests (usa-wa#47).

The org descriptor mirrors PM's ``OrgAcronym`` variants (a list distinct from
``names``) into ``canonical.organization_acronyms`` — the queryable footing for
associating historical WSL data that references *former* committee acronyms.
``Organization.acronym`` stays the resolved current scalar; this table is the
history/association surface.

Sibling to ``test_org_name_sync.py`` (#45) but thinner: PM's ``OrgAcronym`` is
``{id, acronym, is_canonical}`` only — no ``name_type``, no dated window.

Covers the pure PM→local mapping, upsert/prune behaviour against an org's acronym
set, and the descriptor wiring (``upsert_from_pm`` mirrors the embedded
``acronyms[]``).
"""

from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization, OrganizationAcronym
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor
from usa_wa_sync_powermap.descriptors.org_acronyms import (
    ACRONYM_SOURCE,
    map_pm_org_acronym,
    sync_org_acronyms,
)


def _pm_acronym(acronym_id: str, *, acronym="APP", is_canonical=True) -> dict:
    """A PM read ``OrgAcronym`` dict (as produced by the generated model's to_dict)."""
    return {"id": acronym_id, "acronym": acronym, "is_canonical": is_canonical}


def test_map_pm_org_acronym_columns_and_natural_key():
    aid = str(ULID())
    org_id = ULID()
    mapped = map_pm_org_acronym(_pm_acronym(aid), organization_id=org_id)

    assert mapped["source"] == ACRONYM_SOURCE == "powermap"
    assert mapped["source_id"] == aid
    assert mapped["pm_org_acronym_id"] == ULID.from_str(aid)
    assert mapped["organization_id"] == org_id
    assert mapped["acronym"] == "APP"
    assert mapped["is_canonical"] is True


def test_map_pm_org_acronym_non_canonical():
    mapped = map_pm_org_acronym(
        _pm_acronym(str(ULID()), acronym="APPRO", is_canonical=False),
        organization_id=ULID(),
    )
    assert mapped["acronym"] == "APPRO"
    assert mapped["is_canonical"] is False


async def _add_org(session, *, source_id="C-1", name="Org") -> Organization:
    org = Organization(
        source="usa_wa_legislature", source_id=source_id, name=name, org_type="committee"
    )
    session.add(org)
    await session.flush()
    return org


async def _acronyms_for(session, org_id) -> list[OrganizationAcronym]:
    return list(
        (
            await session.execute(
                select(OrganizationAcronym).where(OrganizationAcronym.organization_id == org_id)
            )
        )
        .scalars()
        .all()
    )


async def test_sync_inserts_new_acronyms(db_session):
    org = await _add_org(db_session)
    a, b = str(ULID()), str(ULID())
    await sync_org_acronyms(
        db_session,
        organization_id=org.id,
        pm_acronyms=[
            _pm_acronym(a, acronym="APP"),
            _pm_acronym(b, acronym="WM", is_canonical=False),
        ],
    )
    rows = await _acronyms_for(db_session, org.id)
    assert {r.source_id for r in rows} == {a, b}
    assert all(r.source == "powermap" for r in rows)
    assert all(r.pm_org_acronym_id is not None for r in rows)
    former = next(r for r in rows if r.acronym == "WM")
    assert former.is_canonical is False


async def test_sync_updates_existing_by_anchor(db_session):
    org = await _add_org(db_session)
    aid = str(ULID())
    await sync_org_acronyms(db_session, organization_id=org.id, pm_acronyms=[_pm_acronym(aid)])
    # Re-sync the same anchor with a changed acronym → update in place, no dup.
    await sync_org_acronyms(
        db_session,
        organization_id=org.id,
        pm_acronyms=[_pm_acronym(aid, acronym="APPR", is_canonical=False)],
    )
    rows = await _acronyms_for(db_session, org.id)
    assert len(rows) == 1
    assert rows[0].acronym == "APPR"
    assert rows[0].is_canonical is False


async def test_sync_prunes_acronyms_absent_from_pm(db_session):
    org = await _add_org(db_session)
    keep, drop = str(ULID()), str(ULID())
    await sync_org_acronyms(
        db_session, organization_id=org.id, pm_acronyms=[_pm_acronym(keep), _pm_acronym(drop)]
    )
    await sync_org_acronyms(db_session, organization_id=org.id, pm_acronyms=[_pm_acronym(keep)])
    rows = await _acronyms_for(db_session, org.id)
    assert {r.source_id for r in rows} == {keep}


async def test_sync_empty_list_prunes_all(db_session):
    """An org can legitimately reach **zero** acronyms — PM emits ``acronyms: []``
    (key present, confirmed live, #47 CR), so an empty sync must prune every row
    rather than leave a phantom former acronym behind."""
    org = await _add_org(db_session)
    a, b = str(ULID()), str(ULID())
    await sync_org_acronyms(
        db_session, organization_id=org.id, pm_acronyms=[_pm_acronym(a), _pm_acronym(b)]
    )
    await sync_org_acronyms(db_session, organization_id=org.id, pm_acronyms=[])
    assert await _acronyms_for(db_session, org.id) == []


# --- descriptor wiring -------------------------------------------------------


def _pm_org_with_acronyms(pm_id, *, name="PM Canonical", acronyms=None):
    return {
        "id": str(pm_id),
        "name": name,
        "parent_id": None,
        "jurisdiction_affiliations": [],
        "updated_at": "2030-01-01T00:00:00Z",
        "active": True,
        "acronyms": acronyms or [],
    }


async def test_upsert_mirrors_embedded_acronyms(db_session):
    """``upsert_from_pm`` mirrors the embedded ``acronyms[]`` into the child table."""
    pm_id = ULID()
    org = await _add_org(db_session, source_id="C-7", name="Adapter Name")
    org.pm_organization_id = pm_id
    await db_session.flush()

    canonical, former = str(ULID()), str(ULID())
    record = _pm_org_with_acronyms(
        pm_id,
        acronyms=[
            _pm_acronym(canonical, acronym="APP"),
            _pm_acronym(former, acronym="WM", is_canonical=False),
        ],
    )
    result = await OrganizationDescriptor().upsert_from_pm(db_session, record, existing=org)

    assert result is org
    rows = await _acronyms_for(db_session, org.id)
    assert {r.source_id for r in rows} == {canonical, former}


async def test_upsert_empty_acronyms_prunes_existing(db_session):
    """A record with ``acronyms: []`` (PM's zero-acronym shape) prunes prior rows —
    the guard passes on the present key, then ``sync_org_acronyms([])`` clears them."""
    pm_id = ULID()
    org = await _add_org(db_session, source_id="C-9", name="Y")
    org.pm_organization_id = pm_id
    await db_session.flush()
    await sync_org_acronyms(
        db_session, organization_id=org.id, pm_acronyms=[_pm_acronym(str(ULID()))]
    )

    record = _pm_org_with_acronyms(pm_id, name="Y", acronyms=[])
    result = await OrganizationDescriptor().upsert_from_pm(db_session, record, existing=org)
    assert result is org
    assert await _acronyms_for(db_session, org.id) == []


async def test_upsert_handles_record_without_acronyms(db_session):
    """A record with no ``acronyms`` key (e.g. a search-shaped feed bump) is a no-op for
    the acronym mirror — it must not crash or wipe existing rows."""
    pm_id = ULID()
    org = await _add_org(db_session, source_id="C-8", name="X")
    org.pm_organization_id = pm_id
    await db_session.flush()
    record = _pm_org_with_acronyms(pm_id, name="X")
    del record["acronyms"]

    result = await OrganizationDescriptor().upsert_from_pm(db_session, record, existing=org)
    assert result is org
    assert await _acronyms_for(db_session, org.id) == []
