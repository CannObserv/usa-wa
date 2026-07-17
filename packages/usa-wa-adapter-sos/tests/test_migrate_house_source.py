"""One-shot #101 re-source migration — usa_wa_pdc House rows → usa_wa_legislature.

The re-partition makes the House Position seat ``usa_wa_legislature``-sourced (symmetric with the
Senate). Existing prod rows built by the retired PDC House emission are ``usa_wa_pdc``-sourced;
the new WSL+SOS builder emits the **identical** 4-part ``source_id`` discriminator, so the common
case is an in-place ``source`` flip (PM anchor + citations ride along — PM keys on
``(person, role, start)``, all unchanged). A pre-existing ``usa_wa_legislature`` row with the same
``source_id`` (out-of-order: the new builder ran first) collapses via the index-safe anchor
transfer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch

from sqlalchemy import func, select
from ulid import ULID as _ULID
from usa_wa_adapter_sos import migrate_house_source as migrate_module
from usa_wa_adapter_sos.migrate_house_source import migrate_house_source

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role

_PDC = "usa_wa_pdc"
_WSL = "usa_wa_legislature"
_KIND = "chamber-house"


async def _person(session, mid):
    p = Person(source=_WSL, source_id=str(mid), name_full=f"M{mid}")
    session.add(p)
    await session.flush()
    return p


async def _role(session, usa_wa, suffix):
    org = Organization(
        source=_WSL,
        source_id=f"house-{suffix}",
        jurisdiction_id=usa_wa.id,
        name="House",
        org_type="chamber",
    )
    session.add(org)
    await session.flush()
    role = Role(
        source=_WSL,
        source_id=f"seat-{suffix}",
        organization_id=org.id,
        name="State Representative",
        role_type="state_representative",
    )
    session.add(role)
    await session.flush()
    return role


async def _assignment(session, *, source, source_id, person, role, anchor=None):
    row = Assignment(
        source=source,
        source_id=source_id,
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2013, 1, 1),
        valid_to=None,
        is_active=True,
        pm_assignment_id=anchor,
    )
    session.add(row)
    await session.flush()
    return row


async def _cite(session, usa_wa, assignment):
    src = Source(jurisdiction_id=usa_wa.id, name="PDC", slug=_PDC, kind="rest")
    session.add(src)
    await session.flush()
    ev = FetchEvent(
        source_id=src.id,
        resource_id="house-winners:2012",
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x02" * 32,
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(
        Citation(
            entity_type="assignment",
            entity_id=assignment.id,
            fetch_event_id=ev.id,
            confidence=1.0,
            asserted_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def test_resources_pdc_house_span_in_place(db_session, usa_wa):
    """No usa_wa_legislature counterpart → the PDC row's source flips in place, keeping its id,
    PM anchor, and citations."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    anchor = _ULID()
    row = await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2013-14",
        person=person,
        role=role,
        anchor=anchor,
    )
    await _cite(db_session, usa_wa, row)
    row_id = row.id

    result = await migrate_house_source(db_session)

    assert result.resourced == 1 and result.collapsed == 0
    await db_session.refresh(row)
    assert row.id == row_id  # same row
    assert row.source == _WSL
    assert row.pm_assignment_id == anchor  # anchor rides along
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Citation).where(Citation.entity_id == row_id)
        )
        == 1
    )  # citation preserved


async def test_collapses_onto_existing_legislature_row(db_session, usa_wa):
    """A usa_wa_legislature row with the same source_id already exists (new builder ran first) →
    the PDC row's anchor transfers onto it, the PDC row + its citations are deleted."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    sid = "100:chamber-house:ld-5-position-1:2013-14"
    anchor = _ULID()
    pdc_row = await _assignment(
        db_session, source=_PDC, source_id=sid, person=person, role=role, anchor=anchor
    )
    await _cite(db_session, usa_wa, pdc_row)
    leg_row = await _assignment(
        db_session, source=_WSL, source_id=sid, person=person, role=role, anchor=None
    )
    pdc_id = pdc_row.id

    result = await migrate_house_source(db_session)

    assert result.collapsed == 1 and result.anchors_transferred == 1 and result.resourced == 0
    await db_session.refresh(leg_row)
    assert leg_row.pm_assignment_id == anchor  # transferred
    assert (
        await db_session.execute(select(Assignment).where(Assignment.id == pdc_id))
    ).scalar_one_or_none() is None  # PDC row deleted


async def test_leaves_legacy_3part_pdc_house_row(db_session, usa_wa):
    """A pre-#79 3-part legacy row is migrate_pdc_spans's job — this migration skips it (its
    source_id has no 4-part legislature counterpart the stale sweep would maintain)."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    row = await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:2013-14",  # 3-part legacy
        person=person,
        role=role,
    )

    result = await migrate_house_source(db_session)

    assert result.resourced == 0 and result.collapsed == 0 and result.skipped_legacy == 1
    await db_session.refresh(row)
    assert row.source == _PDC  # untouched


async def test_idempotent(db_session, usa_wa):
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    row = await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2013-14",
        person=person,
        role=role,
        anchor=_ULID(),
    )
    await _cite(db_session, usa_wa, row)

    await migrate_house_source(db_session)
    second = await migrate_house_source(db_session)

    assert second.resourced == 0 and second.collapsed == 0 and second.pdc_house_found == 0


async def test_main_requires_owner_role(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    with patch.object(migrate_module, "configure_logging"):
        code = await migrate_module._main([])
    assert code == 2
    assert "DATABASE_URL_OWNER is not set" in capsys.readouterr().err
