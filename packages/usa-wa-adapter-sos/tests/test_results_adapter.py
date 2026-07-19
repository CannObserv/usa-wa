"""ResultsAdapter tests — resource-id keying, archive-only fetch, normalize guard, provisioning."""

from __future__ import annotations

import pytest
from usa_wa_adapter_sos.provisioning import RESULTS_SOURCE_SLUG, get_or_create_results_source
from usa_wa_adapter_sos.results.adapter import (
    ResultsAdapter,
    election_year_from_resource_id,
    legresults_resource_id,
)
from usa_wa_adapter_sos.results.transport import WireFetch


class _FakeResultsClient:
    """Stands in for the live traversal — returns a one-row Legislative CSV wire."""

    async def fetch_legislative_results(self, election_year: int) -> WireFetch:
        wire = (
            b'"Race","Candidate"\r\n'
            b'"LEGISLATIVE DISTRICT 1 - State Representative Pos. 1","Jane Doe"\r\n'
        )
        return WireFetch(
            records=[{"Race": "LD 1 Rep Pos. 1", "Candidate": "Jane Doe"}],
            wire=wire,
            content_type="application/octet-stream",
        )


def test_resource_id_round_trips_the_election_year() -> None:
    rid = legresults_resource_id(2024)
    assert rid == "sos-legresults:20241105"
    assert election_year_from_resource_id(rid) == 2024


def test_election_year_from_unknown_resource_id_raises() -> None:
    with pytest.raises(ValueError, match="unknown resource_id"):
        election_year_from_resource_id("sos-whofiled:202411")


@pytest.mark.asyncio
async def test_discover_yields_a_cohort_per_configured_year() -> None:
    adapter = ResultsAdapter(election_years=[2020, 2024], client=_FakeResultsClient())
    refs = [ref.resource_id async for ref in adapter.discover(None)]
    assert refs == ["sos-legresults:20201103", "sos-legresults:20241105"]


@pytest.mark.asyncio
async def test_fetch_one_archives_pristine_csv_with_stamped_url() -> None:
    adapter = ResultsAdapter(election_years=[2024], client=_FakeResultsClient())
    payload = await adapter.fetch_one("sos-legresults:20241105")

    assert payload.body, "expected pristine CSV bytes archived (#54)"
    assert payload.http_status == 200
    # The stamped url records the durable export.html anchor + which cohort (fragment) was pulled.
    assert payload.url.endswith("#sos-legresults:20241105")
    assert "/results/20241105/export.html" in payload.url
    assert payload.parsed, "expected decoded rows alongside the wire"


@pytest.mark.asyncio
async def test_normalize_is_guarded_archive_only() -> None:
    adapter = ResultsAdapter(election_years=[2024], client=_FakeResultsClient())
    with pytest.raises(NotImplementedError, match="archive-only"):
        await adapter.normalize(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_or_create_results_source_is_idempotent(db_session, usa_wa) -> None:
    first = await get_or_create_results_source(db_session, usa_wa)
    second = await get_or_create_results_source(db_session, usa_wa)
    assert first.id == second.id
    assert first.slug == RESULTS_SOURCE_SLUG
    assert first.slug != "usa_wa_sos"  # distinct provenance root from the filings source
