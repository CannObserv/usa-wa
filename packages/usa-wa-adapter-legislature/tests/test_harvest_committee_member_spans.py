"""End-to-end Phase B committee-membership span build (#82).

Drives the pipeline offline — archived committee-members-hist rosters → cohort provider
re-parse → membership observations → span builder → emission — and asserts merged spans with
per-(biennium, committee) citations. Also pins the daily re-drive's cohort scoping.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from clearinghouse_domain_legislative.operator_events import KIND_DEPARTED
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.harvest_committee_member_spans import build_committee_member_spans
from usa_wa_adapter_legislature.operator_events_store import (
    get_or_create_operator_source,
    record_operator_event,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction

CURRENT = "2025-26"
CID = "31635"


def _member(mid):
    return {
        "Id": mid,
        "FirstName": "Timm",
        "LastName": "Ormsby",
        "Name": "Timm Ormsby",
        "Agency": "House",
        "Party": "Democrat",
        "District": "3",
    }


class _WireMappingMemberClient:
    """The archived wire encodes its (biennium, committee) so the fake can route it."""

    def __init__(self, rosters):
        self._rosters = rosters  # {(biennium, cid): [rows]}

    async def parse_historical_committee_members(self, wire):
        biennium, cid = wire.decode().removeprefix("<r:").removesuffix("/>").split("|")
        return self._rosters.get((biennium, cid), [])


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


async def _archive(db_session, source, biennium, cid=CID, name="Appropriations"):
    resource_id = committee_members_hist_resource_id(biennium, cid, "House", name)
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes([hash(resource_id) & 0xFF]) * 32,
        status=FetchStatus.ok,
    )
    db_session.add(ev)
    await db_session.flush()
    body = f"<r:{biennium}|{cid}/>".encode()
    db_session.add(
        RawPayload(fetch_event_id=ev.id, content_type="text/xml", body=body, size_bytes=len(body))
    )
    await db_session.flush()
    return ev.id


async def _add_committee(session, usa_wa, source_id=CID):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        jurisdiction_id=usa_wa.id,
        name="House Appropriations",
        short_name="Appropriations",
        org_type="committee",
    )
    session.add(row)
    await session.flush()
    return row


async def _add_person(session, mid):
    session.add(Person(source="usa_wa_legislature", source_id=str(mid), name_full="Timm Ormsby"))
    await session.flush()


async def test_phase_b_builds_merged_membership_spans_from_archive(db_session, usa_wa, wsl_source):
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    await _archive(db_session, wsl_source, "2023-24")
    await _archive(db_session, wsl_source, CURRENT)
    client = _WireMappingMemberClient(
        {("2023-24", CID): [_member(100)], (CURRENT, CID): [_member(100)]}
    )

    result = await build_committee_member_spans(
        db_session, member_client=client, current_biennium=CURRENT
    )

    assert result.emitted == 1  # one merged membership span across both biennia
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2023-24")
        )
    ).scalar_one()
    assert row.valid_from == date(2023, 1, 1)
    assert row.valid_to is None and row.is_active is True
    # one citation per (biennium, committee) roster
    assert (
        await db_session.execute(
            select(func.count()).select_from(Citation).where(Citation.entity_id == row.id)
        )
    ).scalar() == 2


async def test_operator_departed_closes_committee_membership(db_session, usa_wa, wsl_source):
    """#107: a departed event closes the member's committee membership span at the death date
    with a field-level operator citation (a dead member leaves every committee)."""
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    await _archive(db_session, wsl_source, CURRENT)
    client = _WireMappingMemberClient({(CURRENT, CID): [_member(100)]})

    juris = await resolve_jurisdiction(db_session)
    op_source = await get_or_create_operator_source(db_session, juris)
    await record_operator_event(
        db_session,
        op_source,
        member_id="100",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://example.gov/member",
    )

    await build_committee_member_spans(db_session, member_client=client, current_biennium=CURRENT)

    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2025-26")
        )
    ).scalar_one()
    assert row.valid_to == date(2025, 4, 19) and row.is_active is False
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Citation)
            .where(Citation.entity_id == row.id, Citation.field_path == "valid_to")
        )
        == 1
    )


async def test_phase_b_no_archive_emits_nothing(db_session, usa_wa, wsl_source):
    result = await build_committee_member_spans(
        db_session, member_client=_WireMappingMemberClient({}), current_biennium=CURRENT
    )
    assert result.emitted == 0


async def test_restrict_to_biennium_scopes_to_current_memberships(db_session, usa_wa, wsl_source):
    """#82 daily re-drive: rebuild only (member, committee) pairs in the current roster —
    a member who left the committee before the current biennium is not re-asserted."""
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    await _add_person(db_session, 200)
    await _archive(db_session, wsl_source, "2023-24")
    await _archive(db_session, wsl_source, CURRENT)
    client = _WireMappingMemberClient(
        {
            ("2023-24", CID): [_member(100), _member(200)],  # 200 departed after 2023-24
            (CURRENT, CID): [_member(100)],
        }
    )

    result = await build_committee_member_spans(
        db_session,
        member_client=client,
        current_biennium=CURRENT,
        restrict_to_biennium=CURRENT,
    )

    assert result.emitted == 1  # only member 100's membership span
    members = {
        a.source_id.split(":")[0]
        for a in (await db_session.execute(select(Assignment))).scalars().all()
    }
    assert members == {"100"}


async def test_restricted_rebuild_closes_stale_membership_of_sitting_member(
    db_session, usa_wa, wsl_source
):
    """#83, the committee-switch case: member 200 is still seated but LEFT this committee at
    the boundary — the (member, committee) pair vanishes from the current roster, so the
    restricted re-drive must close their open membership span (not just full departures)."""
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    await _add_person(db_session, 200)
    await _archive(db_session, wsl_source, "2023-24")
    client = _WireMappingMemberClient(
        {
            ("2023-24", CID): [_member(100), _member(200)],
            (CURRENT, CID): [_member(100)],  # 200 switched committees; still a legislator
        }
    )

    # Sitting-era build: both memberships open.
    await build_committee_member_spans(db_session, member_client=client, current_biennium="2023-24")
    stale = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"200:committee:{CID}:2023-24")
        )
    ).scalar_one()
    assert stale.is_active is True and stale.valid_to is None

    # Current biennium roster no longer holds the (200, CID) pair → span must close.
    await _archive(db_session, wsl_source, CURRENT)
    await build_committee_member_spans(
        db_session,
        member_client=client,
        current_biennium=CURRENT,
        restrict_to_biennium=CURRENT,
    )

    assert stale.is_active is False
    assert stale.valid_to == date(2024, 12, 31)
    kept = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2023-24")
        )
    ).scalar_one()
    assert kept.is_active is True and kept.valid_to is None


async def test_max_close_fraction_threads_through_the_builder(db_session, usa_wa, wsl_source):
    """#83 CR round 2 (the WSL committee re-key case): a wholesale committee Id re-key makes
    every old-Id membership span stale at once — the builder must forward the operator's
    raised ``max_close_fraction`` so the legitimate mass close can run."""
    committee = await _add_committee(db_session, usa_wa)
    await _add_person(db_session, 100)
    await _archive(db_session, wsl_source, CURRENT)
    client = _WireMappingMemberClient({(CURRENT, CID): [_member(100)]})
    role = Role(
        source="usa_wa_legislature",
        source_id=f"test-stale-role:{CID}",
        organization_id=committee.id,
        name="Stale Member",
        role_type="member",
    )
    db_session.add(role)
    await db_session.flush()
    stale = []
    for mid in range(900, 906):
        person = Person(
            source="usa_wa_legislature", source_id=str(mid), name_full=f"Departed {mid}"
        )
        db_session.add(person)
        await db_session.flush()
        row = Assignment(
            source="usa_wa_legislature",
            source_id=f"{mid}:committee:{CID}:2021-22",
            person_id=person.id,
            role_id=role.id,
            valid_from=date(2021, 1, 1),
            valid_to=None,
            is_active=True,
        )
        db_session.add(row)
        stale.append(row)
    await db_session.flush()

    # Default fraction aborts (6 of 7 open memberships stale)...
    result = await build_committee_member_spans(
        db_session, member_client=client, current_biennium=CURRENT, restrict_to_biennium=CURRENT
    )
    assert result.sweep_aborted is True and result.closed_stale == 0
    assert all(r.is_active for r in stale)

    # ...the override closes them.
    result = await build_committee_member_spans(
        db_session,
        member_client=client,
        current_biennium=CURRENT,
        restrict_to_biennium=CURRENT,
        max_close_fraction=1.0,
    )
    assert result.sweep_aborted is False and result.closed_stale == 6
    assert all(not r.is_active for r in stale)
