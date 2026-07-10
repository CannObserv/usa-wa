"""End-to-end: the member resources (sponsors / committee-members) via AdapterRunner (P1b 7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, RawPayload, Source
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_domain_legislative.identity import (
    Assignment,
    Person,
    PersonIdentifier,
    Role,
)
from usa_wa_adapter_legislature import WALegislatureAdapter
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.transport import WireFetch

BIENNIUM = "2025-26"


def _member(id_, first, last, *, agency, party, district):
    return {
        "Id": id_,
        "Name": f"{first} {last}",
        "LongName": f"{'Senator' if agency == 'Senate' else 'Representative'} {last}",
        "Agency": agency,
        "Party": party,
        "District": district,
        "FirstName": first,
        "LastName": last,
    }


class _FakeSponsorClient:
    def __init__(self, records, *, wire=b"<sponsors/>"):
        self._records = records
        self._wire = wire
        self.calls: list[str] = []

    async def fetch_sponsors(self, biennium) -> WireFetch:
        self.calls.append(biennium)
        return WireFetch(records=self._records, wire=self._wire, content_type="text/xml")


class _FakeMembersClient:
    def __init__(self, records, *, wire=b"<members/>"):
        self._records = records
        self._wire = wire
        self.calls: list[tuple[str, str]] = []

    async def fetch_historical_committee_members(self, biennium, agency, name) -> WireFetch:
        self.calls.append((biennium, agency, name))
        return WireFetch(records=self._records, wire=self._wire, content_type="text/xml")


@pytest.fixture
async def wsl_source(db_session, usa_wa) -> Source:
    row = Source(
        jurisdiction_id=usa_wa.id,
        name="WA State Legislature SOAP",
        slug="usa_wa_legislature",
        kind="soap",
        base_url="https://wslwebservices.leg.wa.gov",
        reliability=1.0,
        cache_ttl_days=1,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _add_ld(session, usa_wa, n):
    row = Jurisdiction(
        slug=f"usa-wa-ld-{n}",
        name=f"WA LD {n}",
        type_id=usa_wa.type_id,
        pm_jurisdiction_id=_ULID(),
        recorded_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def test_sponsors_resource_materializes_person_cluster(db_session, usa_wa, wsl_source):
    """sponsors:<biennium> → Person + identifier only, with provenance (#78-2c).

    Party + Senate-seat tenure are archive-derived merged spans (Phase B), NOT emitted by
    the per-biennium sponsor normalize — so this resource materializes ZERO Assignment/Role."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    client = _FakeSponsorClient(
        [_member(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium=BIENNIUM,
        sponsor_client=client,
        session=db_session,
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=wsl_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )

    n = await runner.fetch_and_normalize("sponsors:2025-26")

    assert client.calls == ["2025-26"]
    assert n == 2  # Person + identifier only (no party/seat — those are Phase B spans)
    # provenance chain
    [event] = (await db_session.execute(select(FetchEvent))).scalars().all()
    assert event.resource_id == "sponsors:2025-26"
    assert (
        await db_session.execute(select(func.count()).select_from(RawPayload))
    ).scalar_one() == 1
    assert (await db_session.execute(select(func.count()).select_from(Citation))).scalar_one() == 2
    # canonical rows persisted with a valid FK chain
    person = (
        await db_session.execute(select(Person).where(Person.source_id == "101"))
    ).scalar_one()
    assert (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.person_id == person.id)
        )
    ).scalar_one().value == "101"
    # no Assignment / Role emitted by the persons-only normalize
    assert (
        await db_session.execute(select(func.count()).select_from(Assignment))
    ).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(Role))).scalar_one() == 0


async def test_sponsors_resource_cache_hit_on_rerun(db_session, usa_wa, wsl_source):
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    await _add_ld(db_session, usa_wa, 18)
    client = _FakeSponsorClient(
        [_member(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium=BIENNIUM,
        sponsor_client=client,
        session=db_session,
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=wsl_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )

    await runner.fetch_and_normalize("sponsors:2025-26")
    second = await runner.fetch_and_normalize("sponsors:2025-26")  # within TTL

    assert second == 0  # cache hit
    assert client.calls == ["2025-26"]  # no second SOAP call
    assert (await db_session.execute(select(func.count()).select_from(Person))).scalar_one() == 1


async def test_historical_committee_roster_materializes_persons_only(
    db_session, usa_wa, wsl_source
):
    """committee-members-hist:<biennium>:<id>:<agency>:<name> → Person cluster only (#82).

    Membership tenure is an archive-derived merged span (Phase B), so this resource emits
    ZERO Assignment/Role — it exists to archive the roster wire and dedup Persons."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    client = _FakeMembersClient(
        [
            _member(301, "Kristine", "Reeves", agency="House", party="Democrat", district="30"),
            _member(302, "Timm", "Ormsby", agency="House", party="Democrat", district="3"),
        ]
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium=BIENNIUM,
        member_client=client,
        session=db_session,
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=wsl_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )

    resource_id = committee_members_hist_resource_id(BIENNIUM, "31635", "House", "Appropriations")
    n = await runner.fetch_and_normalize(resource_id)

    assert client.calls == [(BIENNIUM, "House", "Appropriations")]
    assert n == 4  # 2 Persons + 2 identifiers; no membership rows
    [event] = (await db_session.execute(select(FetchEvent))).scalars().all()
    assert event.resource_id == resource_id
    assert {p.source_id for p in (await db_session.execute(select(Person))).scalars().all()} == {
        "301",
        "302",
    }
    assert (
        await db_session.execute(select(func.count()).select_from(Assignment))
    ).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(Role))).scalar_one() == 0


async def test_member_normalize_without_session_raises(db_session, usa_wa):
    """A member resource without a session is a construction error, surfaced loudly."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium=BIENNIUM,
        sponsor_client=_FakeSponsorClient([]),
    )  # no session
    payload = await adapter.fetch_one("sponsors:2025-26")
    with pytest.raises(RuntimeError, match="requires a session"):
        await adapter.normalize(payload)
