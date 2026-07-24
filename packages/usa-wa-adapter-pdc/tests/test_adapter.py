"""PDCAdapter — archive-only source adapter (#79).

The adapter fetches + archives the winner cohorts; the seat/identity derivation is the Phase B
span builder's job (era-matched, cross-year), so ``normalize`` is unused and raises. The
end-to-end seat materialization lives in ``test_build_pdc_spans.py``.
"""

from __future__ import annotations

import json

import pytest
from usa_wa_adapter_pdc.adapter import (
    PDCAdapter,
    election_year_for_biennium,
    election_years_for_biennium,
    seating_biennium_for_election_year,
    senate_election_years_for_biennium,
)
from usa_wa_adapter_pdc.transport import WireFetch

BIENNIUM = "2025-26"


class FakePDCClient:
    def __init__(self, house=None, senate=None):
        self._house = house or []
        self._senate = senate or []
        self.house_calls = []
        self.senate_calls = []

    async def fetch_house_winners(self, year):
        self.house_calls.append(year)
        body = json.dumps(self._house).encode()
        return WireFetch(records=self._house, wire=body, content_type="application/json")

    async def fetch_senate_winners(self, year):
        self.senate_calls.append(year)
        body = json.dumps(self._senate).encode()
        return WireFetch(records=self._senate, wire=body, content_type="application/json")


def test_election_year_for_biennium() -> None:
    assert election_year_for_biennium("2025-26") == 2024
    assert election_year_for_biennium("2013-14") == 2012


def test_seating_biennium_for_election_year_is_inverse() -> None:
    assert seating_biennium_for_election_year(2024) == "2025-26"
    assert seating_biennium_for_election_year(2012) == "2013-14"
    for biennium in ("2025-26", "2013-14", "1999-00"):
        assert seating_biennium_for_election_year(election_year_for_biennium(biennium)) == biennium


def test_senate_election_years_for_biennium() -> None:
    assert senate_election_years_for_biennium("2025-26") == (2024, 2022)


def test_election_years_for_biennium_spans_the_seating_and_special_generals() -> None:
    """Every general-election year a biennium's membership can be decided by (#106): the even
    ``start-1`` that seated it, plus the odd ``start`` whose November general fills mid-biennium
    vacancies by special (Hunt, LD5 Senate, Nov 2025). November of ``start+1`` is *excluded* — it
    seats the NEXT biennium, not this one."""
    assert election_years_for_biennium("2025-26") == [2024, 2025]
    assert election_years_for_biennium("2013-14") == [2012, 2013]
    # the seating year always leads, so a consumer archiving in order writes the even cohort first
    assert election_years_for_biennium("2025-26")[0] == election_year_for_biennium("2025-26")


def test_adapter_class_vars() -> None:
    assert PDCAdapter.source_slug == "usa_wa_pdc"
    assert PDCAdapter.jurisdiction_slug == "usa-wa"


async def test_discover_yields_house_and_both_senate_cohorts() -> None:
    adapter = PDCAdapter(biennium=BIENNIUM, client=FakePDCClient())
    refs = [r.resource_id async for r in adapter.discover(None)]
    assert refs == ["house-winners:2024", "senate-winners:2024", "senate-winners:2022"]


async def test_fetch_one_house_archives_wire_and_stamps_url() -> None:
    client = FakePDCClient(house=[{"person_id": "1"}])
    adapter = PDCAdapter(biennium=BIENNIUM, client=client)
    payload = await adapter.fetch_one("house-winners:2024")

    assert client.house_calls == [2024]
    assert payload.body == b'[{"person_id": "1"}]'
    assert payload.url.endswith("#house-winners:2024")  # resource id rides as a fragment


async def test_fetch_one_senate_routes_to_senate_client() -> None:
    client = FakePDCClient(senate=[{"person_id": "9"}])
    adapter = PDCAdapter(biennium=BIENNIUM, client=client)
    payload = await adapter.fetch_one("senate-winners:2022")

    assert client.senate_calls == [2022]
    assert payload.url.endswith("#senate-winners:2022")


async def test_fetch_one_unknown_resource_raises() -> None:
    adapter = PDCAdapter(biennium=BIENNIUM, client=FakePDCClient())
    with pytest.raises(ValueError, match="unknown resource_id"):
        await adapter.fetch_one("bogus:2024")


async def test_normalize_raises_archive_only() -> None:
    """PDC is archive-only (#79): seats are built by build_pdc_spans, not normalize."""
    adapter = PDCAdapter(biennium=BIENNIUM, client=FakePDCClient(house=[{"person_id": "1"}]))
    payload = await adapter.fetch_one("house-winners:2024")
    with pytest.raises(NotImplementedError, match="archive-only"):
        await adapter.normalize(payload)
