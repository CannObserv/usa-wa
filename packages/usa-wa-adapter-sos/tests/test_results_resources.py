"""SOS results resource ids — election-year keying — and the results Source's provisioning."""

from __future__ import annotations

import pytest

from usa_wa_adapter_sos.provisioning import RESULTS_SOURCE_SLUG, get_or_create_results_source
from usa_wa_adapter_sos.results.resources import (
    election_year_from_resource_id,
    legresults_resource_id,
)


def test_resource_id_round_trips_the_election_year() -> None:
    rid = legresults_resource_id(2024)
    assert rid == "sos-legresults:20241105"
    assert election_year_from_resource_id(rid) == 2024


def test_election_year_from_unknown_resource_id_raises() -> None:
    with pytest.raises(ValueError, match="unknown resource_id"):
        election_year_from_resource_id("sos-whofiled:202411")


@pytest.mark.asyncio
async def test_get_or_create_results_source_is_idempotent(db_session, usa_wa) -> None:
    first = await get_or_create_results_source(db_session, usa_wa)
    second = await get_or_create_results_source(db_session, usa_wa)
    assert first.id == second.id
    assert first.slug == RESULTS_SOURCE_SLUG
    assert first.slug != "usa_wa_sos"  # distinct provenance root from the filings source
