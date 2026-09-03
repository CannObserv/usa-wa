"""SOS raw-tier harvest (#304): filings + results wires into the file store."""

import json
from dataclasses import dataclass

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_sos.filings.adapter import whofiled_resource_id
from usa_wa_adapter_sos.raw_harvest import harvest_raw
from usa_wa_adapter_sos.results.adapter import legresults_resource_id
from usa_wa_common.elections import election_years_for_biennium

BIENNIUM = "2025-26"


@dataclass
class _Wire:
    wire: bytes
    content_type: str = "text/csv"


class FakeFilingsClient:
    def __init__(self, *, fail_years: set[int] | None = None) -> None:
        self.fail_years = fail_years or set()

    async def fetch_whofiled(self, election_year: int) -> _Wire:
        if election_year in self.fail_years:
            raise RuntimeError("sos down")
        return _Wire(wire=f"filings-{election_year}".encode())


class FakeResultsClient:
    def __init__(self, *, fail_years: set[int] | None = None) -> None:
        self.fail_years = fail_years or set()

    async def fetch_legislative_results(self, election_year: int) -> _Wire:
        if election_year in self.fail_years:
            raise RuntimeError("sos down")
        return _Wire(wire=f"results-{election_year}".encode())


async def test_harvests_filings_and_results_per_year(tmp_path) -> None:
    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        filings_client=FakeFilingsClient(),
        results_client=FakeResultsClient(),
    )
    years = election_years_for_biennium(BIENNIUM)

    filings = json.loads(RawStore(tmp_path, "usa_wa_sos").manifest_paths()[0].read_text())
    assert {e["resource_id"] for e in filings["entries"]} == {
        whofiled_resource_id(y) for y in years
    }
    results = json.loads(RawStore(tmp_path, "usa_wa_sos_results").manifest_paths()[0].read_text())
    assert {e["resource_id"] for e in results["entries"]} == {
        legresults_resource_id(y) for y in years
    }
    assert summary["errors"] == 0
    assert summary["fetched"] == 2 * len(years)


async def test_one_source_failing_does_not_stop_the_other(tmp_path) -> None:
    years = election_years_for_biennium(BIENNIUM)
    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        filings_client=FakeFilingsClient(fail_years=set(years)),
        results_client=FakeResultsClient(),
    )
    assert summary["errors"] == len(years)
    assert summary["fetched"] == len(years)
    results = json.loads(RawStore(tmp_path, "usa_wa_sos_results").manifest_paths()[0].read_text())
    assert all(e["status"] == "ok" for e in results["entries"])
