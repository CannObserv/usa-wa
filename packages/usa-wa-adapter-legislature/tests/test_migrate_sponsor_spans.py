"""Migration (#78-3): collapse pre-#78 per-biennium sponsor Assignments into merged spans.

The legacy per-biennium party/Senate rows (each carrying a ``pm_assignment_id``) are retired
onto the span that shares their ``(person_id, role_id)`` — the anchor moves to the span so
the local cache holds ONE row per PM assignment. chamber-house (PDC) + committee rows are
left untouched; a legacy row with no successor span is left + counted.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person
from usa_wa_adapter_legislature import migrate_sponsor_spans as migrate_module
from usa_wa_adapter_legislature.harvest_sponsor_spans import build_sponsor_spans
from usa_wa_adapter_legislature.migrate_sponsor_spans import MigrationResult, migrate_sponsor_spans

CURRENT = "2025-26"


class _FakeSponsorClient:
    def __init__(self, roster):
        self._roster = roster

    async def parse_sponsors(self, wire):
        return self._roster

    async def fetch_sponsors(self, biennium):
        raise AssertionError("archive-first — no live pull")


class _WireMappingSponsorClient:
    """Returns a distinct roster per biennium — the archived wire encodes it (`<r:2021-22/>`),
    so a member absent from one biennium (dormancy gap) yields non-contiguous spans."""

    def __init__(self, rosters):
        self._rosters = rosters  # {biennium: [member rows]}

    async def parse_sponsors(self, wire):
        biennium = wire.decode().removeprefix("<r:").removesuffix("/>")
        return self._rosters.get(biennium, [])

    async def fetch_sponsors(self, biennium):
        raise AssertionError("archive-first — no live pull")


def _member(mid, *, agency="Senate", district="5", party="D"):
    return {
        "Id": mid,
        "FirstName": "Ann",
        "LastName": "Rivers",
        "District": district,
        "Party": party,
        "Agency": agency,
        "Name": "Ann Rivers",
    }


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


async def _archive(db_session, source, biennium):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=f"sponsors:{biennium}",
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes([hash(biennium) & 0xFF]) * 32,
        status=FetchStatus.ok,
    )
    db_session.add(ev)
    await db_session.flush()
    body = f"<r:{biennium}/>".encode()  # biennium-tagged so a wire-mapping fake can route it
    db_session.add(
        RawPayload(fetch_event_id=ev.id, content_type="text/xml", body=body, size_bytes=len(body))
    )
    await db_session.flush()
    return ev.id


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


async def _spans_for(session, person_id):
    """The person's built span Assignments, keyed by dimension (source_id part[1])."""
    rows = (
        (await session.execute(select(Assignment).where(Assignment.person_id == person_id)))
        .scalars()
        .all()
    )
    return {a.source_id.split(":")[1]: a for a in rows if len(a.source_id.split(":")) == 4}


async def _add_legacy(session, *, source_id, person_id, role_id, pm_id, fetch_event_id=None):
    row = Assignment(
        source="usa_wa_legislature",
        source_id=source_id,
        person_id=person_id,
        role_id=role_id,
        valid_from=date(2025, 1, 1),
        valid_to=None,
        is_active=True,
        pm_assignment_id=pm_id,
    )
    session.add(row)
    await session.flush()
    if fetch_event_id is not None:
        session.add(
            Citation(
                entity_type="assignment",
                entity_id=row.id,
                fetch_event_id=fetch_event_id,
                asserted_at=datetime.now(UTC),
            )
        )
        await session.flush()
    return row


async def _count(session, model, **where):
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalar()


async def _setup_person_and_spans(db_session, usa_wa, wsl_source):
    """Person 100 (Senate, D, LD5) with archived rosters + built party/Senate spans."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2023-24")
    fe_id = await _archive(db_session, wsl_source, "2025-26")
    await build_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )
    person = (
        await db_session.execute(select(Person).where(Person.source_id == "100"))
    ).scalar_one()
    return person, await _spans_for(db_session, person.id), fe_id


async def test_legacy_rows_collapse_and_transfer_anchor(db_session, usa_wa, wsl_source):
    person, spans, fe_id = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    pm_party, pm_senate = _ULID(), _ULID()
    party_legacy = await _add_legacy(
        db_session,
        source_id="100:party:2025-26",
        person_id=person.id,
        role_id=spans["party"].role_id,
        pm_id=pm_party,
        fetch_event_id=fe_id,
    )
    await _add_legacy(
        db_session,
        source_id="100:chamber-senate:2025-26",
        person_id=person.id,
        role_id=spans["chamber-senate"].role_id,
        pm_id=pm_senate,
        fetch_event_id=fe_id,
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert (result.legacy_found, result.anchors_transferred, result.legacy_retired) == (2, 2, 2)
    assert result.orphans_no_span == 0
    # legacy rows are gone
    assert await _count(db_session, Assignment, source_id="100:party:2025-26") == 0
    assert await _count(db_session, Assignment, source_id="100:chamber-senate:2025-26") == 0
    # the spans now carry the transferred PM anchors
    party_span = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:party:democratic:2023-24")
        )
    ).scalar_one()
    senate_span = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2023-24")
        )
    ).scalar_one()
    assert party_span.pm_assignment_id == pm_party
    assert senate_span.pm_assignment_id == pm_senate
    # exactly one live assignment per PM anchor (the descriptor's local_match invariant)
    assert await _count(db_session, Assignment, pm_assignment_id=pm_party) == 1
    assert await _count(db_session, Assignment, pm_assignment_id=pm_senate) == 1
    # the retired legacy row's citations are cleaned (no dangling entity_id)
    assert await _count(db_session, Citation, entity_id=party_legacy.id) == 0


async def test_chamber_house_and_committee_rows_untouched(db_session, usa_wa, wsl_source):
    """chamber-house (PDC/#69) + committee (#82) per-biennium rows are NOT sponsor spans."""
    person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    # Selector keys on source_id, not role — reuse an existing role FK for both rows.
    role_id = spans["party"].role_id
    await _add_legacy(
        db_session,
        source_id="100:chamber-house:2025-26",
        person_id=person.id,
        role_id=role_id,
        pm_id=_ULID(),
    )
    await _add_legacy(
        db_session,
        source_id="100:committee:31635:2025-26",
        person_id=person.id,
        role_id=role_id,
        pm_id=_ULID(),
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert result.legacy_found == 0  # neither dim is a sponsor-span legacy row
    assert await _count(db_session, Assignment, source_id="100:chamber-house:2025-26") == 1
    assert await _count(db_session, Assignment, source_id="100:committee:31635:2025-26") == 1


async def test_orphan_legacy_row_with_no_successor_is_left(db_session, usa_wa, wsl_source):
    """A legacy row whose (person, role) has no built span is left in place + counted."""
    _person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    # A DIFFERENT person, absent from the roster → no span is built for them.
    ghost = Person(source="usa_wa_legislature", source_id="999", name_full="Departed Member")
    db_session.add(ghost)
    await db_session.flush()
    await _add_legacy(
        db_session,
        source_id="999:party:2025-26",
        person_id=ghost.id,
        role_id=spans["party"].role_id,
        pm_id=_ULID(),
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert result.legacy_found == 1
    assert result.orphans_no_span == 1
    assert result.legacy_retired == 0
    assert await _count(db_session, Assignment, source_id="999:party:2025-26") == 1  # left alone


async def test_migration_is_idempotent(db_session, usa_wa, wsl_source):
    person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    await _add_legacy(
        db_session,
        source_id="100:party:2025-26",
        person_id=person.id,
        role_id=spans["party"].role_id,
        pm_id=_ULID(),
    )

    first = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )
    second = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert first.legacy_retired == 1
    assert second.legacy_found == 0  # nothing left to migrate on the second pass
    assert second.anchors_transferred == 0


async def test_covering_span_disambiguates_same_role_tenures(db_session, usa_wa, wsl_source):
    """A member with a dormancy gap has TWO Senate spans on the same LD seat (same role).
    A current legacy row must collapse onto the ACTIVE (covering) span, not the closed one."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    # Present 2019-20, ABSENT 2021-22 (gap), present 2023-24 + 2025-26 → two Senate spans.
    for biennium in ("2019-20", "2023-24", "2025-26"):
        await _archive(db_session, wsl_source, biennium)
    rosters = {b: [_member(100)] for b in ("2019-20", "2023-24", "2025-26")}
    client = _WireMappingSponsorClient(rosters)
    await build_sponsor_spans(db_session, sponsor_client=client, current_biennium=CURRENT)

    person = (
        await db_session.execute(select(Person).where(Person.source_id == "100"))
    ).scalar_one()
    closed = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2019-20")
        )
    ).scalar_one()
    active = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2023-24")
        )
    ).scalar_one()
    assert closed.valid_to is not None and active.valid_to is None  # sanity: two tenures
    assert closed.role_id == active.role_id  # same LD seat → same role → the collision case

    pm_id = _ULID()
    await _add_legacy(
        db_session,
        source_id="100:chamber-senate:2025-26",  # current legacy row (valid_from 2025-01-01)
        person_id=person.id,
        role_id=active.role_id,
        pm_id=pm_id,
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=client, current_biennium=CURRENT
    )

    assert result.anchors_transferred == 1
    await db_session.refresh(active)
    await db_session.refresh(closed)
    assert active.pm_assignment_id == pm_id  # the covering (active) span got the anchor
    assert closed.pm_assignment_id is None  # NOT the closed 2019-20 span


# --- CLI (_main) --------------------------------------------------------------


async def test_main_returns_2_when_database_url_unset(monkeypatch, capsys):
    """Missing DATABASE_URL → stderr message + exit 2 (config error)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(migrate_module, "configure_logging"):
        code = await migrate_module._main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back_and_returns_0(monkeypatch, capsys, test_engine):
    """--dry-run prints the summary, rolls back, and exits 0 (migrate itself is stubbed)."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    fake = MigrationResult(
        spans_built=3, legacy_found=2, anchors_transferred=2, legacy_retired=2, orphans_no_span=0
    )

    async def _fake_migrate(session, **_kwargs):
        return fake

    with (
        patch.object(migrate_module, "configure_logging"),
        patch.object(migrate_module, "migrate_sponsor_spans", _fake_migrate),
    ):
        code = await migrate_module._main(["--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "legacy_found=2 anchors_transferred=2 retired=2" in out
    assert "dry-run, rolled back" in out
