"""Archive-first PDC winner-cohort provider (#79).

Re-parses the archived ``house-winners:<Y>`` / ``senate-winners:<Y>`` SODA bodies offline and
resolves the per-year citation target. Like the committee provider, "latest" means the latest
event that actually stored bytes (a forced daily re-pull re-records a payload-less FetchEvent),
so the query joins ``RawPayload``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from usa_wa_adapter_pdc.pdc_cohort import PdcWinnerCohortProvider

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source


@pytest.fixture
async def pdc_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="PDC", slug="usa_wa_pdc", kind="rest")
    db_session.add(row)
    await db_session.flush()
    return row


async def _archive(
    db_session, source, resource_id, body, *, fetched_at=None, content_hash=b"\x01" * 32
):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=fetched_at or datetime.now(UTC),
        http_status=200,
        content_hash=content_hash,
        status=FetchStatus.ok,
    )
    db_session.add(ev)
    await db_session.flush()
    if body is not None:
        db_session.add(
            RawPayload(
                fetch_event_id=ev.id,
                content_type="application/json",
                body=body,
                size_bytes=len(body),
            )
        )
        await db_session.flush()
    return ev


def _rows(*ids):
    return json.dumps([{"person_id": str(i)} for i in ids]).encode()


async def test_house_cohorts_reparsed_offline_by_year(db_session, usa_wa, pdc_source):
    await _archive(db_session, pdc_source, "house-winners:2012", _rows(1, 2))
    await _archive(db_session, pdc_source, "house-winners:2024", _rows(3))
    await _archive(db_session, pdc_source, "senate-winners:2024", _rows(9))

    provider = PdcWinnerCohortProvider(session=db_session, source_id=pdc_source.id)
    house = await provider.house_cohorts()
    senate = await provider.senate_cohorts()

    assert {y: [r["person_id"] for r in rows] for y, rows in house.items()} == {
        2012: ["1", "2"],
        2024: ["3"],
    }
    assert {y: [r["person_id"] for r in rows] for y, rows in senate.items()} == {2024: ["9"]}


async def test_events_target_payload_bearing_pull(db_session, usa_wa, pdc_source):
    now = datetime.now(UTC)
    day1 = await _archive(
        db_session, pdc_source, "house-winners:2024", _rows(3), fetched_at=now - timedelta(days=1)
    )
    # a forced daily re-pull: new event, byte-identical → no RawPayload
    await _archive(db_session, pdc_source, "house-winners:2024", None, fetched_at=now)

    provider = PdcWinnerCohortProvider(session=db_session, source_id=pdc_source.id)
    events = await provider.house_events()
    cohorts = await provider.house_cohorts()

    assert events[2024][0] == day1.id  # cite the pull that stored bytes
    assert [r["person_id"] for r in cohorts[2024]] == ["3"]  # roster still resolves


async def test_empty_when_nothing_archived(db_session, usa_wa, pdc_source):
    provider = PdcWinnerCohortProvider(session=db_session, source_id=pdc_source.id)
    assert await provider.house_cohorts() == {}
    assert await provider.senate_cohorts() == {}
    assert await provider.archived_house_years() == []


async def test_archived_years_sorted(db_session, usa_wa, pdc_source):
    for y in (2024, 2016, 2020):
        await _archive(db_session, pdc_source, f"house-winners:{y}", _rows(1))
    provider = PdcWinnerCohortProvider(session=db_session, source_id=pdc_source.id)
    assert await provider.archived_house_years() == [2016, 2020, 2024]
