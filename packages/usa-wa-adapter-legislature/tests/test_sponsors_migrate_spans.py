"""Migration (#78-3 + #97): collapse stranded sponsor Assignments into merged spans.

Two stranded shapes are retired onto the span that shares their ``(person_id, role_id)`` — the
anchor moves to the span so the local cache holds ONE row per PM assignment: the pre-#78
per-biennium 3-part rows (#78-3) and the superseded 4-part shallow spans a deeper backfill
strands (#97, the #91/#95 case). chamber-house (PDC) + committee rows are left untouched; a
stranded row with no covering span is left + counted.

The anchor transfer is **index-safe** (#97, mirroring #91/#95): the stranded row is deleted +
flushed before its anchor moves to the keeper, so these tests run under the **live** #86
partial unique index (no ``drop_anchor_unique_indexes``).
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


async def _add_span_row(
    session,
    *,
    source_id,
    person_id,
    role_id,
    valid_from,
    valid_to,
    is_active,
    pm_id,
):
    """A 4-part span-shaped Assignment with explicit validity — used to stage a superseded
    shallow daily-span (#97) that a deeper backfill span strands."""
    row = Assignment(
        source="usa_wa_legislature",
        source_id=source_id,
        person_id=person_id,
        role_id=role_id,
        valid_from=valid_from,
        valid_to=valid_to,
        is_active=is_active,
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


# --- Superseded 4-part shallow spans (#97) ------------------------------------
# The 2c daily path builds a span keyed on the CURRENT biennium start (already 4-part). The
# full-archive backfill then merges the same tenure into a span starting EARLIER (a new
# source_id), stranding the anchored current-start row. It is 4-part (not the 3-part legacy
# shape) so the #78-3 migration missed it — this retires it onto the covering earlier-start
# span, moving the anchor. Same case #91 fixed for PDC House, #95 for committees.


async def test_superseded_shallow_span_retired_onto_deeper_span(db_session, usa_wa, wsl_source):
    """A current-start 4-part Senate span, stranded by a deeper backfill span, is retired onto
    it — anchor transferred, index-safe (runs under the live #86 index)."""
    person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    keeper = spans["chamber-senate"]  # the built 2023-24 span (open), unanchored
    assert keeper.source_id == "100:chamber-senate:5:2023-24"
    pm_id = _ULID()
    await _add_span_row(
        db_session,
        source_id="100:chamber-senate:5:2025-26",  # daily-built current span, stale-closed
        person_id=person.id,
        role_id=keeper.role_id,
        valid_from=date(2025, 1, 1),
        valid_to=date(2025, 1, 1),
        is_active=False,
        pm_id=pm_id,
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert result.superseded_found == 1
    assert result.superseded_retired == 1
    assert result.anchors_transferred == 1
    assert result.legacy_found == 0
    assert await _count(db_session, Assignment, source_id="100:chamber-senate:5:2025-26") == 0
    await db_session.refresh(keeper)
    assert keeper.pm_assignment_id == pm_id  # anchor moved to the deeper span
    assert await _count(db_session, Assignment, pm_assignment_id=pm_id) == 1


async def test_superseded_anchor_dropped_when_keeper_already_anchored(
    db_session, usa_wa, wsl_source
):
    """If the deeper span already carries its own anchor (sidecar produced it first), the
    superseded row's anchor can't transfer — it's dropped + counted, row still retired."""
    person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    keeper = spans["party"]  # 2023-24 party span
    keeper_pm = _ULID()
    keeper.pm_assignment_id = keeper_pm  # already anchored (preserved through build's re-emit)
    await db_session.flush()
    await _add_span_row(
        db_session,
        source_id="100:party:democratic:2025-26",  # superseded shallow, a DIFFERENT anchor
        person_id=person.id,
        role_id=keeper.role_id,
        valid_from=date(2025, 1, 1),
        valid_to=date(2025, 1, 1),
        is_active=False,
        pm_id=_ULID(),
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert result.superseded_found == 1
    assert result.superseded_retired == 1
    assert result.anchors_transferred == 0
    assert result.anchors_dropped == 1
    await db_session.refresh(keeper)
    assert keeper.pm_assignment_id == keeper_pm  # unchanged
    assert await _count(db_session, Assignment, source_id="100:party:democratic:2025-26") == 0


async def test_disjoint_dormancy_4part_spans_both_kept(db_session, usa_wa, wsl_source):
    """Two 4-part Senate spans for one seat with disjoint windows (a dormancy gap) are BOTH
    real tenures — neither covers the other's start, so neither is superseded."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    # Present 2019-20, ABSENT 2021-22 (gap), present 2023-24 + 2025-26 → two disjoint Senate spans.
    for biennium in ("2019-20", "2023-24", "2025-26"):
        await _archive(db_session, wsl_source, biennium)
    bienniums = ("2019-20", "2023-24", "2025-26")
    client = _WireMappingSponsorClient({b: [_member(100)] for b in bienniums})

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=client, current_biennium=CURRENT
    )

    assert result.superseded_found == 0
    assert await _count(db_session, Assignment, source_id="100:chamber-senate:5:2019-20") == 1
    assert await _count(db_session, Assignment, source_id="100:chamber-senate:5:2023-24") == 1


async def test_legacy_and_superseded_coexist_retire_onto_surviving_keeper(
    db_session, usa_wa, wsl_source
):
    """A legacy 3-part row AND a superseded 4-part shallow span for the SAME (person, role)
    collapse together in one pass: both retire, the durable earlier-start keeper is the sole
    survivor, and exactly ONE PM anchor lands on it (the descriptor's local_match invariant) —
    the other is dropped. Verifies the combined legacy + superseded end-state; the exclusion
    *identification* itself is unit-tested order-independently in
    ``test_superseded_pairs_*`` below (a scan-order-dependent assertion can't reliably isolate
    it here)."""
    person, spans, _fe = await _setup_person_and_spans(db_session, usa_wa, wsl_source)
    keeper = spans["chamber-senate"]  # 2023-24 span (open), unanchored — the durable keeper
    pm_superseded, pm_legacy = _ULID(), _ULID()
    await _add_span_row(
        db_session,
        source_id="100:chamber-senate:5:2025-26",  # superseded 4-part shallow
        person_id=person.id,
        role_id=keeper.role_id,
        valid_from=date(2025, 1, 1),
        valid_to=date(2025, 1, 1),
        is_active=False,
        pm_id=pm_superseded,
    )
    await _add_legacy(
        db_session,
        source_id="100:chamber-senate:2025-26",  # legacy 3-part (valid_from 2025-01-01)
        person_id=person.id,
        role_id=keeper.role_id,
        pm_id=pm_legacy,
    )

    result = await migrate_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium=CURRENT
    )

    assert (result.legacy_found, result.legacy_retired) == (1, 1)
    assert (result.superseded_found, result.superseded_retired) == (1, 1)
    # both stranded rows gone; the keeper is the sole survivor
    assert await _count(db_session, Assignment, source_id="100:chamber-senate:5:2025-26") == 0
    assert await _count(db_session, Assignment, source_id="100:chamber-senate:2025-26") == 0
    await db_session.refresh(keeper)
    # exactly one anchor survives on the keeper (one transferred, one dropped) — no double-anchor.
    # Legacy is processed before the superseded pass, so its anchor is the one kept.
    assert (result.anchors_transferred, result.anchors_dropped) == (1, 1)
    assert keeper.pm_assignment_id == pm_legacy
    assert await _count(db_session, Assignment, pm_assignment_id=pm_legacy) == 1
    assert await _count(db_session, Assignment, pm_assignment_id=pm_superseded) == 0


def test_superseded_pairs_identifies_shallow_covered_by_earlier_span():
    """``_superseded_pairs`` pairs a later-start span with the earlier-start span that covers it,
    order-independently — the pure core of the #97 supersession detection."""
    deep = Assignment(source_id="100:chamber-senate:5:2013-14", valid_from=date(2013, 1, 1))
    deep.id = _ULID()
    shallow = Assignment(
        source_id="100:chamber-senate:5:2025-26",
        valid_from=date(2025, 1, 1),
        valid_to=date(2025, 1, 1),
    )
    shallow.id = _ULID()
    key = ("person", "role")
    for ordering in ([deep, shallow], [shallow, deep]):  # scan order must not matter
        pairs = migrate_module._superseded_pairs({key: list(ordering)})
        assert len(pairs) == 1
        row, keeper = pairs[0]
        assert row is shallow and keeper is deep


def test_superseded_pairs_keeps_disjoint_dormancy_tenures():
    """Two spans with disjoint windows (a dormancy gap) are both real tenures — neither covers
    the other's start, so ``_superseded_pairs`` returns nothing."""
    early = Assignment(
        source_id="100:chamber-senate:5:2013-14",
        valid_from=date(2013, 1, 1),
        valid_to=date(2014, 12, 31),  # closed before the later span starts
    )
    early.id = _ULID()
    later = Assignment(source_id="100:chamber-senate:5:2019-20", valid_from=date(2019, 1, 1))
    later.id = _ULID()
    assert migrate_module._superseded_pairs({("person", "role"): [early, later]}) == []


# --- CLI (_main) --------------------------------------------------------------


async def test_main_returns_2_when_owner_url_unset(monkeypatch, capsys):
    """Missing DATABASE_URL_OWNER → stderr message + exit 2 (config error).

    The migration runs under the owner role — retiring a legacy row deletes its citations,
    which the app role is REVOKEd (#54)."""
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    with patch.object(migrate_module, "configure_logging"):
        code = await migrate_module._main([])
    assert code == 2
    assert "DATABASE_URL_OWNER is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back_and_returns_0(monkeypatch, capsys, test_engine):
    """--dry-run prints the summary, rolls back, and exits 0 (migrate itself is stubbed)."""
    monkeypatch.setenv("DATABASE_URL_OWNER", os.environ["TEST_DATABASE_URL"])
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
    assert "legacy_found=2 legacy_retired=2" in out
    assert "superseded_found=0 superseded_retired=0" in out
    assert "dry-run, rolled back" in out


async def test_main_forwards_max_close_fraction(monkeypatch, test_engine):
    """--max-close-fraction is parsed and forwarded to migrate_sponsor_spans (→ the #83 sweep)."""
    monkeypatch.setenv("DATABASE_URL_OWNER", os.environ["TEST_DATABASE_URL"])
    seen = {}

    async def _fake_migrate(session, **kwargs):
        seen.update(kwargs)
        return MigrationResult(
            spans_built=0,
            legacy_found=0,
            anchors_transferred=0,
            legacy_retired=0,
            orphans_no_span=0,
        )

    with (
        patch.object(migrate_module, "configure_logging"),
        patch.object(migrate_module, "migrate_sponsor_spans", _fake_migrate),
    ):
        code = await migrate_module._main(["--dry-run", "--max-close-fraction", "1.0"])

    assert code == 0
    assert seen["max_close_fraction"] == 1.0
