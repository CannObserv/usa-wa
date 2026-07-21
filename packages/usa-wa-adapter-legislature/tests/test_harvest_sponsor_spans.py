"""End-to-end Phase B span build (#78 2b-ii): archived rosters → merged-span Assignments.

Drives the whole pipeline offline — archived sponsors:<biennium> → provider re-parse →
observation projection → span builder → emission — and asserts merged open Assignments with
per-biennium citations.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from clearinghouse_domain_legislative.operator_events import KIND_DEPARTED
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.committee_member_cohort import CommitteeMemberCohortProvider
from usa_wa_adapter_legislature.harvest_committee_member_spans import (
    build_committee_member_spans,
)
from usa_wa_adapter_legislature.harvest_sponsor_spans import build_sponsor_spans
from usa_wa_adapter_legislature.operator_events_store import (
    get_or_create_operator_source,
    record_operator_event,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction


class _FakeSponsorClient:
    """parse_sponsors returns a fixed roster (the archived wire is opaque to the test)."""

    def __init__(self, roster):
        self._roster = roster
        self.fetch_calls = 0

    async def parse_sponsors(self, wire):
        return self._roster

    async def fetch_sponsors(self, biennium):
        self.fetch_calls += 1
        raise AssertionError("live pull must not happen — everything is archived")


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


async def _archive(db_session, source, biennium, wire):
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
    db_session.add(
        RawPayload(fetch_event_id=ev.id, content_type="text/xml", body=wire, size_bytes=len(wire))
    )
    await db_session.flush()


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


async def test_phase_b_builds_merged_spans_from_archive(db_session, usa_wa, wsl_source):
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2023-24", b"<r23/>")
    await _archive(db_session, wsl_source, "2025-26", b"<r25/>")

    result = await build_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium="2025-26"
    )

    assert result.emitted == 2  # party + Senate seat, merged across both archived biennia
    seat = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2023-24")
        )
    ).scalar_one()
    assert seat.valid_from == date(2023, 1, 1)
    assert seat.valid_to is None and seat.is_active is True  # reaches current → open
    # cite-every-biennium → 2 citations on the merged seat assignment
    assert (
        await db_session.execute(
            select(func.count()).select_from(Citation).where(Citation.entity_id == seat.id)
        )
    ).scalar() == 2


async def test_operator_departed_closes_spans_through_builder(db_session, usa_wa, wsl_source):
    """A departed operator event (Ramos-shaped) closes the member's Senate seat AND party spans
    at the effective date through the full builder, with a field-level operator citation (#107)."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2025-26", b"<r25/>")

    juris = await resolve_jurisdiction(db_session)
    op_source = await get_or_create_operator_source(db_session, juris)
    await record_operator_event(
        db_session,
        op_source,
        member_id="100",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://example.gov/rivers",
    )

    await build_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium="2025-26"
    )

    seat = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2025-26")
        )
    ).scalar_one()
    party = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:party:democratic:2025-26")
        )
    ).scalar_one()
    assert seat.valid_to == date(2025, 4, 19) and seat.is_active is False
    assert party.valid_to == date(2025, 4, 19) and party.is_active is False
    # field-level operator citation on the closed boundary
    field_cites = (
        await db_session.execute(
            select(func.count())
            .select_from(Citation)
            .where(Citation.entity_id == seat.id, Citation.field_path == "valid_to")
        )
    ).scalar()
    assert field_cites == 1


async def test_phase_b_no_archive_emits_nothing(db_session, usa_wa, wsl_source):
    result = await build_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([]), current_biennium="2025-26"
    )
    assert result.emitted == 0


class _WireMappingSponsorClient:
    """Distinct roster per biennium — the wire encodes it (`<b:2023-24>`)."""

    def __init__(self, rosters):
        self._rosters = rosters

    async def parse_sponsors(self, wire):
        return self._rosters.get(wire.decode().removeprefix("<b:").removesuffix(">"), [])

    async def fetch_sponsors(self, biennium):
        raise AssertionError("archive-first — no live pull")


async def test_restrict_to_biennium_scopes_rebuild_to_current_cohort(
    db_session, usa_wa, wsl_source
):
    """#78-2c: the daily re-drive rebuilds only members in the current pull (their full
    history) — a member present in a PRIOR biennium but absent from the current one is skipped."""
    await _add_ld(db_session, usa_wa, 5)
    for mid in (100, 200):
        db_session.add(
            Person(source="usa_wa_legislature", source_id=str(mid), name_full=f"Member {mid}")
        )
    await db_session.flush()
    # 100 serves both biennia; 200 (departed) only appears in 2023-24.
    await _archive(db_session, wsl_source, "2023-24", b"<b:2023-24>")
    await _archive(db_session, wsl_source, "2025-26", b"<b:2025-26>")
    client = _WireMappingSponsorClient(
        {
            "2023-24": [_member(100, district="5"), _member(200, district="9")],
            "2025-26": [_member(100, district="5")],
        }
    )

    result = await build_sponsor_spans(
        db_session,
        sponsor_client=client,
        current_biennium="2025-26",
        restrict_to_biennium="2025-26",
    )

    # Only 100's spans (party + Senate) — 200 is absent from the 2025-26 cohort, so skipped.
    assert result.emitted == 2
    members_with_spans = {
        a.source_id.split(":")[0]
        for a in (await db_session.execute(select(Assignment))).scalars().all()
    }
    assert members_with_spans == {"100"}


async def test_restricted_rebuild_closes_departed_members_open_spans(
    db_session, usa_wa, wsl_source
):
    """#83: a departed member's open spans (left by an earlier build) are closed by the
    restricted re-drive — is_active=False, valid_to = end of the biennium before current —
    instead of staying open forever."""
    await _add_ld(db_session, usa_wa, 5)
    await _add_ld(db_session, usa_wa, 9)
    for mid in (100, 200):
        db_session.add(
            Person(source="usa_wa_legislature", source_id=str(mid), name_full=f"Member {mid}")
        )
    await db_session.flush()
    await _archive(db_session, wsl_source, "2023-24", b"<b:2023-24>")
    client = _WireMappingSponsorClient(
        {
            "2023-24": [_member(100, district="5"), _member(200, district="9")],
            "2025-26": [_member(100, district="5")],
        }
    )

    # Sitting-era build: both members' spans open (end == current 2023-24).
    await build_sponsor_spans(db_session, sponsor_client=client, current_biennium="2023-24")
    departed_seat = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "200:chamber-senate:9:2023-24")
        )
    ).scalar_one()
    assert departed_seat.is_active is True and departed_seat.valid_to is None

    # New biennium: 200 departed. The restricted daily re-drive must close their spans.
    await _archive(db_session, wsl_source, "2025-26", b"<b:2025-26>")
    await build_sponsor_spans(
        db_session,
        sponsor_client=client,
        current_biennium="2025-26",
        restrict_to_biennium="2025-26",
    )

    assert departed_seat.is_active is False
    assert departed_seat.valid_to == date(2024, 12, 31)
    departed_party = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "200:party:democratic:2023-24")
        )
    ).scalar_one()
    assert departed_party.is_active is False
    # the sitting member's span stays open
    sitting = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2023-24")
        )
    ).scalar_one()
    assert sitting.is_active is True and sitting.valid_to is None


async def _stale_party_rows(db_session, usa_wa, count):
    """Directly-inserted open party spans for long-departed members (no archive backing)."""
    org = Organization(
        source="usa_wa_legislature",
        source_id="test-stale-party-org",
        jurisdiction_id=usa_wa.id,
        name="Test Party",
        org_type="party",
    )
    db_session.add(org)
    await db_session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id="test-stale-party-role",
        organization_id=org.id,
        name="Member",
        role_type="member",
    )
    db_session.add(role)
    await db_session.flush()
    rows = []
    for mid in range(900, 900 + count):
        person = Person(
            source="usa_wa_legislature", source_id=str(mid), name_full=f"Departed {mid}"
        )
        db_session.add(person)
        await db_session.flush()
        row = Assignment(
            source="usa_wa_legislature",
            source_id=f"{mid}:party:democratic:2021-22",
            person_id=person.id,
            role_id=role.id,
            valid_from=date(2021, 1, 1),
            valid_to=None,
            is_active=True,
        )
        db_session.add(row)
        rows.append(row)
    await db_session.flush()
    return rows


async def test_max_close_fraction_threads_through_the_builder(
    db_session, usa_wa, wsl_source, caplog
):
    """#83 CR round 2: a legitimate mass close (e.g. a WSL committee-era re-key) needs the
    operator override — the builder forwards ``max_close_fraction`` to the sweep, and a
    default run surfaces the abort in its completion log (``sweep_aborted``)."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Member 100"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2025-26", b"<r25/>")
    client = _FakeSponsorClient([_member(100, district="5")])
    stale = await _stale_party_rows(db_session, usa_wa, 6)

    # Default fraction: 6 of 8 open rows stale → abort, surfaced in the completion log
    # AND the returned result (the CLI prints it, #83 CR round 3).
    with caplog.at_level(logging.INFO):
        result = await build_sponsor_spans(
            db_session,
            sponsor_client=client,
            current_biennium="2025-26",
            restrict_to_biennium="2025-26",
        )
    assert all(r.is_active for r in stale)  # aborted — nothing closed
    assert result.sweep_aborted is True and result.closed_stale == 0
    completes = [r for r in caplog.records if r.getMessage() == "sponsor_span_build_complete"]
    assert completes and completes[-1].sweep_aborted is True

    # Operator override: raised fraction lets the legitimate mass close through.
    result = await build_sponsor_spans(
        db_session,
        sponsor_client=client,
        current_biennium="2025-26",
        restrict_to_biennium="2025-26",
        max_close_fraction=1.0,
    )
    assert result.sweep_aborted is False and result.closed_stale == 6
    assert all(not r.is_active for r in stale)
    assert all(r.valid_to == date(2024, 12, 31) for r in stale)


class _WireMappingMemberClient:
    """Committee roster per wire: ``b"<r:100,200/>"`` names member ids (or ``<r:/>`` empty)."""

    async def parse_historical_committee_members(self, wire):
        ids = wire.decode().removeprefix("<r:").removesuffix("/>")
        return [{"Id": int(i), "FirstName": "A", "LastName": "B"} for i in ids.split(",") if i]


async def _archive_committee_roster(db_session, source, biennium, cid, wire):
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
    db_session.add(
        RawPayload(fetch_event_id=ev.id, content_type="text/xml", body=wire, size_bytes=len(wire))
    )
    await db_session.flush()


async def test_stale_named_row_party_span_ends_at_committee_departure(
    db_session, usa_wa, wsl_source
):
    """#105 (b) end-to-end: the Kilduff shape. A departed member stays fully named in later
    sponsor wires, but drops off every committee roster at the departure boundary — the
    committee-corroborated exclusion ends their party span there instead of leaving it open,
    while the sitting member's spans stay open."""
    await _add_ld(db_session, usa_wa, 5)
    for mid, name in ((100, "Member 100"), (900, "Chris Kilduff")):
        db_session.add(Person(source="usa_wa_legislature", source_id=str(mid), name_full=name))
    await db_session.flush()

    rosters = {
        "2019-20": [_member(100), _member(900, agency="House", district="28")],
        # Kilduff left Dec 2020 — still named in the 2021-22 wire (the ghost row).
        "2021-22": [_member(100), _member(900, agency="House", district="28")],
    }
    for biennium in rosters:
        await _archive(db_session, wsl_source, biennium, f"<b:{biennium}>".encode())
    # Committee archive: both on committees in 2019-20; only 100 in 2021-22.
    await _archive_committee_roster(db_session, wsl_source, "2019-20", "888", b"<r:100,900/>")
    await _archive_committee_roster(db_session, wsl_source, "2021-22", "888", b"<r:100/>")

    result = await build_sponsor_spans(
        db_session,
        sponsor_client=_WireMappingSponsorClient(rosters),
        member_client=_WireMappingMemberClient(),
        current_biennium="2021-22",
        stale_min_coverage=0.5,
    )

    assert result.emitted >= 3
    ghost_party = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "900:party:democratic:2019-20")
        )
    ).scalar_one()
    # Ends at the departure boundary (2020-12-31) — not open on the ghost row.
    assert ghost_party.is_active is False and ghost_party.valid_to == date(2020, 12, 31)
    live_party = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:party:democratic:2019-20")
        )
    ).scalar_one()
    assert live_party.is_active is True and live_party.valid_to is None


async def test_missing_committee_archive_excludes_nothing(db_session, usa_wa, wsl_source):
    """Guardrail wiring: with no committee archive at all (pre-1999-00 / fresh deploy), the
    exclusion is a silent no-op — spans build exactly as before #105."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2025-26", b"<b:2025-26>")

    result = await build_sponsor_spans(
        db_session,
        sponsor_client=_WireMappingSponsorClient({"2025-26": [_member(100)]}),
        member_client=_WireMappingMemberClient(),
        current_biennium="2025-26",
    )

    assert result.emitted == 2  # party + Senate seat — nothing excluded


async def test_builders_accept_a_shared_member_cohort(db_session, usa_wa, wsl_source):
    """#105 CR-1: a caller (the daily refresh) can pass ONE CommitteeMemberCohortProvider to
    both span builders; combined with the provider's memoized archive scan, the committee
    wires are parsed exactly once across both builds."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2025-26", b"<b:2025-26>")
    await _archive_committee_roster(db_session, wsl_source, "2025-26", "888", b"<r:100/>")

    class _CountingMemberClient(_WireMappingMemberClient):
        def __init__(self):
            self.parse_calls = 0

        async def parse_historical_committee_members(self, wire):
            self.parse_calls += 1
            return await super().parse_historical_committee_members(wire)

    client = _CountingMemberClient()
    provider = CommitteeMemberCohortProvider(client, session=db_session, source_id=wsl_source.id)

    await build_sponsor_spans(
        db_session,
        sponsor_client=_WireMappingSponsorClient({"2025-26": [_member(100)]}),
        member_cohort=provider,
        current_biennium="2025-26",
    )
    await build_committee_member_spans(
        db_session, member_cohort=provider, current_biennium="2025-26"
    )

    assert client.parse_calls == 1
