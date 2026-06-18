"""Default-tier tests for the refresh entrypoint."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import vcr
from sqlalchemy import select

from clearinghouse_core.provenance import Source
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.refresh import biennium_for_date, run_refresh

CASSETTE_DIR = Path(__file__).parent / "cassettes"
CASSETTE = "committee_service_get_active_committees_2025-26.yaml"


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2025, 1, 13), "2025-26"),
        (date(2025, 12, 31), "2025-26"),
        (date(2026, 6, 18), "2025-26"),
        (date(2026, 12, 31), "2025-26"),
        (date(2027, 1, 1), "2027-28"),
        (date(2030, 7, 4), "2029-30"),
    ],
)
def test_biennium_for_date_rolls_on_odd_years(today, expected):
    """WA bienniums start on odd years; even-year dates roll back to the start."""
    assert biennium_for_date(today) == expected


async def test_run_refresh_seeds_source_and_runs_adapter(db_session, usa_wa):
    """The entrypoint lazy-creates Source, bootstraps anchors, runs refresh."""
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with (
        recorder.use_cassette(CASSETTE),
        patch(
            "usa_wa_adapter_legislature.refresh.biennium_for_date",
            return_value="2025-26",
        ),
    ):
        summary = await run_refresh(db_session, biennium="2025-26")

    assert summary.discovered == 1
    assert summary.fetched == 1
    assert summary.upserted_entities == 34
    assert summary.errors == 0

    sources = (await db_session.execute(select(Source))).scalars().all()
    assert len(sources) == 1
    assert sources[0].slug == "usa_wa_legislature"

    committees = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "committee")))
        .scalars()
        .all()
    )
    assert len(committees) == 34


async def test_run_refresh_is_idempotent_on_source_creation(db_session, usa_wa):
    """A second call reuses the existing Source (no duplicate slug violation)."""
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with recorder.use_cassette(CASSETTE):
        await run_refresh(db_session, biennium="2025-26")

    # Second invocation hits the cache (no new SOAP call); Source row is reused.
    await run_refresh(db_session, biennium="2025-26")

    sources = (await db_session.execute(select(Source))).scalars().all()
    assert len(sources) == 1


async def test_run_refresh_raises_when_jurisdiction_missing(db_session):
    """A clean DB without the usa-wa jurisdiction row → explicit error."""
    with pytest.raises(LookupError, match="usa-wa"):
        await run_refresh(db_session, biennium="2025-26")
