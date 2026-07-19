"""WSL+SOS House Position span emission (#101) — spans → merged usa_wa_legislature Assignments.

Binds the House-position spans to the generic emitter: one Assignment per tenure, bound to the
WSL Person + the ``state_representative`` seat Role, ``usa_wa_legislature``-sourced (the seat
authority since #101), citing each biennium's ``sos-whofiled:<YYYYMM>`` filing cohort.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.normalize.pdc_matching import build_house_roster
from usa_wa_adapter_pdc.normalize.pdc_observations import build_house_position_observations
from usa_wa_adapter_sos.house.emit import emit_house_position_spans

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.tenure_spans import build_tenure_spans

CURRENT = "2025-26"


@pytest.fixture
async def anchors(db_session, usa_wa):
    return await bootstrap_synthetic_anchors(
        db_session, biennium=CURRENT, jurisdiction_id=usa_wa.id
    )


async def _add_ld(session, usa_wa, n):
    session.add(
        Jurisdiction(
            slug=f"usa-wa-ld-{n}",
            name=f"LD {n}",
            type_id=usa_wa.type_id,
            pm_jurisdiction_id=_ULID(),
            recorded_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _add_person(session, mid):
    row = Person(source="usa_wa_legislature", source_id=str(mid), name_full="Ann Rivers")
    session.add(row)
    await session.flush()
    return row


async def _sos_events(session, usa_wa, years):
    """One archived sos-whofiled FetchEvent per election year; return {biennium: target}."""
    source = Source(jurisdiction_id=usa_wa.id, name="SOS", slug="usa_wa_sos", kind="rest")
    session.add(source)
    await session.flush()
    out = {}
    for year, biennium in years.items():
        rid = f"sos-whofiled:{year}11"
        ev = FetchEvent(
            source_id=source.id,
            resource_id=rid,
            url="https://x",
            fetched_at=datetime.now(UTC),
            http_status=200,
            content_hash=b"\x01" * 32,
            status=FetchStatus.ok,
        )
        session.add(ev)
        await session.flush()
        out[biennium] = (ev.id, ev.fetched_at, rid)
    return out


def _winner(pdc_id, ld, position, filer):
    return {
        "person_id": pdc_id,
        "legislative_district": str(ld),
        "position": str(position),
        "filer_name": filer,
        "party_code": "DEMOCRAT",
    }


def _sponsor(mid, ld, last):
    return {
        "Id": mid,
        "FirstName": "Ann",
        "LastName": last,
        "District": str(ld),
        "Agency": "House",
        "Party": "D",
    }


async def _count(session, model, **where):
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalar()


async def test_house_span_is_legislature_sourced_on_a_wsl_person_seat_role(
    db_session, usa_wa, anchors
):
    """A member seated LD5 Pos1 across two bienniums → one merged, open, usa_wa_legislature
    Assignment bound to the WSL Person and the state_representative seat Role, citing both
    cohorts. The default assignment_source is usa_wa_legislature (#101)."""
    await _add_ld(db_session, usa_wa, 5)
    person = await _add_person(db_session, 100)
    events = await _sos_events(db_session, usa_wa, {2022: "2023-24", 2024: CURRENT})
    house = build_house_roster([_sponsor(100, 5, "Rivers")])
    obs = []
    for biennium in ("2023-24", CURRENT):
        obs += build_house_position_observations(
            [_winner("900", 5, 1, "Ann Rivers")],
            house_roster=house,
            senate_roster={},
            biennium=biennium,
        ).observations
    spans = build_tenure_spans(obs, current_biennium=CURRENT)

    emitted = await emit_house_position_spans(
        db_session, spans, anchors=anchors, reliability=1.0, fetch_events=events
    )

    assert emitted == 1
    row = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.source_id == "100:chamber-house:ld-5-position-1:2023-24"
            )
        )
    ).scalar_one()
    assert row.source == "usa_wa_legislature"
    assert row.person_id == person.id
    assert row.valid_from == date(2023, 1, 1) and row.valid_to is None and row.is_active is True
    role = (await db_session.execute(select(Role).where(Role.id == row.role_id))).scalar_one()
    assert role.role_type == "state_representative" and role.qualifier == "Position 1"
    assert role.organization_id == anchors.house_id
    assert await _count(db_session, Citation, entity_id=row.id) == 2  # cite every biennium


async def test_unsynced_ld_skips_the_span(db_session, usa_wa, anchors):
    await _add_person(db_session, 100)  # LD 5 jurisdiction NOT added
    events = await _sos_events(db_session, usa_wa, {2024: CURRENT})
    house = build_house_roster([_sponsor(100, 5, "Rivers")])
    obs = build_house_position_observations(
        [_winner("900", 5, 1, "Ann Rivers")], house_roster=house, senate_roster={}, biennium=CURRENT
    ).observations
    spans = build_tenure_spans(obs, current_biennium=CURRENT)

    emitted = await emit_house_position_spans(
        db_session, spans, anchors=anchors, reliability=1.0, fetch_events=events
    )

    assert emitted == 0
    assert await _count(db_session, Assignment) == 0
