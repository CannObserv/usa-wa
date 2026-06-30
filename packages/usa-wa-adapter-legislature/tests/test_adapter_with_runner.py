"""End-to-end: WALegislatureAdapter driven by AdapterRunner."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
import vcr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from clearinghouse_core.provenance import Citation, FetchEvent, RawPayload, Source
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature import WALegislatureAdapter
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.meeting_windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.transport import WireFetch, WSLClient

CASSETTE_DIR = Path(__file__).parent / "cassettes"
CASSETTE = "committee_service_get_active_committees_2025-26.yaml"


class _FakeMeetingClient:
    """Stand-in for ``WSLClient("CommitteeMeetingService")`` — returns a fixed docket.

    Lets the meeting-window path be driven through the real AdapterRunner without a
    cassette, so the test controls the Joint/Other/House mix precisely."""

    def __init__(self, records: list[dict], *, wire: bytes) -> None:
        self._records = records
        self._wire = wire

    async def fetch_committee_meetings(self, begin, end) -> WireFetch:  # noqa: ANN001
        return WireFetch(
            records=self._records, wire=self._wire, content_type="text/xml; charset=utf-8"
        )


@pytest.fixture
async def wsl_source(db_session, usa_wa) -> Source:
    """Insert the usa_wa_legislature Source row."""
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


async def _run_refresh(db_session, source, jurisdiction):
    anchors = await bootstrap_synthetic_anchors(
        db_session,
        biennium="2025-26",
        jurisdiction_id=jurisdiction.id,
    )
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with recorder.use_cassette(CASSETTE):
        client = WSLClient("CommitteeService")
        adapter = WALegislatureAdapter(
            anchors=anchors,
            jurisdiction_id=jurisdiction.id,
            biennium="2025-26",
            client=client,
        )
        runner = AdapterRunner(
            adapter,
            db_session,
            source=source,
            jurisdiction=jurisdiction,
            natural_key=("source", "source_id"),
        )
        summary = await runner.refresh()
    return anchors, summary


async def test_refresh_writes_provenance_chain_and_committee_rows(db_session, usa_wa, wsl_source):
    """One discover ref → one FetchEvent + one RawPayload + 34 Organization rows + 34 Citations."""
    anchors, summary = await _run_refresh(db_session, wsl_source, usa_wa)

    assert summary.discovered == 1
    assert summary.fetched == 1
    assert summary.errors == 0
    assert summary.upserted_entities == 34
    assert summary.skipped_cache_hit == 0

    fetch_events = (await db_session.execute(select(FetchEvent))).scalars().all()
    raw_payloads = (await db_session.execute(select(RawPayload))).scalars().all()
    citations = (await db_session.execute(select(Citation))).scalars().all()
    assert len(fetch_events) == 1
    assert len(raw_payloads) == 1
    assert len(citations) == 34

    committees = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "committee")))
        .scalars()
        .all()
    )
    assert len(committees) == 34
    # Every committee binds to one of the chamber Orgs from bootstrap.
    chamber_ids = {anchors.house_id, anchors.senate_id}
    assert {c.parent_organization_id for c in committees}.issubset(chamber_ids)


async def test_refresh_is_idempotent_via_cache_hit(db_session, usa_wa, wsl_source):
    """A second refresh inside cache TTL short-circuits — no new SOAP, no new rows."""
    anchors = await bootstrap_synthetic_anchors(
        db_session,
        biennium="2025-26",
        jurisdiction_id=usa_wa.id,
    )
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium="2025-26",
        client=WSLClient("CommitteeService"),
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=wsl_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )

    with recorder.use_cassette(CASSETTE):
        first = await runner.refresh()
    assert first.fetched == 1

    # A second refresh must not contact WSL — patch fetch_one to guarantee that.
    with patch.object(
        adapter,
        "fetch_one",
        side_effect=AssertionError("cache-hit should short-circuit fetch_one"),
    ):
        second = await runner.refresh()
    assert second.fetched == 0
    assert second.skipped_cache_hit == 1
    assert second.discovered == 1

    # No new rows materialized on the second pass.
    committees = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "committee")))
        .scalars()
        .all()
    )
    assert len(committees) == 34


async def test_meeting_window_archives_wire_and_upserts_joint_other(db_session, usa_wa, wsl_source):
    """A committee-meetings window archives the pristine wire (hashed) and upserts the
    Joint/Other class only — the House ref in the same docket is skipped (#39)."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium="2023-24", jurisdiction_id=usa_wa.id
    )
    wire = b"<soap:Envelope>docket</soap:Envelope>"
    records = [
        {
            "Agency": "Joint",
            "Committees": {
                "Committee": [
                    {
                        "Id": -140,
                        "Name": "Joint Transportation Committee",
                        "LongName": "Joint Joint Transportation Committee",
                        "Agency": "Joint",
                        "Acronym": "JTC",
                        "Phone": None,
                    }
                ]
            },
        },
        {
            "Agency": "House",
            "Committees": {
                "Committee": [
                    {
                        "Id": 31649,
                        "Name": "Finance",
                        "LongName": "House Finance",
                        "Agency": "House",
                        "Acronym": "FIN",
                        "Phone": None,
                    }
                ]
            },
        },
    ]
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium="2023-24",
        meeting_client=_FakeMeetingClient(records, wire=wire),
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=wsl_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )

    resource_id = meetings_resource_id(*biennium_window("2023-24"))
    upserted = await runner.fetch_and_normalize(resource_id)
    assert upserted == 1  # only the Joint body; the House ref is CommitteeService's

    # Pristine wire archived verbatim, hashed as the integrity baseline (#54).
    [raw] = (await db_session.execute(select(RawPayload))).scalars().all()
    assert raw.body == wire
    [event] = (await db_session.execute(select(FetchEvent))).scalars().all()
    assert event.resource_id == resource_id
    assert event.content_hash == hashlib.sha256(wire).digest()

    # The Joint body landed as an org_type='other' row under the legislature anchor.
    [org] = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "other")))
        .scalars()
        .all()
    )
    assert org.source_id == "-140"
    assert org.name == "Joint Joint Transportation Committee"
    assert org.parent_organization_id == anchors.legislature_id


async def test_meeting_absence_is_not_retirement(db_session, usa_wa, wsl_source):
    """A joint body absent from a window is left fully intact — meeting-absence is not
    retirement for the Joint/Other class; the normalizer only ever upserts present bodies."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium="2025-26", jurisdiction_id=usa_wa.id
    )
    # An existing, live joint org that will NOT appear in this window's docket.
    await db_session.execute(
        pg_insert(Organization).values(
            source="usa_wa_legislature",
            source_id="-140",
            jurisdiction_id=usa_wa.id,
            name="Joint Joint Transportation Committee",
            org_type="other",
            parent_organization_id=anchors.legislature_id,
        )
    )
    # The docket carries a *different* joint body (-5), not -140.
    records = [
        {
            "Agency": "Joint",
            "Committees": {
                "Committee": [
                    {
                        "Id": -5,
                        "Name": "JLARC",
                        "LongName": "Joint JLARC",
                        "Agency": "Joint",
                        "Acronym": "JLARC",
                        "Phone": None,
                    }
                ]
            },
        }
    ]
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium="2025-26",
        meeting_client=_FakeMeetingClient(records, wire=b"<docket/>"),
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=wsl_source,
        jurisdiction=usa_wa,
        natural_key=("source", "source_id"),
    )
    await runner.fetch_and_normalize(meetings_resource_id(*biennium_window("2025-26")))

    rows = {
        o.source_id: o
        for o in (
            await db_session.execute(select(Organization).where(Organization.org_type == "other"))
        )
        .scalars()
        .all()
    }
    assert set(rows) == {"-140", "-5"}  # -5 discovered; -140 retained, not retired
    survivor = rows["-140"]
    assert survivor.active is True
    assert survivor.archived_at is None
    assert survivor.deleted_at is None
