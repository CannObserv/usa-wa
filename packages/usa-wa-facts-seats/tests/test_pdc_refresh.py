"""PDC refresh cycle (#79; identifier-only since #101) — re-drive the links from the archive.

Since #201 the refresh is **rebuild-only**: the Phase-A cohort archive refresh is
`usa_wa_adapter_pdc.archive_refresh` (its own unit, ordered before this one). This module
re-drives :func:`build_pdc_spans` scoped to the current biennium — emitting the ``person_wa_pdc``
identifier links only (the House Position seat is the WSL+SOS builder's since #101). Both
cohorts are read archive-first (pre-seeded here as the archive refresh + the WSL refresh do in
prod).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core import job as job_module
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_facts_seats.pdc import refresh as refresh_module
from usa_wa_facts_seats.pdc.refresh import run_refresh

BIENNIUM = "2025-26"


class _StubSponsorClient:
    """Archive-first: the live fetch must not be hit; parse decodes the archived JSON wire."""

    async def fetch_sponsors(self, biennium):  # pragma: no cover
        raise AssertionError("live sponsor pull; era roster must be archive-first")

    async def parse_sponsors(self, wire):
        return json.loads(wire.decode())


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def pdc_source(db_session, usa_wa):
    """The PDC Source the **archive** half provisions — pre-seeded here, as the
    `usa-wa-pdc-archive-refresh.service` predecessor does in prod."""
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


async def _add_person(session, mid, name):
    session.add(Person(source="usa_wa_legislature", source_id=str(mid), name_full=name))
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


async def _archive_sponsors(session, wsl_source, biennium, rows):
    await _archive(session, wsl_source, f"sponsors:{biennium}", json.dumps(rows).encode())


async def _archive_winners(session, pdc_source, resource_id, rows):
    await _archive(session, pdc_source, resource_id, json.dumps(rows).encode())


def _sponsor(mid, ld, last, agency="House"):
    return {
        "Id": mid,
        "FirstName": "X",
        "LastName": last,
        "District": str(ld),
        "Agency": agency,
        "Party": "D",
    }


async def test_refresh_materializes_house_identifier_only(
    db_session, usa_wa, wsl_source, pdc_source
):
    """#101: the daily PDC refresh emits the House winner's person_wa_pdc link, NOT a House
    Position Assignment (that seat is the WSL+SOS builder's, driven by the SOS refresh)."""
    await _add_ld(db_session, usa_wa, 42)
    await _add_person(db_session, 100, "Alicia Rule")
    await _archive_sponsors(db_session, wsl_source, BIENNIUM, [_sponsor(100, 42, "Rule")])
    await _archive_winners(
        db_session,
        pdc_source,
        "house-winners:2024",
        [
            {
                "person_id": "900",
                "filer_name": "Alicia Rule",
                "position": "1",
                "legislative_district": "42",
                "party_code": "D",
            }
        ],
    )

    outcome = await run_refresh(db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient())

    assert outcome.identifiers == 1
    ident = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.source_id == "900:wa_pdc")
        )
    ).scalar_one()
    assert ident.person_id is not None
    # No House Position Assignment is emitted by the PDC refresh anymore.
    assert (
        await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc"))
    ).scalars().all() == []


async def test_refresh_materializes_senate_identifier_only(
    db_session, usa_wa, wsl_source, pdc_source
):
    await _add_ld(db_session, usa_wa, 1)
    await _add_person(db_session, 897, "Derek Stanford")
    await _archive_sponsors(
        db_session, wsl_source, BIENNIUM, [_sponsor(897, 1, "Stanford", agency="Senate")]
    )
    await _archive_winners(
        db_session,
        pdc_source,
        "senate-winners:2024",
        [
            {
                "person_id": "800",
                "filer_name": "Derek Stanford",
                "legislative_district": "1",
                "party_code": "D",
            }
        ],
    )

    outcome = await run_refresh(db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient())

    assert outcome.identifiers == 1
    ident = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.scheme == "wa_pdc")
        )
    ).scalar_one()
    assert ident.person_id is not None
    assert (
        await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc"))
    ).scalars().all() == []


async def test_refresh_archives_nothing(db_session, usa_wa, wsl_source, pdc_source):
    """#201: the fact rebuilds, it does not source. No PDC client is injectable and no
    ``FetchEvent`` is written — refreshing the cohort archive is
    ``usa_wa_adapter_pdc.archive_refresh``'s job, in its own unit and its own ledger row."""
    await _archive_sponsors(db_session, wsl_source, BIENNIUM, [])
    before = await db_session.scalar(select(func.count()).select_from(FetchEvent))

    await run_refresh(db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient())

    assert await db_session.scalar(select(func.count()).select_from(FetchEvent)) == before


async def test_refresh_defaults_to_current_biennium(
    db_session, usa_wa, wsl_source, monkeypatch, caplog
):
    monkeypatch.delenv("USA_WA_BIENNIUM", raising=False)
    expected = biennium_for_date(datetime.now(UTC).date())
    await _archive_sponsors(db_session, wsl_source, expected, [])

    with caplog.at_level(logging.INFO):
        await run_refresh(db_session, sponsor_client=_StubSponsorClient())

    record = next(r for r in caplog.records if r.message == "pdc_refresh_complete")
    assert record.biennium == expected


async def test_refresh_warns_on_noncurrent_biennium(db_session, usa_wa, wsl_source, caplog):
    await _archive_sponsors(db_session, wsl_source, "2019-20", [])
    with caplog.at_level(logging.WARNING):
        await run_refresh(db_session, biennium="2019-20", sponsor_client=_StubSponsorClient())
    assert "pdc_refresh_noncurrent_biennium" in [r.message for r in caplog.records]


# --- CLI ----------------------------------------------------------------------


def test_main_requires_database_url(monkeypatch, capsys):
    """The daily entrypoint aborts with exit 2 when DATABASE_URL is unset (symmetric with the
    harvest / build / migrate CLIs)."""

    def _raise(_role="app"):
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    code = refresh_module.main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err
