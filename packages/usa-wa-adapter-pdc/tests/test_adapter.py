"""PDCAdapter tests — dispatch shape + end-to-end through the AdapterRunner."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.adapter import (
    PDCAdapter,
    election_year_for_biennium,
    senate_election_years_for_biennium,
)
from usa_wa_adapter_pdc.normalize.house_positions import build_house_roster, build_senate_roster
from usa_wa_adapter_pdc.transport import WireFetch

from clearinghouse_core.adapter import FetchedPayload
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, RawPayload, Source
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier, Role
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors

BIENNIUM = "2025-26"


class FakePDCClient:
    """Duck-typed PDCClient returning a fixed winner cohort as a JSON WireFetch.

    ``senate_winners`` (per election year) defaults to empty; ``senate_calls`` records the
    election years the Senate fetch was asked for (#75)."""

    def __init__(
        self, winners: list[dict], senate_winners: dict[int, list[dict]] | None = None
    ) -> None:
        import json

        self._winners = winners
        self._wire = json.dumps(winners).encode("utf-8")
        self._senate = senate_winners or {}
        self._json = json
        self.calls: list[int] = []
        self.senate_calls: list[int] = []

    async def fetch_house_winners(self, election_year: int) -> WireFetch:
        self.calls.append(election_year)
        return WireFetch(records=self._winners, wire=self._wire, content_type="application/json")

    async def fetch_senate_winners(self, election_year: int) -> WireFetch:
        self.senate_calls.append(election_year)
        rows = self._senate.get(election_year, [])
        return WireFetch(
            records=rows,
            wire=self._json.dumps(rows).encode("utf-8"),
            content_type="application/json",
        )


def _winner(person_id, filer_name, *, position, ld, party_code="D"):
    return {
        "person_id": person_id,
        "filer_name": filer_name,
        "position": position,
        "legislative_district": ld,
        "party_code": party_code,
    }


def _senate_winner(person_id, filer_name, *, ld, party_code="D"):
    # Senate SODA rows carry no ballot position (single seat per LD).
    return {
        "person_id": person_id,
        "filer_name": filer_name,
        "legislative_district": ld,
        "party_code": party_code,
    }


def test_election_year_for_biennium() -> None:
    assert election_year_for_biennium("2025-26") == 2024
    assert election_year_for_biennium("2023-24") == 2022


def test_senate_election_years_for_biennium() -> None:
    # Staggered 4-yr terms → the two most-recent even years (start-1, start-3).
    assert senate_election_years_for_biennium("2025-26") == (2024, 2022)
    assert senate_election_years_for_biennium("2023-24") == (2022, 2020)


def test_adapter_class_vars() -> None:
    assert PDCAdapter.source_slug == "usa_wa_pdc"
    assert PDCAdapter.schema_name == "usa_wa_pdc"
    assert PDCAdapter.jurisdiction_slug == "usa-wa"


async def test_discover_yields_house_winners_resource(db_session, usa_wa) -> None:
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    adapter = PDCAdapter(
        anchors=anchors, biennium=BIENNIUM, house_roster={}, client=FakePDCClient([])
    )
    refs = [ref async for ref in adapter.discover(None)]
    assert [r.resource_id for r in refs] == ["house-winners:2024"]


async def test_fetch_one_archives_wire(db_session, usa_wa) -> None:
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    client = FakePDCClient([_winner("900", "Alicia Rule", position="1", ld="42")])
    adapter = PDCAdapter(anchors=anchors, biennium=BIENNIUM, house_roster={}, client=client)
    payload = await adapter.fetch_one("house-winners:2024")
    assert client.calls == [2024]
    assert payload.body == client._wire  # pristine JSON archived
    assert payload.parsed[0]["person_id"] == "900"
    # FetchEvent.url must identify the real SODA source (#54 provenance), not a module path;
    # the resource id rides as a fragment so normalize can route on it (#75).
    assert payload.url == "https://data.wa.gov/resource/3h9x-7bvm.json#house-winners:2024"


async def test_discover_yields_senate_cohorts_when_roster_present(db_session, usa_wa) -> None:
    # A Senate roster opts the adapter into Senate discovery: House + both staggered
    # Senate cohorts (start-1, start-3) for the biennium (#75).
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    senate_roster = build_senate_roster(
        [
            {
                "Id": "897",
                "Agency": "Senate",
                "Party": "D",
                "District": "1",
                "FirstName": "Derek",
                "LastName": "Stanford",
            }
        ]
    )
    adapter = PDCAdapter(
        anchors=anchors,
        biennium=BIENNIUM,
        house_roster={},
        senate_roster=senate_roster,
        client=FakePDCClient([]),
    )
    refs = [r.resource_id async for r in adapter.discover(None)]
    assert refs == ["house-winners:2024", "senate-winners:2024", "senate-winners:2022"]


async def test_discover_house_only_without_senate_roster(db_session, usa_wa) -> None:
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    adapter = PDCAdapter(
        anchors=anchors, biennium=BIENNIUM, house_roster={}, client=FakePDCClient([])
    )
    refs = [r.resource_id async for r in adapter.discover(None)]
    assert refs == ["house-winners:2024"]


async def test_fetch_one_senate_routes_and_stamps_url(db_session, usa_wa) -> None:
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    client = FakePDCClient(
        [], senate_winners={2022: [_senate_winner("897", "Derek Stanford", ld="1")]}
    )
    adapter = PDCAdapter(
        anchors=anchors, biennium=BIENNIUM, house_roster={}, client=client, session=db_session
    )
    payload = await adapter.fetch_one("senate-winners:2022")
    assert client.senate_calls == [2022]
    assert payload.url == "https://data.wa.gov/resource/3h9x-7bvm.json#senate-winners:2022"


async def test_fetch_one_unknown_resource_raises() -> None:
    adapter = PDCAdapter(anchors=None, biennium=BIENNIUM, house_roster={}, client=FakePDCClient([]))
    with pytest.raises(ValueError, match="unknown resource_id"):
        await adapter.fetch_one("bogus:2024")


async def test_normalize_requires_session() -> None:
    adapter = PDCAdapter(anchors=None, biennium=BIENNIUM, house_roster={}, client=FakePDCClient([]))
    payload = await adapter.fetch_one("house-winners:2024")
    with pytest.raises(RuntimeError, match="requires a session"):
        await adapter.normalize(payload)


async def test_normalize_unroutable_fragment_raises() -> None:
    # No silent House default: a payload whose stamped fragment matches neither chamber
    # is a routing error, symmetric with fetch_one's unknown-resource guard.
    adapter = PDCAdapter(anchors=None, biennium=BIENNIUM, house_roster={}, client=FakePDCClient([]))
    payload = FetchedPayload(
        url="https://data.wa.gov/resource/3h9x-7bvm.json#bogus:2024",
        fetched_at=datetime.now(UTC),
        content_type="application/json",
        body=b"[]",
        parsed=[],
    )
    with pytest.raises(ValueError, match="cannot route payload"):
        await adapter.normalize(payload)


@pytest.fixture
async def pdc_source(db_session, usa_wa) -> Source:
    row = Source(
        jurisdiction_id=usa_wa.id,
        name="WA Public Disclosure Commission",
        slug="usa_wa_pdc",
        kind="rest",
        base_url="https://data.wa.gov",
        reliability=1.0,
        cache_ttl_days=1,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _add_ld(session, usa_wa, n):
    row = Jurisdiction(
        slug=f"usa-wa-ld-{n}",
        name=f"LD {n}",
        type_id=usa_wa.type_id,
        pm_jurisdiction_id=_ULID(),
        recorded_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()


async def test_runner_end_to_end_materializes_seat(db_session, usa_wa, pdc_source) -> None:
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    await _add_ld(db_session, usa_wa, 42)
    person = Person(source="usa_wa_legislature", source_id="100", name_full="Alicia Rule")
    db_session.add(person)
    await db_session.flush()

    sponsors = [
        {
            "Id": "100",
            "Agency": "House",
            "Party": "D",
            "District": "42",
            "FirstName": "Alicia",
            "LastName": "Rule",
        }
    ]
    client = FakePDCClient([_winner("900", "Alicia Rule", position="1", ld="42")])
    adapter = PDCAdapter(
        anchors=anchors,
        biennium=BIENNIUM,
        house_roster=build_house_roster(sponsors),
        client=client,
        session=db_session,
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=pdc_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )

    summary = await runner.refresh()
    assert summary.fetched == 1
    assert summary.errors == 0

    assert len((await db_session.execute(select(FetchEvent))).scalars().all()) == 1
    assert len((await db_session.execute(select(RawPayload))).scalars().all()) == 1
    ident = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.scheme == "wa_pdc")
        )
    ).scalar_one()
    assert ident.value == "900"
    assert ident.person_id == person.id
    role = (
        await db_session.execute(select(Role).where(Role.role_type == "state_representative"))
    ).scalar_one()
    assert role.qualifier == "Position 1"
    assign = (
        await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc"))
    ).scalar_one()
    assert assign.role_id == role.id

    # Re-run inside the TTL is a cache hit (no second FetchEvent).
    summary2 = await runner.refresh()
    assert summary2.skipped_cache_hit == 1


async def test_runner_end_to_end_materializes_senate_identifier(
    db_session, usa_wa, pdc_source
) -> None:
    """The #75 Senate path persists through the runner: the seated Senator's WSL Person
    gains a `person_wa_pdc` identifier, and no Assignment is minted (identifier-only —
    WSL already owns the Senate seat)."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    await _add_ld(db_session, usa_wa, 1)
    senator = Person(source="usa_wa_legislature", source_id="897", name_full="Derek Stanford")
    db_session.add(senator)
    await db_session.flush()

    sponsors = [
        {
            "Id": "897",
            "Agency": "Senate",
            "Party": "D",
            "District": "1",
            "FirstName": "Derek",
            "LastName": "Stanford",
        }
    ]
    client = FakePDCClient(
        [],  # no House winners this fixture
        senate_winners={2024: [_senate_winner("897", "Derek Stanford", ld="1")]},
    )
    adapter = PDCAdapter(
        anchors=anchors,
        biennium=BIENNIUM,
        house_roster={},
        senate_roster=build_senate_roster(sponsors),
        client=client,
        session=db_session,
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=pdc_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )

    summary = await runner.refresh()
    assert summary.errors == 0
    # Both staggered Senate cohorts + the House cohort were fetched.
    assert client.senate_calls == [2024, 2022]

    ident = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.scheme == "wa_pdc")
        )
    ).scalar_one()
    assert ident.value == "897"
    assert ident.person_id == senator.id
    # Identifier-only — no PDC-sourced Assignment for the Senate.
    assert (
        not (await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc")))
        .scalars()
        .all()
    )


async def test_runner_end_to_end_mover_cross_link_and_inferred_seat(
    db_session, usa_wa, pdc_source
) -> None:
    """The #74 paths persist through the runner: the replacement's inferred seat + the
    mover's `person_wa_pdc` cross-linked onto their current (Senate) Person, with the
    inferred assignment carrying a reduced-confidence field-level citation."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    await _add_ld(db_session, usa_wa, 48)
    for member_id, name in [
        ("35655", "Osman Salahuddin"),
        ("29109", "Amy Walen"),
        ("27504", "Vandana Slatter"),
    ]:
        db_session.add(Person(source="usa_wa_legislature", source_id=member_id, name_full=name))
    await db_session.flush()

    def _sponsor(id_, first, last, agency="House"):
        return {
            "Id": id_,
            "Agency": agency,
            "Party": "D",
            "District": "48",
            "FirstName": first,
            "LastName": last,
        }

    sponsors = [
        _sponsor("35655", "Osman", "Salahuddin"),
        _sponsor("29109", "Amy", "Walen"),
        _sponsor("27504", "Vandana", "Slatter", agency="Senate"),
    ]
    client = FakePDCClient(
        [
            _winner("800", "Amy Walen", position="2", ld="48"),
            _winner("801", "Vandana Slatter", position="1", ld="48"),  # moved to Senate
        ]
    )
    adapter = PDCAdapter(
        anchors=anchors,
        biennium=BIENNIUM,
        house_roster=build_house_roster(sponsors),
        senate_roster=build_senate_roster(sponsors),
        client=client,
        session=db_session,
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=pdc_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )

    summary = await runner.refresh()
    assert summary.errors == 0

    # Mover cross-link persisted onto Slatter's (Senate) Person.
    slatter = (
        await db_session.execute(select(Person).where(Person.source_id == "27504"))
    ).scalar_one()
    mover_id = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.person_id == slatter.id)
        )
    ).scalar_one()
    assert mover_id.scheme == "wa_pdc" and mover_id.value == "801"

    # Inferred replacement seat (Salahuddin, Pos 1) persisted with a reduced-confidence cite.
    salahuddin = (
        await db_session.execute(select(Person).where(Person.source_id == "35655"))
    ).scalar_one()
    inferred = (
        await db_session.execute(select(Assignment).where(Assignment.person_id == salahuddin.id))
    ).scalar_one()
    inferred_role = (
        await db_session.execute(select(Role).where(Role.id == inferred.role_id))
    ).scalar_one()
    assert inferred_role.qualifier == "Position 1"  # the vacated position, by elimination
    cites = (
        (
            await db_session.execute(
                select(Citation).where(
                    Citation.entity_id == inferred.id, Citation.field_path.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(cites) == 1 and cites[0].confidence < 1.0
