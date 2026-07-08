"""Tests for the PDC refresh cycle — roster build + fill-only adapter run."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.refresh import run_refresh
from usa_wa_adapter_pdc.transport import WireFetch

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Source
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier, Role
from usa_wa_adapter_legislature.refresh import biennium_for_date


class FakeSponsorClient:
    def __init__(self, members):
        self._members = members
        self.calls = []

    async def get_sponsors(self, biennium):
        self.calls.append(biennium)
        return self._members


class FakePDCClient:
    def __init__(self, winners, senate_winners=None):
        self._winners = winners
        self._wire = json.dumps(winners).encode("utf-8")
        self._senate = senate_winners or {}

    async def fetch_house_winners(self, election_year):
        return WireFetch(records=self._winners, wire=self._wire, content_type="application/json")

    async def fetch_senate_winners(self, election_year):
        rows = self._senate.get(election_year, [])
        return WireFetch(
            records=rows, wire=json.dumps(rows).encode("utf-8"), content_type="application/json"
        )


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


async def test_run_refresh_materializes_house_seat(db_session, usa_wa):
    await _add_ld(db_session, usa_wa, 42)
    person = Person(source="usa_wa_legislature", source_id="100", name_full="Alicia Rule")
    db_session.add(person)
    await db_session.flush()

    sponsor_client = FakeSponsorClient(
        [
            {
                "Id": "100",
                "Agency": "House",
                "Party": "D",
                "District": "42",
                "FirstName": "Alicia",
                "LastName": "Rule",
            },
        ]
    )
    pdc_client = FakePDCClient(
        [
            {
                "person_id": "900",
                "filer_name": "Alicia Rule",
                "position": "1",
                "legislative_district": "42",
                "party_code": "D",
            },
        ]
    )

    summary = await run_refresh(
        db_session,
        biennium="2025-26",
        sponsor_client=sponsor_client,
        pdc_client=pdc_client,
    )

    assert sponsor_client.calls == ["2025-26"]  # roster pulled for the biennium
    assert summary.errors == 0
    role = (
        await db_session.execute(select(Role).where(Role.role_type == "state_representative"))
    ).scalar_one()
    assert role.qualifier == "Position 1"
    assign = (
        await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc"))
    ).scalar_one()
    assert assign.person_id == person.id

    # Source row created with the REST kind.
    source = (
        await db_session.execute(select(Source).where(Source.slug == "usa_wa_pdc"))
    ).scalar_one()
    assert source.kind == "rest"


async def test_run_refresh_materializes_senate_identifier(db_session, usa_wa):
    # The one GetSponsors pull builds both rosters; a Senate winner cross-links its
    # person_wa_pdc onto the sitting WSL Senator (#75) — identifier-only, no Assignment.
    await _add_ld(db_session, usa_wa, 1)
    senator = Person(source="usa_wa_legislature", source_id="897", name_full="Derek Stanford")
    db_session.add(senator)
    await db_session.flush()

    sponsor_client = FakeSponsorClient(
        [
            {
                "Id": "897",
                "Agency": "Senate",
                "Party": "D",
                "District": "1",
                "FirstName": "Derek",
                "LastName": "Stanford",
            },
        ]
    )
    pdc_client = FakePDCClient(
        [],  # no House winners
        senate_winners={
            2024: [
                {
                    "person_id": "897",
                    "filer_name": "Derek Stanford",
                    "legislative_district": "1",
                    "party_code": "D",
                }
            ]
        },
    )

    summary = await run_refresh(
        db_session,
        biennium="2025-26",
        sponsor_client=sponsor_client,
        pdc_client=pdc_client,
    )
    assert summary.errors == 0
    ident = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.scheme == "wa_pdc")
        )
    ).scalar_one()
    assert ident.person_id == senator.id
    assert (
        not (await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc")))
        .scalars()
        .all()
    )


async def test_run_refresh_defaults_to_current_biennium(db_session, usa_wa, monkeypatch):
    # No explicit biennium and no USA_WA_BIENNIUM override → derived from the current date.
    monkeypatch.delenv("USA_WA_BIENNIUM", raising=False)
    expected = biennium_for_date(datetime.now(UTC).date())
    sponsor_client = FakeSponsorClient([])
    await run_refresh(db_session, sponsor_client=sponsor_client, pdc_client=FakePDCClient([]))
    assert sponsor_client.calls == [expected]


async def test_run_refresh_warns_on_noncurrent_biennium(db_session, usa_wa, caplog):
    sponsor_client = FakeSponsorClient([])
    with caplog.at_level(logging.WARNING):
        await run_refresh(
            db_session,
            biennium="2019-20",  # deliberately not the current biennium
            sponsor_client=sponsor_client,
            pdc_client=FakePDCClient([]),
        )
    assert "pdc_refresh_noncurrent_biennium" in [r.message for r in caplog.records]


async def test_run_refresh_reuses_existing_source(db_session, usa_wa):
    # Second cycle finds the Source created by the first (idempotent _get_or_create_source).
    for _ in range(2):
        await run_refresh(
            db_session,
            biennium="2025-26",
            sponsor_client=FakeSponsorClient([]),
            pdc_client=FakePDCClient([]),
        )
    sources = (
        (await db_session.execute(select(Source).where(Source.slug == "usa_wa_pdc")))
        .scalars()
        .all()
    )
    assert len(sources) == 1
