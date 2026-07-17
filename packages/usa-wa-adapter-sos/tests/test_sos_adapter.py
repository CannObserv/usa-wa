"""SOSAdapter tests — resource-id keying, archive-only fetch, and the normalize guard."""

from __future__ import annotations

import pytest
from usa_wa_adapter_sos.adapter import (
    SOSAdapter,
    election_year_from_resource_id,
    whofiled_resource_id,
)
from usa_wa_adapter_sos.transport import SOSClient

ELECTION_YEAR = 2016


def test_resource_id_round_trips_the_election_year() -> None:
    rid = whofiled_resource_id(ELECTION_YEAR)
    assert rid == "sos-whofiled:201611"
    assert election_year_from_resource_id(rid) == ELECTION_YEAR


def test_election_year_from_unknown_resource_id_raises() -> None:
    with pytest.raises(ValueError, match="unknown resource_id"):
        election_year_from_resource_id("house-winners:2016")


@pytest.mark.asyncio
async def test_discover_yields_a_cohort_per_configured_year() -> None:
    adapter = SOSAdapter(election_years=[2008, 2016], client=SOSClient())
    refs = [ref.resource_id async for ref in adapter.discover(None)]
    assert refs == ["sos-whofiled:200811", "sos-whofiled:201611"]


@pytest.mark.asyncio
async def test_fetch_one_archives_pristine_csv_with_stamped_url(sos_vcr) -> None:
    adapter = SOSAdapter(election_years=[ELECTION_YEAR], client=SOSClient())
    with sos_vcr.use_cassette("whofiled_2016.yaml"):
        payload = await adapter.fetch_one("sos-whofiled:201611")

    assert payload.body, "expected pristine CSV bytes archived (#54)"
    assert "csv" in payload.content_type
    assert payload.http_status == 200
    # The stamped url records the endpoint + which cohort (fragment) was pulled.
    assert payload.url.endswith("#sos-whofiled:201611")
    assert "electionDate=201611" in payload.url
    assert payload.parsed, "expected decoded rows alongside the wire"


@pytest.mark.asyncio
async def test_normalize_is_guarded_archive_only() -> None:
    adapter = SOSAdapter(election_years=[ELECTION_YEAR], client=SOSClient())
    with pytest.raises(NotImplementedError, match="archive-only"):
        await adapter.normalize(None)  # type: ignore[arg-type]
