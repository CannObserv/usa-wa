"""Committee-membership spans (#82): roster observations → merged Assignment spans.

Projects archived per-(biennium, committee) rosters into tenure observations, merges
contiguous biennia into one membership span, and emits one Assignment per tenure bound to
the committee's shared ``member`` Role — citing each (biennium, committee) roster it was
observed in. A dormancy gap opens a second span; an un-ingested committee is skipped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.committee_membership_observations import (
    KIND_COMMITTEE,
    build_committee_membership_observations,
)
from usa_wa_adapter_legislature.committee_span_emit import emit_committee_spans
from usa_wa_adapter_legislature.tenure_spans import build_tenure_spans

CURRENT = "2025-26"
CID = "31635"


def _member(mid, *, first="Timm", last="Ormsby", agency="House", party="Democrat"):
    return {
        "Id": mid,
        "FirstName": first,
        "LastName": last,
        "Name": f"{first} {last}",
        "Agency": agency,
        "Party": party,
        "District": "3",
    }


def _blanked(mid):
    return {"Id": mid, "FirstName": None, "LastName": None, "Name": " ", "Agency": "House"}


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(
        jurisdiction_id=usa_wa.id,
        name="WSL",
        slug="usa_wa_legislature",
        kind="soap",
        reliability=1.0,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _add_committee(session, usa_wa, source_id=CID, name="House Appropriations"):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        jurisdiction_id=usa_wa.id,
        name=name,
        short_name="Appropriations",
        org_type="committee",
    )
    session.add(row)
    await session.flush()
    return row


async def _add_person(session, mid):
    row = Person(source="usa_wa_legislature", source_id=str(mid), name_full="Timm Ormsby")
    session.add(row)
    await session.flush()
    return row


async def _fetch_events(session, source, bienniums, committee_id=CID):
    """One archived committee-roster FetchEvent per (biennium, committee)."""
    out = {}
    for b in bienniums:
        resource_id = committee_members_hist_resource_id(b, committee_id, "House", "Appropriations")
        ev = FetchEvent(
            source_id=source.id,
            resource_id=resource_id,
            url="https://x",
            fetched_at=datetime.now(UTC),
            http_status=200,
            content_hash=b"\x01" * 32,
            status=FetchStatus.ok,
        )
        session.add(ev)
        await session.flush()
        out[(b, committee_id)] = (ev.id, ev.fetched_at, resource_id)
    return out


async def _count(session, model, **where):
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalar()


# --- projection ---------------------------------------------------------------


def test_projection_emits_one_observation_per_member_per_biennium():
    obs = build_committee_membership_observations(
        {
            ("2023-24", CID): [_member(100), _member(200, last="Reeves")],
            ("2025-26", CID): [_member(100)],
        }
    )
    assert len(obs) == 3
    assert all(o.kind == KIND_COMMITTEE and o.discriminator == CID for o in obs)
    assert {(o.member_id, o.biennium) for o in obs} == {
        ("100", "2023-24"),
        ("200", "2023-24"),
        ("100", "2025-26"),
    }


def test_projection_skips_name_blanked_stubs():
    obs = build_committee_membership_observations({("2025-26", CID): [_blanked(999), _member(100)]})
    assert [o.member_id for o in obs] == ["100"]


# --- emission -----------------------------------------------------------------


async def test_emits_merged_membership_span_with_per_roster_citations(
    db_session, usa_wa, wsl_source
):
    committee = await _add_committee(db_session, usa_wa)
    person = await _add_person(db_session, 100)
    events = await _fetch_events(db_session, wsl_source, ["2023-24", CURRENT])
    spans = build_tenure_spans(
        build_committee_membership_observations(
            {("2023-24", CID): [_member(100)], (CURRENT, CID): [_member(100)]}
        ),
        current_biennium=CURRENT,
    )

    emitted = await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=events)

    assert emitted == 1  # one merged membership span across both biennia
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2023-24")
        )
    ).scalar_one()
    assert row.person_id == person.id
    assert row.valid_from == date(2023, 1, 1)
    assert row.valid_to is None and row.is_active is True  # reaches current → open
    # bound to the committee's shared `member` Role
    role = (await db_session.execute(select(Role).where(Role.id == row.role_id))).scalar_one()
    assert role.role_type == "member" and role.organization_id == committee.id
    # cite-every-biennium, keyed per (biennium, committee) roster
    assert await _count(db_session, Citation, entity_id=row.id) == 2


async def test_dormancy_gap_opens_a_second_membership_span(db_session, usa_wa, wsl_source):
    """Off the committee for a biennium, then back → two spans, the later one open."""
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    events = await _fetch_events(db_session, wsl_source, ["2019-20", "2023-24", CURRENT])
    spans = build_tenure_spans(
        build_committee_membership_observations(
            {
                ("2019-20", CID): [_member(100)],
                # absent 2021-22 → gap
                ("2023-24", CID): [_member(100)],
                (CURRENT, CID): [_member(100)],
            }
        ),
        current_biennium=CURRENT,
    )

    emitted = await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=events)

    assert emitted == 2
    closed = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2019-20")
        )
    ).scalar_one()
    active = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2023-24")
        )
    ).scalar_one()
    assert closed.valid_to == date(2020, 12, 31) and closed.is_active is False
    assert active.valid_to is None and active.is_active is True
    assert closed.role_id == active.role_id  # same committee seat/role


async def test_unknown_committee_span_is_skipped(db_session, usa_wa, wsl_source):
    """A committee Org never ingested → span skipped + logged, never guessed."""
    await _add_person(db_session, 100)
    events = await _fetch_events(db_session, wsl_source, [CURRENT], committee_id="99999")
    spans = build_tenure_spans(
        build_committee_membership_observations({(CURRENT, "99999"): [_member(100)]}),
        current_biennium=CURRENT,
    )

    emitted = await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=events)

    assert emitted == 0
    assert await _count(db_session, Assignment) == 0


async def test_absent_person_span_is_skipped(db_session, usa_wa, wsl_source):
    await _add_committee(db_session, usa_wa)  # committee exists, Person never ingested
    events = await _fetch_events(db_session, wsl_source, [CURRENT])
    spans = build_tenure_spans(
        build_committee_membership_observations({(CURRENT, CID): [_member(100)]}),
        current_biennium=CURRENT,
    )

    emitted = await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=events)

    assert emitted == 0
    assert await _count(db_session, Assignment) == 0


async def test_reemission_is_append_only_and_idempotent(db_session, usa_wa, wsl_source):
    """Second pass adds no duplicate Assignment and no duplicate citations."""
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    events = await _fetch_events(db_session, wsl_source, ["2023-24", CURRENT])
    spans = build_tenure_spans(
        build_committee_membership_observations(
            {("2023-24", CID): [_member(100)], (CURRENT, CID): [_member(100)]}
        ),
        current_biennium=CURRENT,
    )

    await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=events)
    await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=events)

    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2023-24")
        )
    ).scalar_one()  # exactly one row
    assert await _count(db_session, Citation, entity_id=row.id) == 2  # not 4


async def test_daily_repull_of_the_same_roster_adds_no_citation(db_session, usa_wa, wsl_source):
    """The daily forced pull mints a **fresh FetchEvent** for the same resource. Citation
    idempotency keys on the event's ``resource_id``, not its id — so a re-drive against a new
    event for an already-cited roster appends nothing (the #54 append-only contract would
    otherwise grow one citation per day, and the app role cannot DELETE them)."""
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    spans = build_tenure_spans(
        build_committee_membership_observations({(CURRENT, CID): [_member(100)]}),
        current_biennium=CURRENT,
    )
    day1 = await _fetch_events(db_session, wsl_source, [CURRENT])
    await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=day1)

    day2 = await _fetch_events(db_session, wsl_source, [CURRENT])  # new event, same resource_id
    assert day2[(CURRENT, CID)][0] != day1[(CURRENT, CID)][0]
    await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=day2)

    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:{CURRENT}")
        )
    ).scalar_one()
    assert await _count(db_session, Citation, entity_id=row.id) == 1


async def test_renamed_committee_appends_a_second_citation_for_that_biennium(
    db_session, usa_wa, wsl_source
):
    """A WSL rename changes the short ``Name``, which rides the resource id — so the renamed
    roster is a *different* resource and attests the span separately. Two citations for the
    one biennium is intended (two distinct pulls, both real evidence), not a leak: the
    idempotency key is the resource, and each resource is cited exactly once."""
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    spans = build_tenure_spans(
        build_committee_membership_observations({(CURRENT, CID): [_member(100)]}),
        current_biennium=CURRENT,
    )
    before = await _fetch_events(db_session, wsl_source, [CURRENT])
    await emit_committee_spans(db_session, spans, reliability=1.0, fetch_events=before)

    # same (biennium, committee) key, new short Name → new resource_id
    renamed_rid = committee_members_hist_resource_id(CURRENT, CID, "House", "Approps")
    ev = FetchEvent(
        source_id=wsl_source.id,
        resource_id=renamed_rid,
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x02" * 32,
        status=FetchStatus.ok,
    )
    db_session.add(ev)
    await db_session.flush()
    await emit_committee_spans(
        db_session,
        spans,
        reliability=1.0,
        fetch_events={(CURRENT, CID): (ev.id, ev.fetched_at, renamed_rid)},
    )

    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:{CURRENT}")
        )
    ).scalar_one()
    assert await _count(db_session, Citation, entity_id=row.id) == 2
