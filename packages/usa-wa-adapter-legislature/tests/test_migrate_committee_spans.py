"""Committee-membership migration (#82): retire per-biennium rows stranded by deeper spans.

The span key shares the legacy key's 4-part shape, so a span starting at the legacy row's
biennium upserts it *in place* (shallow archive → nothing to migrate). Once the harvest
deepens a span past that biennium, the legacy row is stranded and gets retired onto the
covering span, carrying its PM anchor.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person
from usa_wa_adapter_legislature import migrate_committee_spans as migrate_module
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.migrate_committee_spans import (
    MigrationResult,
    migrate_committee_spans,
)

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
    def __init__(self, rosters):
        self._rosters = rosters

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


async def _archive(db_session, source, biennium, cid=CID):
    resource_id = committee_members_hist_resource_id(biennium, cid, "House", "Appropriations")
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


async def _add_committee(session, usa_wa):
    row = Organization(
        source="usa_wa_legislature",
        source_id=CID,
        jurisdiction_id=usa_wa.id,
        name="House Appropriations",
        short_name="Appropriations",
        org_type="committee",
    )
    session.add(row)
    await session.flush()
    return row


async def _add_person(session, mid=100):
    row = Person(source="usa_wa_legislature", source_id=str(mid), name_full="Timm Ormsby")
    session.add(row)
    await session.flush()
    return row


async def _add_legacy(session, *, source_id, person_id, role_id, pm_id, valid_from):
    row = Assignment(
        source="usa_wa_legislature",
        source_id=source_id,
        person_id=person_id,
        role_id=role_id,
        valid_from=valid_from,
        valid_to=None,
        is_active=True,
        pm_assignment_id=pm_id,
    )
    session.add(row)
    await session.flush()
    return row


async def _count(session, model, **where):
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalar()


async def test_shallow_archive_upserts_legacy_row_in_place_nothing_stranded(
    db_session, usa_wa, wsl_source
):
    """Only the current biennium archived → the span key EQUALS the legacy key, so the row is
    upgraded in place (keeping its id + PM anchor). Zero legacy found, zero retired."""
    committee = await _add_committee(db_session, usa_wa)
    person = await _add_person(db_session)
    await _archive(db_session, wsl_source, CURRENT)
    client = _WireMappingMemberClient({(CURRENT, CID): [_member(100)]})

    # First build creates the span row; capture its identity + give it a PM anchor.
    result = await migrate_committee_spans(
        db_session, member_client=client, current_biennium=CURRENT
    )

    assert result.legacy_found == 0 and result.legacy_retired == 0
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:{CURRENT}")
        )
    ).scalar_one()
    assert row.person_id == person.id
    assert row.role_id is not None and committee.id is not None
    assert await _count(db_session, Assignment) == 1


async def test_deeper_span_strands_legacy_row_which_is_retired_with_its_anchor(
    db_session, usa_wa, wsl_source
):
    """After the harvest deepens the span to 2013-14, the shipped 2025-26 row is stranded →
    retired onto the covering span, transferring its pm_assignment_id."""
    await _add_committee(db_session, usa_wa)
    person = await _add_person(db_session)
    for biennium in ("2013-14", "2015-16", CURRENT):
        await _archive(db_session, wsl_source, biennium)
    # a contiguous run 2013→2025 (2017-2023 also present so there's no gap)
    for biennium in ("2017-18", "2019-20", "2021-22", "2023-24"):
        await _archive(db_session, wsl_source, biennium)
    rosters = {
        (b, CID): [_member(100)]
        for b in ("2013-14", "2015-16", "2017-18", "2019-20", "2021-22", "2023-24", CURRENT)
    }
    client = _WireMappingMemberClient(rosters)

    # Seed the shipped per-biennium row (as prod has today) with a PM anchor.
    # Build spans once to create the committee `member` Role, then attach the legacy row to it.
    await migrate_committee_spans(db_session, member_client=client, current_biennium=CURRENT)
    span = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2013-14")
        )
    ).scalar_one()
    pm_id = _ULID()
    await _add_legacy(
        db_session,
        source_id=f"100:committee:{CID}:{CURRENT}",  # the stranded shipped row
        person_id=person.id,
        role_id=span.role_id,
        pm_id=pm_id,
        valid_from=date(2025, 1, 1),
    )
    span.pm_assignment_id = None
    await db_session.flush()

    result = await migrate_committee_spans(
        db_session, member_client=client, current_biennium=CURRENT
    )

    assert (result.legacy_found, result.anchors_transferred, result.legacy_retired) == (1, 1, 1)
    assert result.orphans_no_span == 0
    assert await _count(db_session, Assignment, source_id=f"100:committee:{CID}:{CURRENT}") == 0
    await db_session.refresh(span)
    assert span.pm_assignment_id == pm_id  # anchor moved to the covering span
    assert await _count(db_session, Assignment, pm_assignment_id=pm_id) == 1


async def test_already_anchored_span_records_the_dropped_legacy_anchor(
    db_session, usa_wa, wsl_source
):
    """If the daily re-drive anchored the deepened span before this migration ran, the legacy
    row's own PM anchor cannot be transferred — retiring it orphans that PM assignment
    upstream. That must be counted + warned, never silent."""
    await _add_committee(db_session, usa_wa)
    person = await _add_person(db_session)
    for biennium in ("2013-14", "2015-16", "2017-18", "2019-20", "2021-22", "2023-24", CURRENT):
        await _archive(db_session, wsl_source, biennium)
    rosters = {
        (b, CID): [_member(100)]
        for b in ("2013-14", "2015-16", "2017-18", "2019-20", "2021-22", "2023-24", CURRENT)
    }
    client = _WireMappingMemberClient(rosters)

    await migrate_committee_spans(db_session, member_client=client, current_biennium=CURRENT)
    span = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2013-14")
        )
    ).scalar_one()
    span_anchor = _ULID()
    span.pm_assignment_id = span_anchor  # the sidecar got there first
    legacy_anchor = _ULID()
    await _add_legacy(
        db_session,
        source_id=f"100:committee:{CID}:{CURRENT}",
        person_id=person.id,
        role_id=span.role_id,
        pm_id=legacy_anchor,
        valid_from=date(2025, 1, 1),
    )
    await db_session.flush()

    result = await migrate_committee_spans(
        db_session, member_client=client, current_biennium=CURRENT
    )

    assert result.legacy_retired == 1
    assert result.anchors_transferred == 0
    assert result.anchors_dropped == 1  # the orphaned PM assignment, surfaced
    await db_session.refresh(span)
    assert span.pm_assignment_id == span_anchor  # the span keeps its own anchor
    assert await _count(db_session, Assignment, pm_assignment_id=legacy_anchor) == 0


async def test_legacy_and_span_sharing_one_anchor_retires_cleanly(db_session, usa_wa, wsl_source):
    """PM's structural (person, role, start_date) match can fold the deepened span and its
    legacy row onto the *same* pm_assignment_id (#78-3's double-anchor case). Retiring the
    legacy row is then a clean collapse — no anchor to transfer, none dropped."""
    await _add_committee(db_session, usa_wa)
    person = await _add_person(db_session)
    for biennium in ("2013-14", "2015-16", "2017-18", "2019-20", "2021-22", "2023-24", CURRENT):
        await _archive(db_session, wsl_source, biennium)
    rosters = {
        (b, CID): [_member(100)]
        for b in ("2013-14", "2015-16", "2017-18", "2019-20", "2021-22", "2023-24", CURRENT)
    }
    client = _WireMappingMemberClient(rosters)

    await migrate_committee_spans(db_session, member_client=client, current_biennium=CURRENT)
    span = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:2013-14")
        )
    ).scalar_one()
    shared = _ULID()
    span.pm_assignment_id = shared
    await _add_legacy(
        db_session,
        source_id=f"100:committee:{CID}:{CURRENT}",
        person_id=person.id,
        role_id=span.role_id,
        pm_id=shared,  # same anchor as the span (PM folded them)
        valid_from=date(2025, 1, 1),
    )
    await db_session.flush()

    result = await migrate_committee_spans(
        db_session, member_client=client, current_biennium=CURRENT
    )

    assert (result.legacy_retired, result.anchors_transferred, result.anchors_dropped) == (1, 0, 0)
    await db_session.refresh(span)
    assert span.pm_assignment_id == shared
    assert await _count(db_session, Assignment, pm_assignment_id=shared) == 1  # only the span


async def test_non_committee_rows_are_untouched(db_session, usa_wa, wsl_source):
    """party / chamber-senate / chamber-house rows are other issues' spans — never touched."""
    await _add_committee(db_session, usa_wa)
    person = await _add_person(db_session)
    await _archive(db_session, wsl_source, CURRENT)
    client = _WireMappingMemberClient({(CURRENT, CID): [_member(100)]})
    await migrate_committee_spans(db_session, member_client=client, current_biennium=CURRENT)
    span = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == f"100:committee:{CID}:{CURRENT}")
        )
    ).scalar_one()
    for source_id in (f"100:party:democratic:{CURRENT}", f"100:chamber-house:{CURRENT}"):
        await _add_legacy(
            db_session,
            source_id=source_id,
            person_id=person.id,
            role_id=span.role_id,
            pm_id=_ULID(),
            valid_from=date(2025, 1, 1),
        )

    result = await migrate_committee_spans(
        db_session, member_client=client, current_biennium=CURRENT
    )

    assert result.legacy_found == 0
    assert await _count(db_session, Assignment, source_id=f"100:party:democratic:{CURRENT}") == 1
    assert await _count(db_session, Assignment, source_id=f"100:chamber-house:{CURRENT}") == 1


async def test_migration_is_idempotent(db_session, usa_wa, wsl_source):
    await _add_committee(db_session, usa_wa)
    await _add_person(db_session)
    await _archive(db_session, wsl_source, CURRENT)
    client = _WireMappingMemberClient({(CURRENT, CID): [_member(100)]})

    first = await migrate_committee_spans(
        db_session, member_client=client, current_biennium=CURRENT
    )
    second = await migrate_committee_spans(
        db_session, member_client=client, current_biennium=CURRENT
    )

    assert first.spans_built == 1 and second.spans_built == 1
    assert second.legacy_found == 0 and second.legacy_retired == 0
    assert await _count(db_session, Assignment) == 1
    span = (await db_session.execute(select(Assignment))).scalar_one()
    assert await _count(db_session, Citation, entity_id=span.id) == 1  # append-only


# --- CLI ----------------------------------------------------------------------


async def test_main_returns_2_when_owner_url_unset(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    with patch.object(migrate_module, "configure_logging"):
        code = await migrate_module._main([])
    assert code == 2
    assert "DATABASE_URL_OWNER is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back_and_returns_0(monkeypatch, capsys, test_engine):
    monkeypatch.setenv("DATABASE_URL_OWNER", os.environ["TEST_DATABASE_URL"])
    fake = MigrationResult(
        spans_built=5, legacy_found=2, anchors_transferred=2, legacy_retired=2, orphans_no_span=0
    )

    async def _fake_migrate(session, **_kwargs):
        return fake

    with (
        patch.object(migrate_module, "configure_logging"),
        patch.object(migrate_module, "migrate_committee_spans", _fake_migrate),
    ):
        code = await migrate_module._main(["--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "legacy_found=2 anchors_transferred=2 retired=2" in out
    assert "dry-run, rolled back" in out
