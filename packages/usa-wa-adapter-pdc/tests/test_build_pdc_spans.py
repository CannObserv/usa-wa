"""End-to-end Phase B PDC identifier build (#79; **identifier-only since #101**), fully offline.

Archives a PDC winner cohort + the seating biennium's WSL sponsor roster, then drives the
builder: cohort re-parse → era-matched winner→member match → ``person_wa_pdc`` identifier links.
The load-bearing assertion is **era matching** — a 2012 cohort resolves against the 2013-14
roster, not the current one (the #75 fix). PDC no longer emits or sweeps House Position seats
(#101 — that is the WSL+SOS builder's job); this suite asserts the demoted identifier-only shape.
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


async def _person_id(session, mid):
    return (
        await session.execute(
            select(Person.id).where(
                Person.source == "usa_wa_legislature", Person.source_id == str(mid)
            )
        )
    ).scalar_one()


async def test_era_matched_house_identifier_built_from_archive(
    db_session, usa_wa, wsl_source, pdc_source
):
    """2012 cohort → 2013-14 roster. The member (100) sat LD5 Pos1 that biennium → a
    person_wa_pdc link, NO House Position Assignment (that is the WSL+SOS builder's job)."""
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)
    await _archive(
        db_session, pdc_source, "house-winners:2012", _winners(("900", 5, 1, "M100 Smith"))
    )
    await _archive(
        db_session, wsl_source, "sponsors:2013-14", _sponsor_wire((100, 5, "Smith", "House"))
    )

    result = await build_pdc_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.identifiers == 1
    # No House Position Assignment is emitted by PDC anymore.
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0
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

    assert result.identifiers == 1
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0


async def test_absent_person_yields_no_identifier(db_session, usa_wa, wsl_source, pdc_source):
    """The WSL Person doesn't exist yet (pre-#77) → the identifier link is skipped."""
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

    assert result.identifiers == 0  # absent Person → no identifier
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0


async def test_daily_redrive_matches_staggered_senate_against_current_roster(
    db_session, usa_wa, wsl_source, pdc_source
):
    """The daily re-drive (restrict_to_biennium set) must NOT era-match the start-3 Senate cohort
    to its historical seating biennium — that would force a live GetSponsors pull. It matches the
    staggered senators against the CURRENT roster instead (they're all sitting)."""
    await _add_ld(db_session, usa_wa, 8)
    await _add_person(db_session, 200)
    await _archive(
        db_session, pdc_source, "senate-winners:2022", _winners(("800", 8, 0, "M200 Jones"))
    )
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


async def test_mid_biennium_mover_cross_links_onto_senate_person(
    db_session, usa_wa, wsl_source, pdc_source
):
    """The #74 signal survives the demotion: the 2012 House winner (Rivers) moved to the Senate
    mid-term; her PDC id cross-links onto her Senate Person (100). No House Assignment is emitted
    (PDC is identifier-only), but the mover's identifier link still lands."""
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 300)  # appointed replacement
    await _add_person(db_session, 100)  # the mover, now a Senator
    await _archive(
        db_session, pdc_source, "house-winners:2012", _winners(("900", 5, 1, "Ann Rivers"))
    )
    await _archive(
        db_session,
        wsl_source,
        "sponsors:2013-14",
        _sponsor_wire((300, 5, "Replacement", "House"), (100, 5, "Rivers", "Senate")),
    )

    result = await build_pdc_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.identifiers == 1  # the mover's cross-link
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0
    ident = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.source_id == "900:wa_pdc")
        )
    ).scalar_one()
    assert str(ident.person_id) == str(await _person_id(db_session, 100))


async def test_no_archive_emits_nothing(db_session, usa_wa, wsl_source, pdc_source):
    result = await build_pdc_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )
    assert result.identifiers == 0


# --- CLI ----------------------------------------------------------------------


async def test_main_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(build_module, "configure_logging"):
        code = await build_module._main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back(monkeypatch, capsys, test_engine):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    fake = PdcSpanResult(identifiers=5, house_years=2, senate_years=2)

    async def _fake_build(session, **_kwargs):
        return fake

    with (
        patch.object(build_module, "configure_logging"),
        patch.object(build_module, "build_pdc_spans", _fake_build),
    ):
        code = await build_module._main(["--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "identifiers=5" in out
    assert "dry-run, rolled back" in out
