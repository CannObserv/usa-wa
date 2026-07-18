"""One-shot #101 re-source migration — retire usa_wa_pdc House rows onto usa_wa_legislature spans.

The re-partition makes the House Position seat ``usa_wa_legislature``-sourced (symmetric with the
Senate). Existing prod rows built by the retired PDC House emission are ``usa_wa_pdc``-sourced; the
new WSL+SOS builder emits a span whose ``{start}`` can be **deeper** (SOS positions back to 2008;
PDC omits the position before 2018), so the migration maps each PDC row onto the covering
``usa_wa_legislature`` span by ``(person, role)`` + validity window and transfers the PM anchor —
NOT an exact-``source_id`` re-point. Run **after** ``build_house_spans``.
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


async def _assignment(
    session,
    *,
    source,
    source_id,
    person,
    role,
    anchor=None,
    valid_from=date(2013, 1, 1),
    valid_to=None,
    is_active=True,
):
    row = Assignment(
        source=source,
        source_id=source_id,
        person_id=person.id,
        role_id=role.id,
        valid_from=valid_from,
        valid_to=valid_to,
        is_active=is_active,
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


async def test_collapses_shallow_pdc_onto_deep_legislature_keeper(db_session, usa_wa):
    """The central #101 cohort: a cross-2018 incumbent's existing PDC span is SHALLOW
    (…:2019-20 — PDC omits the pre-2018 position) while the SOS builder emits a DEEPER span
    (…:2017-18). Different source_id → the migration must map by (person, role) + window, transfer
    the anchor to the deep keeper, and delete the shallow PDC row (NOT flip it in place, which
    would strand the anchor + duplicate the PM assignment)."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    anchor = _ULID()
    # Existing shallow PDC row (open, anchored), start 2019-20.
    pdc = await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2019-20",
        person=person,
        role=role,
        anchor=anchor,
        valid_from=date(2019, 1, 1),
    )
    await _cite(db_session, usa_wa, pdc)
    pdc_id = pdc.id
    # Deep legislature keeper built by build_house_spans (open, no anchor), start 2017-18.
    keeper = await _assignment(
        db_session,
        source=_WSL,
        source_id="100:chamber-house:ld-5-position-1:2017-18",
        person=person,
        role=role,
        anchor=None,
        valid_from=date(2017, 1, 1),
    )

    result = await migrate_house_source(db_session)

    assert result.retired == 1 and result.anchors_transferred == 1 and result.orphans_no_keeper == 0
    await db_session.refresh(keeper)
    assert keeper.pm_assignment_id == anchor  # anchor moved onto the deep keeper
    # The shallow PDC row + its citations are gone.
    assert (
        await db_session.execute(select(Assignment).where(Assignment.id == pdc_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Citation).where(Citation.entity_id == pdc_id)
        )
        == 0
    )


async def test_collapses_identical_source_id_keeper(db_session, usa_wa):
    """A post-2018-only member: the PDC row and the legislature keeper share the same start
    (SOS can't position them before they existed) — the covering-window match still collapses."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    sid = "100:chamber-house:ld-5-position-1:2021-22"
    anchor = _ULID()
    pdc = await _assignment(
        db_session,
        source=_PDC,
        source_id=sid,
        person=person,
        role=role,
        anchor=anchor,
        valid_from=date(2021, 1, 1),
    )
    keeper = await _assignment(
        db_session,
        source=_WSL,
        source_id=sid,
        person=person,
        role=role,
        anchor=None,
        valid_from=date(2021, 1, 1),
    )
    pdc_id = pdc.id

    result = await migrate_house_source(db_session)

    assert result.retired == 1 and result.anchors_transferred == 1
    await db_session.refresh(keeper)
    assert keeper.pm_assignment_id == anchor
    assert (
        await db_session.execute(select(Assignment).where(Assignment.id == pdc_id))
    ).scalar_one_or_none() is None


async def test_orphan_no_keeper_is_left_alone(db_session, usa_wa):
    """A PDC row whose member the SOS builder couldn't position (no legislature keeper) is left
    in place + counted — deleting it would orphan its live PM assignment."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    pdc = await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2019-20",
        person=person,
        role=role,
        anchor=_ULID(),
        valid_from=date(2019, 1, 1),
    )

    result = await migrate_house_source(db_session)

    assert result.retired == 0 and result.orphans_no_keeper == 1
    await db_session.refresh(pdc)
    assert pdc.source == _PDC  # untouched


async def test_anchor_dropped_when_keeper_already_anchored(db_session, usa_wa):
    """A keeper already carrying a different anchor can't adopt the PDC row's — the PDC row is
    retired but its anchor is dropped (the #80 orphaned-upstream case)."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2019-20",
        person=person,
        role=role,
        anchor=_ULID(),
        valid_from=date(2019, 1, 1),
    )
    keeper = await _assignment(
        db_session,
        source=_WSL,
        source_id="100:chamber-house:ld-5-position-1:2017-18",
        person=person,
        role=role,
        anchor=_ULID(),  # already anchored to a DIFFERENT PM assignment
        valid_from=date(2017, 1, 1),
    )
    keeper_anchor = keeper.pm_assignment_id

    result = await migrate_house_source(db_session)

    assert result.retired == 1 and result.anchors_dropped == 1 and result.anchors_transferred == 0
    await db_session.refresh(keeper)
    assert keeper.pm_assignment_id == keeper_anchor  # keeper keeps its own anchor


async def test_leaves_legacy_3part_pdc_house_row(db_session, usa_wa):
    """A pre-#79 3-part legacy row is migrate_pdc_spans's job — this migration skips it."""
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

    assert result.retired == 0 and result.skipped_legacy == 1
    await db_session.refresh(row)
    assert row.source == _PDC  # untouched


async def test_idempotent(db_session, usa_wa):
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    pdc = await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2019-20",
        person=person,
        role=role,
        anchor=_ULID(),
        valid_from=date(2019, 1, 1),
    )
    await _cite(db_session, usa_wa, pdc)  # citation deleted on retire; exercises the delete path
    await _assignment(
        db_session,
        source=_WSL,
        source_id="100:chamber-house:ld-5-position-1:2017-18",
        person=person,
        role=role,
        valid_from=date(2017, 1, 1),
    )

    await migrate_house_source(db_session)
    second = await migrate_house_source(db_session)

    assert second.retired == 0 and second.pdc_house_found == 0


async def test_collapses_closed_historical_row_onto_closed_keeper(db_session, usa_wa):
    """The prod-dominant case: a departed member's PDC House seat is CLOSED (is_active=False,
    valid_to set). It still collapses onto the equally-closed usa_wa_legislature keeper by the
    valid_to-bounded window — the migration is is_active-agnostic (window match, not liveness)."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    anchor = _ULID()
    pdc = await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2013-14",
        person=person,
        role=role,
        anchor=anchor,
        valid_from=date(2013, 1, 1),
        valid_to=date(2016, 12, 31),
        is_active=False,
    )
    pdc_id = pdc.id
    keeper = await _assignment(
        db_session,
        source=_WSL,
        source_id="100:chamber-house:ld-5-position-1:2011-12",
        person=person,
        role=role,
        anchor=None,
        valid_from=date(2011, 1, 1),
        valid_to=date(2016, 12, 31),  # window contains the PDC row's 2013 start
        is_active=False,
    )

    result = await migrate_house_source(db_session)

    assert result.retired == 1 and result.anchors_transferred == 1
    await db_session.refresh(keeper)
    assert keeper.pm_assignment_id == anchor  # anchor moved onto the closed keeper
    assert (
        await db_session.execute(select(Assignment).where(Assignment.id == pdc_id))
    ).scalar_one_or_none() is None


async def test_orphan_is_stable_across_reruns(db_session, usa_wa):
    """A no-keeper orphan is re-left, not retired, on every run — idempotency is convergence
    (retired=0), NOT "pdc_house_found drops to 0" (the #101 CR round 3 docstring fix)."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2019-20",
        person=person,
        role=role,
        anchor=_ULID(),
        valid_from=date(2019, 1, 1),
    )  # no usa_wa_legislature keeper → orphan

    first = await migrate_house_source(db_session)
    second = await migrate_house_source(db_session)

    assert first.orphans_no_keeper == 1 and first.retired == 0
    # Re-run: the orphan is found again (pdc_house_found stays 1) but nothing new is retired.
    assert second.pdc_house_found == 1 and second.orphans_no_keeper == 1 and second.retired == 0


async def test_disambiguates_two_keepers_by_window(db_session, usa_wa):
    """A member who served, left, and returned has TWO disjoint usa_wa_legislature tenures of the
    same seat. Each stranded PDC row must retire onto the keeper whose window contains ITS start —
    the covering-window match, not a blind first-of-list keeper."""
    person = await _person(db_session, 100)
    role = await _role(db_session, usa_wa, "5-1")
    early_anchor, late_anchor = _ULID(), _ULID()
    # Two PDC rows: an early closed tenure + a later open tenure of the same seat.
    await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2013-14",
        person=person,
        role=role,
        anchor=early_anchor,
        valid_from=date(2013, 1, 1),
        valid_to=date(2016, 12, 31),
        is_active=False,
    )
    await _assignment(
        db_session,
        source=_PDC,
        source_id="100:chamber-house:ld-5-position-1:2021-22",
        person=person,
        role=role,
        anchor=late_anchor,
        valid_from=date(2021, 1, 1),
    )
    # Two matching legislature keepers with disjoint windows (neither contains the other's start).
    early_keeper = await _assignment(
        db_session,
        source=_WSL,
        source_id="100:chamber-house:ld-5-position-1:2013-14",
        person=person,
        role=role,
        anchor=None,
        valid_from=date(2013, 1, 1),
        valid_to=date(2016, 12, 31),
        is_active=False,
    )
    late_keeper = await _assignment(
        db_session,
        source=_WSL,
        source_id="100:chamber-house:ld-5-position-1:2021-22",
        person=person,
        role=role,
        anchor=None,
        valid_from=date(2021, 1, 1),
    )

    result = await migrate_house_source(db_session)

    assert result.retired == 2 and result.anchors_transferred == 2
    await db_session.refresh(early_keeper)
    await db_session.refresh(late_keeper)
    assert early_keeper.pm_assignment_id == early_anchor  # each anchor lands in its own window
    assert late_keeper.pm_assignment_id == late_anchor


async def test_main_requires_owner_role(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    with patch.object(migrate_module, "configure_logging"):
        code = await migrate_module._main([])
    assert code == 2
    assert "DATABASE_URL_OWNER is not set" in capsys.readouterr().err
