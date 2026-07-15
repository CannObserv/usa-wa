"""End-to-end Phase B PDC span build (#79), fully offline.

Archives a PDC winner cohort + the seating biennium's WSL sponsor roster, then drives the
builder: cohort re-parse → era-matched projection → merged House Position spans + person_wa_pdc
links. The load-bearing assertion is **era matching** — a 2012 cohort resolves against the
2013-14 roster, not the current one (the #75 fix).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID
from usa_wa_adapter_pdc import build_pdc_spans as build_module
from usa_wa_adapter_pdc.build_pdc_spans import PdcSpanResult, build_pdc_spans

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier

CURRENT = "2025-26"


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def pdc_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="PDC", slug="usa_wa_pdc", kind="rest")
    db_session.add(row)
    await db_session.flush()
    return row


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
    session.add(Person(source="usa_wa_legislature", source_id=str(mid), name_full=f"M{mid}"))
    await session.flush()


async def _archive(session, source, resource_id, body):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes([hash(resource_id) & 0xFF]) * 32,
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(RawPayload(fetch_event_id=ev.id, content_type="x", body=body, size_bytes=len(body)))
    await session.flush()


def _winners(*rows):
    return json.dumps(
        [
            {
                "person_id": pid,
                "legislative_district": str(ld),
                "position": str(pos),
                "filer_name": filer,
                "party_code": "DEMOCRAT",
            }
            for pid, ld, pos, filer in rows
        ]
    ).encode()


class _StubSponsorClient:
    """The sponsor-archive provider's live fallback — must NOT be hit (archive-first)."""

    async def fetch_sponsors(self, biennium):  # pragma: no cover
        raise AssertionError(f"live sponsor pull for {biennium}; era roster must be archive-first")

    async def parse_sponsors(self, wire):
        return json.loads(wire.decode())


def _sponsor_wire(*rows):
    return json.dumps(
        [
            {
                "Id": mid,
                "FirstName": "M",
                "LastName": last,
                "District": str(ld),
                "Agency": ag,
                "Party": "D",
            }
            for mid, ld, last, ag in rows
        ]
    ).encode()


async def test_era_matched_house_span_built_from_archive(
    db_session, usa_wa, wsl_source, pdc_source
):
    """2012 cohort → 2013-14 roster. The member (100) sat LD5 Pos1 that biennium → a House
    Position span + person_wa_pdc link, all offline."""
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)
    # PDC 2012 House cohort: person 900 won LD5 Pos1.
    await _archive(
        db_session, pdc_source, "house-winners:2012", _winners(("900", 5, 1, "M100 Smith"))
    )
    # WSL 2013-14 sponsor roster: member 100 is LD5 House, surname Smith.
    await _archive(
        db_session, wsl_source, "sponsors:2013-14", _sponsor_wire((100, 5, "Smith", "House"))
    )

    result = await build_pdc_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.house_spans == 1
    assert result.identifiers == 1
    row = (
        await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc"))
    ).scalar_one()
    assert row.source_id == "100:chamber-house:ld-5-position-1:2013-14"
    assert row.valid_to is not None and row.is_active is False  # 2013-14 is closed (not current)
    assert (
        await db_session.execute(
            select(func.count())
            .select_from(PersonIdentifier)
            .where(PersonIdentifier.source_id == "900:wa_pdc")
        )
    ).scalar() == 1


async def test_senate_cohort_emits_identifier_only(db_session, usa_wa, wsl_source, pdc_source):
    await _add_ld(db_session, usa_wa, 8)
    await _add_person(db_session, 200)
    await _archive(
        db_session, pdc_source, "senate-winners:2012", _winners(("800", 8, 0, "M200 Jones"))
    )
    await _archive(
        db_session, wsl_source, "sponsors:2013-14", _sponsor_wire((200, 8, "Jones", "Senate"))
    )

    result = await build_pdc_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.house_spans == 0  # Senate is identifier-only
    assert result.identifiers == 1
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0


async def test_absent_person_yields_no_span(db_session, usa_wa, wsl_source, pdc_source):
    """The WSL Person doesn't exist yet (pre-#77) → the seat span is skipped (gated on #77)."""
    await _add_ld(db_session, usa_wa, 5)
    await _archive(
        db_session, pdc_source, "house-winners:2012", _winners(("900", 5, 1, "M100 Smith"))
    )
    await _archive(
        db_session, wsl_source, "sponsors:2013-14", _sponsor_wire((100, 5, "Smith", "House"))
    )

    result = await build_pdc_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.house_spans == 0
    assert result.identifiers == 0  # absent Person → no seat AND no identifier
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0


async def test_daily_redrive_matches_staggered_senate_against_current_roster(
    db_session, usa_wa, wsl_source, pdc_source
):
    """The daily re-drive (restrict_to_biennium set) must NOT era-match the start-3 Senate cohort
    to its historical seating biennium — that would force a live GetSponsors pull. It matches the
    staggered senators against the CURRENT roster instead (they're all sitting)."""
    await _add_ld(db_session, usa_wa, 8)
    await _add_person(db_session, 200)
    # A staggered Senate cohort: 2022 winners seat 2023-24 but sit through the current biennium.
    await _archive(
        db_session, pdc_source, "senate-winners:2022", _winners(("800", 8, 0, "M200 Jones"))
    )
    # ONLY the current sponsor roster is archived — sponsors:2023-24 is deliberately absent, so a
    # seating-biennium era-match would hit the stub's live pull and raise.
    await _archive(
        db_session, wsl_source, "sponsors:2025-26", _sponsor_wire((200, 8, "Jones", "Senate"))
    )

    result = await build_pdc_spans(
        db_session,
        sponsor_client=_StubSponsorClient(),
        current_biennium=CURRENT,
        restrict_to_biennium=CURRENT,
    )

    assert result.identifiers == 1  # matched against the current roster, no live pull


async def test_daily_redrive_scopes_identifiers_to_current_members(
    db_session, usa_wa, wsl_source, pdc_source
):
    """restrict_to_biennium keeps only current members' identifiers — a member who won a
    historical House seat but isn't in the current roster is not re-emitted daily."""
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)  # current member
    await _add_person(db_session, 999)  # historical-only member
    # A historical House cohort seating a now-departed member (999) + a current one (100).
    await _archive(
        db_session,
        pdc_source,
        "house-winners:2012",
        _winners(("900", 5, 1, "M100 Smith"), ("999", 6, 1, "M999 Gone")),
    )
    await _archive(
        db_session,
        pdc_source,
        "house-winners:2024",
        _winners(("900", 5, 1, "M100 Smith")),  # only 100 is current
    )
    await _add_ld(db_session, usa_wa, 6)
    await _archive(
        db_session,
        wsl_source,
        "sponsors:2013-14",
        _sponsor_wire((100, 5, "Smith", "House"), (999, 6, "Gone", "House")),
    )
    await _archive(
        db_session, wsl_source, "sponsors:2025-26", _sponsor_wire((100, 5, "Smith", "House"))
    )

    result = await build_pdc_spans(
        db_session,
        sponsor_client=_StubSponsorClient(),
        current_biennium=CURRENT,
        restrict_to_biennium=CURRENT,
    )

    assert result.identifiers == 1  # only member 100's link (999 is not current)
    assert (
        await db_session.execute(
            select(func.count())
            .select_from(PersonIdentifier)
            .where(PersonIdentifier.source_id == "999:wa_pdc")
        )
    ).scalar() == 0


async def test_no_archive_emits_nothing(db_session, usa_wa, wsl_source, pdc_source):
    result = await build_pdc_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )
    assert result.house_spans == 0 and result.identifiers == 0


# --- CLI ----------------------------------------------------------------------


async def test_main_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(build_module, "configure_logging"):
        code = await build_module._main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back(monkeypatch, capsys, test_engine):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    fake = PdcSpanResult(house_spans=3, identifiers=5, house_years=2, senate_years=2)

    async def _fake_build(session, **_kwargs):
        return fake

    with (
        patch.object(build_module, "configure_logging"),
        patch.object(build_module, "build_pdc_spans", _fake_build),
    ):
        code = await build_module._main(["--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "house_spans=3 identifiers=5" in out
    assert "dry-run, rolled back" in out
