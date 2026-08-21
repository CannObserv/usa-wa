"""End-to-end Phase B span build (#78 2b-ii): archived rosters → merged-span Assignments.

Drives the whole pipeline offline — archived sponsors:<biennium> → provider re-parse →
observation projection → span builder → emission — and asserts merged open Assignments with
per-biennium citations.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from clearinghouse_domain_legislative.operator_events import KIND_DEPARTED, KIND_SEATED
from clearinghouse_domain_legislative.span_emit import SpanBuildResult
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.membership.build import (
    build_committee_member_spans,
)
from usa_wa_adapter_legislature.membership.cohort import CommitteeMemberCohortProvider
from usa_wa_adapter_legislature.operators.store import (
    get_or_create_operator_source,
    record_operator_event,
)
from usa_wa_adapter_legislature.sponsors import build as build_module
from usa_wa_adapter_legislature.sponsors.build import build_spans
from usa_wa_common.jurisdiction import resolve_jurisdiction


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

    result = await build_spans(
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

    await build_spans(
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


async def test_operator_seated_synthesizes_appointee_without_roster_citation(
    db_session, usa_wa, wsl_source
):
    """A seated event for an appointee the wire hasn't listed yet → the overlay synthesizes an
    open Senate span, emitted with ONLY the operator field citation (no false roster citation,
    #107 CR finding 9)."""
    await _add_ld(db_session, usa_wa, 5)
    # The wire names member 100 only; 999 is a fresh appointee not yet in the roster.
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Sitting"))
    db_session.add(Person(source="usa_wa_legislature", source_id="999", name_full="Appointee"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2025-26", b"<r25/>")

    juris = await resolve_jurisdiction(db_session)
    op_source = await get_or_create_operator_source(db_session, juris)
    await record_operator_event(
        db_session,
        op_source,
        member_id="999",
        kind=KIND_SEATED,
        reason="appointed",
        effective_date=date(2025, 6, 3),
        evidence_url="https://example.gov/appointee",
        seat_kind="chamber-senate",
        seat_discriminator="5",
    )

    await build_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium="2025-26"
    )

    synth = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "999:chamber-senate:5:2025-26")
        )
    ).scalar_one()
    assert synth.valid_from == date(2025, 6, 3) and synth.is_active is True
    cites = (
        (await db_session.execute(select(Citation).where(Citation.entity_id == synth.id)))
        .scalars()
        .all()
    )
    # exactly one citation, field-level (valid_from) to the operator event — no roster citation
    assert len(cites) == 1
    assert cites[0].field_path == "valid_from"


async def test_phase_b_no_archive_emits_nothing(db_session, usa_wa, wsl_source):
    result = await build_spans(
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

    result = await build_spans(
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
    await build_spans(db_session, sponsor_client=client, current_biennium="2023-24")
    departed_seat = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "200:chamber-senate:9:2023-24")
        )
    ).scalar_one()
    assert departed_seat.is_active is True and departed_seat.valid_to is None

    # New biennium: 200 departed. The restricted daily re-drive must close their spans.
    await _archive(db_session, wsl_source, "2025-26", b"<b:2025-26>")
    await build_spans(
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
        result = await build_spans(
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
    result = await build_spans(
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

    result = await build_spans(
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


async def test_event_member_stale_in_a_later_biennium_is_not_exempted(
    db_session, usa_wa, wsl_source
):
    """#145 CR (O'Ban shape): the stale-exemption is biennium-scoped. A member with an operator
    event in an EARLY biennium (900, a `seated` in 2019-20) who genuinely departs later — still
    named in the 2021-22 wire but off every committee roster (an election-loss ghost) — must be
    stale-excluded in 2021-22, so their Senate span ends at the real departure boundary. A GLOBAL
    exemption (the pre-fix bug) kept the ghost and stretched the span into 2021-22 (a spurious
    later duplicate). The control member 100 (no event) stays open."""
    await _add_ld(db_session, usa_wa, 5)
    await _add_ld(db_session, usa_wa, 28)
    for mid, name in ((100, "Member 100"), (900, "Steve OBan")):
        db_session.add(Person(source="usa_wa_legislature", source_id=str(mid), name_full=name))
    await db_session.flush()

    rosters = {
        "2019-20": [_member(100), _member(900, district="28")],
        # 900 lost in 2020 — still named in the 2021-22 wire (the ghost row).
        "2021-22": [_member(100), _member(900, district="28")],
    }
    for biennium in rosters:
        await _archive(db_session, wsl_source, biennium, f"<b:{biennium}>".encode())
    # Committee archive: both seated in 2019-20; only 100 in 2021-22 (900 is committee-absent).
    await _archive_committee_roster(db_session, wsl_source, "2019-20", "888", b"<r:100,900/>")
    await _archive_committee_roster(db_session, wsl_source, "2021-22", "888", b"<r:100/>")

    juris = await resolve_jurisdiction(db_session)
    op_source = await get_or_create_operator_source(db_session, juris)
    await record_operator_event(
        db_session,
        op_source,
        member_id="900",
        kind=KIND_SEATED,
        reason="appointed",
        effective_date=date(2019, 6, 1),  # latest event biennium = 2019-20
        evidence_url="https://x",
        seat_kind="chamber-senate",
        seat_discriminator="28",
    )

    await build_spans(
        db_session,
        sponsor_client=_WireMappingSponsorClient(rosters),
        member_client=_WireMappingMemberClient(),
        current_biennium="2021-22",
        stale_min_coverage=0.5,
    )

    ghost_senate = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "900:chamber-senate:28:2019-20")
        )
    ).scalar_one()
    # Stale-excluded in 2021-22 → ends at the departure boundary, NOT stretched into 2021-22.
    assert ghost_senate.is_active is False and ghost_senate.valid_to == date(2020, 12, 31)
    # Control: 100 (no event, committee-present throughout) stays open.
    live_senate = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2019-20")
        )
    ).scalar_one()
    assert live_senate.is_active is True and live_senate.valid_to is None


async def test_missing_committee_archive_excludes_nothing(db_session, usa_wa, wsl_source):
    """Guardrail wiring: with no committee archive at all (pre-1999-00 / fresh deploy), the
    exclusion is a silent no-op — spans build exactly as before #105."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2025-26", b"<b:2025-26>")

    result = await build_spans(
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

    await build_spans(
        db_session,
        sponsor_client=_WireMappingSponsorClient({"2025-26": [_member(100)]}),
        member_cohort=provider,
        current_biennium="2025-26",
    )
    await build_committee_member_spans(
        db_session, member_cohort=provider, current_biennium="2025-26"
    )

    assert client.parse_calls == 1


# --- CLI (#179b: the shared job harness) --------------------------------------


def test_main_forwards_its_guard_flags_and_ledgers_the_result(monkeypatch, capsys):
    """The span builder's own flags survive the move onto ``run_job()``, and the
    result object it already returns becomes the #178 row's counters."""
    patch_job_runtime(monkeypatch)
    seen: dict = {}

    async def _fake_build(session, **kwargs):
        seen.update(kwargs)
        return SpanBuildResult(emitted=4, closed_stale=1, sweep_aborted=False)

    with patch.object(build_module, "build_spans", _fake_build):
        code = build_module.main(
            ["--json", "--max-close-fraction", "1.0", "--stale-min-coverage", "0.25"]
        )

    assert code == 0
    assert seen["max_close_fraction"] == 1.0
    assert seen["stale_min_coverage"] == 0.25
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["job"] == build_module.JOB_SLUG
    assert payload["counters"] == {"emitted": 4, "closed_stale": 1, "sweep_aborted": False}


def test_main_dry_run_rolls_back(monkeypatch):
    """--dry-run is now the harness's, and the harness owns the rollback."""
    recording = patch_job_runtime(monkeypatch)

    async def _fake_build(session, **_kwargs):
        return SpanBuildResult(emitted=0, closed_stale=0, sweep_aborted=False)

    with patch.object(build_module, "build_spans", _fake_build):
        assert build_module.main(["--dry-run"]) == 0

    assert (recording.committed, recording.rolled_back) == (0, 1)


def test_main_failure_exits_one(monkeypatch):
    patch_job_runtime(monkeypatch)

    async def _boom(session, **_kwargs):
        raise RuntimeError("span build blew up")

    with patch.object(build_module, "build_spans", _boom):
        assert build_module.main([]) == 1


# ---------------------------------------------------------------------------
# #228 — deepening: roster observations extend a joined member's spans pre-1991


async def test_extra_observations_deepen_a_member_span(db_session, usa_wa, wsl_source):
    """Roster-derived observations for a WSL-joined member merge into the sponsor build,
    so the tenure emits as ONE span keyed at the roster-era start (the #97 deepening
    shape) — and the pre-archive bienniums cite the roster edition via the fallback
    target, since no sponsor wire attests them."""
    from clearinghouse_domain_legislative.span_kinds import KIND_SENATE
    from clearinghouse_domain_legislative.tenure_spans import Observation

    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2023-24", b"<r23/>")
    await _archive(db_session, wsl_source, "2025-26", b"<r25/>")
    roster_event = FetchEvent(
        source_id=wsl_source.id,
        resource_id="legroster:2025-06-05",
        url="https://x/roster.pdf",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x02" * 32,
        status=FetchStatus.ok,
    )
    db_session.add(roster_event)
    await db_session.flush()

    result = await build_spans(
        db_session,
        sponsor_client=_FakeSponsorClient([_member(100)]),
        current_biennium="2025-26",
        extra_observations=[
            Observation("100", KIND_SENATE, "5", "2019-20"),
            Observation("100", KIND_SENATE, "5", "2021-22"),
        ],
        fallback_citation=(
            roster_event.id,
            roster_event.fetched_at,
            "legroster:2025-06-05",
        ),
    )

    assert result.emitted == 2  # the deepened seat span + the party span
    seat = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2019-20")
        )
    ).scalar_one()
    assert seat.valid_from == date(2019, 1, 1)
    assert seat.is_active is True  # still reaches current — deepening extends, never closes
    # citations: two sponsor wires + ONE roster row (the two pre-archive bienniums share
    # the edition's resource, so the dedup collapses them)
    assert (
        await db_session.execute(
            select(func.count()).select_from(Citation).where(Citation.entity_id == seat.id)
        )
    ).scalar() == 3


async def test_extra_observations_default_empty_changes_nothing(db_session, usa_wa, wsl_source):
    """The daily restricted path passes no extras; the parameter must be inert."""
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2025-26", b"<r25/>")
    result = await build_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium="2025-26"
    )
    assert result.emitted == 2


async def test_unrestricted_build_self_includes_the_roster_cohort(
    db_session, usa_wa, wsl_source, monkeypatch
):
    """The deepening is a standing property of the unrestricted build (#228): without it,
    any full rebuild — including migrate_spans' internal one — would re-assert the shallow
    1991-start keys and recreate the stranded rows the collapse just retired. Explicit
    extras win; the restricted daily path never derives."""
    from clearinghouse_domain_legislative.span_kinds import KIND_SENATE
    from clearinghouse_domain_legislative.tenure_spans import Observation
    from usa_wa_adapter_legislature.sponsors import build as sb

    calls = []

    async def fake_joined(session):
        calls.append("derived")
        return (
            [Observation("100", KIND_SENATE, "5", "2019-20")],
            None,
        )

    monkeypatch.setattr(sb, "joined_pre1991_observations", fake_joined)
    await _add_ld(db_session, usa_wa, 5)
    db_session.add(Person(source="usa_wa_legislature", source_id="100", name_full="Ann Rivers"))
    await db_session.flush()
    await _archive(db_session, wsl_source, "2025-26", b"<r25/>")

    # unrestricted, no explicit extras -> derives and deepens
    await build_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium="2025-26"
    )
    assert calls == ["derived"]
    deepened = (
        await db_session.execute(
            select(Assignment).where(Assignment.source_id == "100:chamber-senate:5:2019-20")
        )
    ).scalar_one_or_none()
    assert deepened is not None

    # restricted -> never derives
    await build_spans(
        db_session,
        sponsor_client=_FakeSponsorClient([_member(100)]),
        current_biennium="2025-26",
        restrict_to_biennium="2025-26",
    )
    assert calls == ["derived"]

    # explicit extras -> the caller's set wins, no derivation
    await build_spans(
        db_session,
        sponsor_client=_FakeSponsorClient([_member(100)]),
        current_biennium="2025-26",
        extra_observations=[Observation("100", KIND_SENATE, "5", "2021-22")],
    )
    assert calls == ["derived"]

    # include_roster=False -> the opt-out actually opts out (CR #90)
    await build_spans(
        db_session,
        sponsor_client=_FakeSponsorClient([_member(100)]),
        current_biennium="2025-26",
        include_roster=False,
    )
    assert calls == ["derived"]
