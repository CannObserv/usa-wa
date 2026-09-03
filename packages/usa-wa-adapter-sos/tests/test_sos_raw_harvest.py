"""SOS raw-tier harvest (#304): filings + results wires into the file store."""

import json
from dataclasses import dataclass

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_sos.filings.adapter import whofiled_resource_id
from usa_wa_adapter_sos.filings.transport import SOSFilingsClient
from usa_wa_adapter_sos.raw_harvest import harvest_raw, job_outcome
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


async def test_ttl_skips_fresh_resources(tmp_path) -> None:
    await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        filings_client=FakeFilingsClient(),
        results_client=FakeResultsClient(),
    )

    class MustNotFetchFilings(FakeFilingsClient):
        async def fetch_whofiled(self, election_year: int) -> _Wire:
            raise AssertionError("fresh resource must not be fetched")

    class MustNotFetchResults(FakeResultsClient):
        async def fetch_legislative_results(self, election_year: int) -> _Wire:
            raise AssertionError("fresh resource must not be fetched")

    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        filings_client=MustNotFetchFilings(),
        results_client=MustNotFetchResults(),
        ttl_days=1,
    )
    assert summary["fetched"] == 0
    assert summary["skipped_fresh"] == 2 * len(election_years_for_biennium(BIENNIUM))


async def test_refetch_is_deduped_not_restored(tmp_path) -> None:
    await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        filings_client=FakeFilingsClient(),
        results_client=FakeResultsClient(),
    )
    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        filings_client=FakeFilingsClient(),
        results_client=FakeResultsClient(),
    )
    assert summary["unchanged"] == 2 * len(election_years_for_biennium(BIENNIUM))
    assert len(RawStore(tmp_path, "usa_wa_sos").manifest_paths()) == 2


async def test_manifest_urls_are_real_endpoints(tmp_path) -> None:
    """The manifest ``url`` is the #54 provenance record: the actual export
    endpoints, not fabricated sos.wa.gov paths."""
    await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        filings_client=FakeFilingsClient(),
        results_client=FakeResultsClient(),
    )
    filings = json.loads(RawStore(tmp_path, "usa_wa_sos").manifest_paths()[0].read_text())
    for entry in filings["entries"]:
        assert entry["url"].startswith(SOSFilingsClient().export_url() + "?")
        assert "electionDate=" in entry["url"]
    results = json.loads(RawStore(tmp_path, "usa_wa_sos_results").manifest_paths()[0].read_text())
    for entry in results["entries"]:
        assert entry["url"].endswith("/export.html")


def test_job_outcome_alerts_per_source() -> None:
    """A dead source degrades even when the other is healthy or TTL masks it."""
    healthy = {"fetched": 2, "unchanged": 0, "skipped_fresh": 0, "errors": 0}
    dead = {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 2}
    masked = {"fetched": 0, "unchanged": 0, "skipped_fresh": 1, "errors": 1}
    assert job_outcome({"filings": healthy, "results": healthy}).outcome == "ok"
    assert job_outcome({"filings": dead, "results": healthy}).outcome == "degraded"
    assert job_outcome({"filings": healthy, "results": dead}).outcome == "degraded"
    assert job_outcome({"filings": masked, "results": healthy}).outcome == "degraded"
    fresh = {"fetched": 0, "unchanged": 0, "skipped_fresh": 2, "errors": 0}
    assert job_outcome({"filings": fresh, "results": healthy}).outcome == "ok"


async def test_manifest_url_honors_injected_client_bases(tmp_path) -> None:
    """CR 45: provenance records the request the fetching clients would make."""

    class MirrorFilings(FakeFilingsClient):
        def export_url(self) -> str:
            return "https://mirror.example/whofiled"

    class MirrorResults(FakeResultsClient):
        def export_index_url(self, election_date: str) -> str:
            return f"https://mirror.example/results/{election_date}/export.html"

    await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        filings_client=MirrorFilings(),
        results_client=MirrorResults(),
    )
    filings = json.loads(RawStore(tmp_path, "usa_wa_sos").manifest_paths()[0].read_text())
    assert all(e["url"].startswith("https://mirror.example/whofiled?") for e in filings["entries"])
    results = json.loads(RawStore(tmp_path, "usa_wa_sos_results").manifest_paths()[0].read_text())
    assert all(e["url"].startswith("https://mirror.example/results/") for e in results["entries"])
