"""PDC refresh cycle (#79) — archive current cohorts + re-drive the span builder.

The refresh is span-based now: it archives the current biennium's PDC winner cohorts and
re-drives :func:`build_pdc_spans` scoped to the current biennium (House Position seat spans with
the current biennium as the open end + ``person_wa_pdc`` links). The era roster is read
archive-first from the WSL sponsor archive (pre-seeded here as the WSL refresh does in prod).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.refresh import run_refresh
from usa_wa_adapter_pdc.transport import WireFetch

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier, Role
from usa_wa_adapter_legislature.refresh import biennium_for_date

BIENNIUM = "2025-26"


class _StubSponsorClient:
    """Archive-first: the live fetch must not be hit; parse decodes the archived JSON wire."""

    async def fetch_sponsors(self, biennium):  # pragma: no cover
        raise AssertionError("live sponsor pull; era roster must be archive-first")

    async def parse_sponsors(self, wire):
        return json.loads(wire.decode())


class FakePDCClient:
    def __init__(self, house=None, senate=None):
        self._house = house or []
        self._senate = senate or {}

    async def fetch_house_winners(self, election_year):
        return WireFetch(
            records=self._house,
            wire=json.dumps(self._house).encode(),
            content_type="application/json",
        )

    async def fetch_senate_winners(self, election_year):
        rows = self._senate.get(election_year, [])
        return WireFetch(
            records=rows, wire=json.dumps(rows).encode(), content_type="application/json"
        )


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
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


async def _archive_sponsors(session, wsl_source, biennium, rows):
    body = json.dumps(rows).encode()
    ev = FetchEvent(
        source_id=wsl_source.id,
        resource_id=f"sponsors:{biennium}",
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x01" * 32,
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(RawPayload(fetch_event_id=ev.id, content_type="x", body=body, size_bytes=len(body)))
    await session.flush()


def _sponsor(mid, ld, last, agency="House"):
    return {
        "Id": mid,
        "FirstName": "X",
        "LastName": last,
        "District": str(ld),
        "Agency": agency,
        "Party": "D",
    }


async def test_refresh_materializes_open_house_span(db_session, usa_wa, wsl_source):
    await _add_ld(db_session, usa_wa, 42)
    await _add_person(db_session, 100, "Alicia Rule")
    await _archive_sponsors(db_session, wsl_source, BIENNIUM, [_sponsor(100, 42, "Rule")])
    pdc = FakePDCClient(
        house=[
            {
                "person_id": "900",
                "filer_name": "Alicia Rule",
                "position": "1",
                "legislative_district": "42",
                "party_code": "D",
            }
        ]
    )

    outcome = await run_refresh(
        db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient(), pdc_client=pdc
    )

    assert outcome.cohorts_archived == 3  # house + 2 staggered senate cohorts
    assert outcome.house_spans == 1
    role = (
        await db_session.execute(select(Role).where(Role.role_type == "state_representative"))
    ).scalar_one()
    assert role.qualifier == "Position 1"
    assign = (
        await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc"))
    ).scalar_one()
    assert assign.source_id == "100:chamber-house:ld-42-position-1:2025-26"
    assert assign.valid_to is None and assign.is_active is True  # current → open end


async def test_refresh_materializes_senate_identifier_only(db_session, usa_wa, wsl_source):
    await _add_ld(db_session, usa_wa, 1)
    await _add_person(db_session, 897, "Derek Stanford")
    await _archive_sponsors(
        db_session, wsl_source, BIENNIUM, [_sponsor(897, 1, "Stanford", agency="Senate")]
    )
    pdc = FakePDCClient(
        senate={
            2024: [
                {
                    "person_id": "800",
                    "filer_name": "Derek Stanford",
                    "legislative_district": "1",
                    "party_code": "D",
                }
            ]
        }
    )

    outcome = await run_refresh(
        db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient(), pdc_client=pdc
    )

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


async def test_refresh_defaults_to_current_biennium(db_session, usa_wa, wsl_source, monkeypatch):
    monkeypatch.delenv("USA_WA_BIENNIUM", raising=False)
    expected = biennium_for_date(datetime.now(UTC).date())
    await _archive_sponsors(db_session, wsl_source, expected, [])
    outcome = await run_refresh(
        db_session, sponsor_client=_StubSponsorClient(), pdc_client=FakePDCClient()
    )
    assert outcome.cohorts_archived == 3  # archived the current cohorts


async def test_refresh_warns_on_noncurrent_biennium(db_session, usa_wa, wsl_source, caplog):
    await _archive_sponsors(db_session, wsl_source, "2019-20", [])
    with caplog.at_level(logging.WARNING):
        await run_refresh(
            db_session,
            biennium="2019-20",
            sponsor_client=_StubSponsorClient(),
            pdc_client=FakePDCClient(),
        )
    assert "pdc_refresh_noncurrent_biennium" in [r.message for r in caplog.records]


async def test_refresh_reuses_existing_source(db_session, usa_wa, wsl_source):
    await _archive_sponsors(db_session, wsl_source, BIENNIUM, [])
    for _ in range(2):
        await run_refresh(
            db_session,
            biennium=BIENNIUM,
            sponsor_client=_StubSponsorClient(),
            pdc_client=FakePDCClient(),
        )
    sources = (
        (await db_session.execute(select(Source).where(Source.slug == "usa_wa_pdc")))
        .scalars()
        .all()
    )
    assert len(sources) == 1
    assert sources[0].kind == "rest"
