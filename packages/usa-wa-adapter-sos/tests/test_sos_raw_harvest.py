"""SOS raw-tier harvest (#304): filings + results wires into the file store."""

import json
import logging
from dataclasses import dataclass

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_sos.filings.adapter import whofiled_resource_id
from usa_wa_adapter_sos.filings.transport import SOSFilingsClient
from usa_wa_adapter_sos.raw_harvest import (
    ACCEPTED_OUTAGES,
    AcceptedOutage,
    harvest_raw,
    job_outcome,
)
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


HEALTHY = {"fetched": 2, "unchanged": 0, "skipped_fresh": 0, "errors": 0}
DEAD = {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 2}


def test_job_outcome_alerts_per_source() -> None:
    """A dead source degrades even when the other is healthy or TTL masks it.

    Driven with NO acceptances so it keeps pinning the underlying rule rather
    than whatever `ACCEPTED_OUTAGES` happens to hold today.
    """
    masked = {"fetched": 0, "unchanged": 0, "skipped_fresh": 1, "errors": 1}
    none: tuple = ()
    assert job_outcome({"filings": HEALTHY, "results": HEALTHY}, accepted=none).outcome == "ok"
    assert job_outcome({"filings": DEAD, "results": HEALTHY}, accepted=none).outcome == "degraded"
    assert job_outcome({"filings": HEALTHY, "results": DEAD}, accepted=none).outcome == "degraded"
    assert job_outcome({"filings": masked, "results": HEALTHY}, accepted=none).outcome == "degraded"
    fresh = {"fetched": 0, "unchanged": 0, "skipped_fresh": 2, "errors": 0}
    assert job_outcome({"filings": fresh, "results": HEALTHY}, accepted=none).outcome == "ok"


class TestAcceptedOutages:
    """#333: a KNOWN upstream outage must not mail the operator every night.

    votewa.gov's WhoFiled export has returned HTTP 500 for every election date
    since 2026-09-03; the nightly mailed on five consecutive runs carrying no
    new information, for a source that feeds nothing. Alert fatigue is how #49
    alerting dies, so the outage is named in code — and the naming has to expire
    by itself, or the exemption outlives the outage and the source goes dark
    without anyone hearing about it.
    """

    ACCEPT_FILINGS = (
        AcceptedOutage(
            source="filings",
            reason="upstream 500",
            issue="#333",
            observed="2026-09-03",
            # Required, not defaulted: an acceptance you cannot describe the
            # cleanup for is one whose cleanup will not happen.
            follow_up=("remove this entry",),
        ),
    )

    def test_an_accepted_outage_does_not_degrade_the_run(self) -> None:
        result = job_outcome({"filings": DEAD, "results": HEALTHY}, accepted=self.ACCEPT_FILINGS)
        assert result.outcome == "ok"
        assert result.counters["accepted_outages"] == ["filings"]

    def test_the_other_source_still_degrades(self) -> None:
        """The acceptance is scoped to one source, not to the job."""
        result = job_outcome({"filings": HEALTHY, "results": DEAD}, accepted=self.ACCEPT_FILINGS)
        assert result.outcome == "degraded"
        assert result.counters["unaccepted_outages"] == ["results"]

    def test_an_accepted_source_that_recovers_makes_the_acceptance_stale(self) -> None:
        """The self-clearing half, and the reason this is safe to add at all.

        The day upstream comes back, the acceptance is a lie sitting in the
        code — so it degrades ONCE and names itself, which is what forces its
        removal. Without this the exemption is permanent and silent.
        """
        result = job_outcome({"filings": HEALTHY, "results": HEALTHY}, accepted=self.ACCEPT_FILINGS)
        assert result.outcome == "degraded"
        assert result.counters["stale_acceptances"] == ["filings"]

    def test_a_stale_acceptance_names_every_follow_up_the_recovery_unblocks(self, caplog) -> None:
        """The recovery message has to carry the WHOLE cleanup, not half of it.

        #333's own next-steps list two things a first real wire unblocks:
        removing the acceptance, and ratcheting `stg_sos_filings_key` from
        `severity: warn` back to error (its key is a contract stated before any
        wire ever landed — #330). The staleness signal fires exactly once, so if
        it names only the acceptance the second item goes with it, and the issue
        gets closed on recovery with an unverified key still unenforced.
        """
        with caplog.at_level(logging.WARNING):
            job_outcome({"filings": HEALTHY, "results": HEALTHY}, accepted=ACCEPTED_OUTAGES)
        [record] = [r for r in caplog.records if r.message == "sos_raw_harvest_acceptance_stale"]
        assert any("stg_sos_filings_key" in item for item in record.follow_up)
        assert any("ACCEPTED_OUTAGES" in item for item in record.follow_up)

    def test_a_stale_acceptance_and_a_real_outage_are_both_named(self) -> None:
        result = job_outcome({"filings": HEALTHY, "results": DEAD}, accepted=self.ACCEPT_FILINGS)
        assert result.counters["stale_acceptances"] == ["filings"]
        assert result.counters["unaccepted_outages"] == ["results"]

    def test_the_shipped_acceptance_covers_exactly_the_333_outage(self) -> None:
        """Pins what is actually shipped, so widening it is a deliberate diff."""
        assert [(a.source, a.issue) for a in ACCEPTED_OUTAGES] == [("filings", "#333")]

    def test_todays_live_shape_is_ok_and_still_visible(self) -> None:
        """Last night's exact counters: filings dead, results fine."""
        result = job_outcome({"filings": DEAD, "results": HEALTHY})
        assert result.outcome == "ok"
        assert result.counters["accepted_outages"] == ["filings"]


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
