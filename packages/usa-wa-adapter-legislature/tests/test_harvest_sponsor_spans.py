"""End-to-end Phase B span build (#78 2b-ii): archived rosters → merged-span Assignments.

Drives the whole pipeline offline — archived sponsors:<biennium> → provider re-parse →
observation projection → span builder → emission — and asserts merged open Assignments with
per-biennium citations.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person
from usa_wa_adapter_legislature.harvest_sponsor_spans import build_sponsor_spans


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

    emitted = await build_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([_member(100)]), current_biennium="2025-26"
    )

    assert emitted == 2  # party + Senate seat, each merged across both archived biennia
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


async def test_phase_b_no_archive_emits_nothing(db_session, usa_wa, wsl_source):
    emitted = await build_sponsor_spans(
        db_session, sponsor_client=_FakeSponsorClient([]), current_biennium="2025-26"
    )
    assert emitted == 0


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

    emitted = await build_sponsor_spans(
        db_session,
        sponsor_client=client,
        current_biennium="2025-26",
        restrict_to_biennium="2025-26",
    )

    # Only 100's spans (party + Senate) — 200 is absent from the 2025-26 cohort, so skipped.
    assert emitted == 2
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
