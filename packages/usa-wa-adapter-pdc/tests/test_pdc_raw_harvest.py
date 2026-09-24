"""PDC raw-tier harvest (#304): winner-cohort wires into the file store."""

import json
from dataclasses import dataclass

import pytest

from clearinghouse_core.job import JobFailure
from clearinghouse_core.rawstore import RawRun, RawStore
from usa_wa_adapter_pdc.harvest import biennium_resource_ids
from usa_wa_adapter_pdc.raw_harvest import SOURCE_SLUG, harvest_raw, job_outcome
from usa_wa_adapter_pdc.transport import PDCClient
from usa_wa_common.elections import election_years_for_biennium

BIENNIUM = "2025-26"


@dataclass
class _Wire:
    wire: bytes
    content_type: str = "text/csv"


class FakePDCClient:
    def __init__(self, *, fail_years: set[int] | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail_years = fail_years or set()

    async def fetch_house_winners(self, election_year: int) -> _Wire:
        self.calls.append(("house", election_year))
        if election_year in self.fail_years:
            raise RuntimeError("socrata down")
        return _Wire(wire=f"house-{election_year}".encode())

    async def fetch_senate_winners(self, election_year: int) -> _Wire:
        self.calls.append(("senate", election_year))
        if election_year in self.fail_years:
            raise RuntimeError("socrata down")
        return _Wire(wire=f"senate-{election_year}".encode())


async def test_harvests_every_winner_cohort(tmp_path) -> None:
    client = FakePDCClient()
    summary = await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=client)

    store = RawStore(tmp_path, SOURCE_SLUG)
    [manifest_path] = store.manifest_paths()
    manifest = json.loads(manifest_path.read_text())
    recorded = {e["resource_id"] for e in manifest["entries"]}
    assert recorded == set(biennium_resource_ids(BIENNIUM))
    assert all(e["status"] == "ok" for e in manifest["entries"])
    assert summary["fetched"] == len(recorded)
    assert summary["errors"] == 0


async def test_one_cohort_failure_is_contained(tmp_path) -> None:
    years = {int(r.rsplit(":", 1)[-1]) for r in biennium_resource_ids(BIENNIUM)}
    bad_year = sorted(years)[0]
    client = FakePDCClient(fail_years={bad_year})
    summary = await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=client)
    assert summary["errors"] >= 1
    assert summary["fetched"] >= 1

    store = RawStore(tmp_path, SOURCE_SLUG)
    manifest = json.loads(store.manifest_paths()[0].read_text())
    statuses = {e["resource_id"]: e["status"] for e in manifest["entries"]}
    assert "err" in statuses.values()
    assert "ok" in statuses.values()


class _NullWireSenateClient(FakePDCClient):
    async def fetch_senate_winners(self, election_year: int) -> _Wire:
        return _Wire(wire=None)


async def test_broken_transport_contract_fails_with_the_counters_reached(tmp_path) -> None:
    """#331: ``wire=None`` raises (CR 44), and the failure carries the House cohorts
    that landed before it, so the alert says how far the run got."""
    with pytest.raises(JobFailure) as caught:
        await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=_NullWireSenateClient())
    assert isinstance(caught.value.__cause__, ValueError)
    assert caught.value.counters["fetched"] == len(election_years_for_biennium(BIENNIUM))
    manifest = json.loads(RawStore(tmp_path, SOURCE_SLUG).manifest_paths()[0].read_text())
    assert len(manifest["entries"]) == caught.value.counters["fetched"]


async def test_a_failed_manifest_write_still_reports_the_counters(tmp_path, monkeypatch) -> None:
    """CR 1: ``run.close()`` is the manifest write — the I/O step a full disk fails —
    so its failure is wrapped too, not only the fetch loop's."""

    def _disk_full(self) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(RawRun, "close", _disk_full)
    with pytest.raises(JobFailure) as caught:
        await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=FakePDCClient())
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.counters["fetched"] == len(biennium_resource_ids(BIENNIUM))


async def test_ttl_skips_fresh_resources(tmp_path) -> None:
    client = FakePDCClient()
    await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=client)
    second = FakePDCClient()
    summary = await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=second, ttl_days=1)
    assert second.calls == []
    assert summary["skipped_fresh"] == len(biennium_resource_ids(BIENNIUM))
    assert summary["fetched"] == 0


async def test_refetch_is_deduped_not_restored(tmp_path) -> None:
    await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=FakePDCClient())
    summary = await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=FakePDCClient())
    assert summary["unchanged"] == len(biennium_resource_ids(BIENNIUM))
    store = RawStore(tmp_path, SOURCE_SLUG)
    assert len(store.manifest_paths()) == 2


@pytest.mark.parametrize("prefix", ["house-winners:", "senate-winners:"])
def test_resource_ids_reuse_archive_prefixes(prefix: str) -> None:
    """The raw store keys match the Postgres archive's resource ids, so #306's
    staging models address one vocabulary across both stores."""
    assert any(r.startswith(prefix) for r in biennium_resource_ids(BIENNIUM))


async def test_manifest_url_is_replayable_soda_request(tmp_path) -> None:
    await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=FakePDCClient())
    store = RawStore(tmp_path, SOURCE_SLUG)
    manifest = json.loads(store.manifest_paths()[0].read_text())
    for entry in manifest["entries"]:
        year = entry["resource_id"].rsplit(":", 1)[-1]
        assert entry["url"].startswith(PDCClient().winners_url() + "?")
        assert f"election_year={year}" in entry["url"]


def test_job_outcome_degrades_on_ttl_masked_outage() -> None:
    """A whole-source outage must alert even when TTL skips mask it (#302 CR)."""
    base = {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 0}
    assert job_outcome({**base, "fetched": 5}).outcome == "ok"
    assert job_outcome(base).outcome == "degraded"
    assert job_outcome({**base, "skipped_fresh": 2, "errors": 3}).outcome == "degraded"
    assert job_outcome({**base, "fetched": 1, "errors": 4}).outcome == "ok"


async def test_manifest_url_honors_the_injected_clients_base(tmp_path) -> None:
    """CR 45: provenance records the request the fetching client would make."""

    class MirrorClient(FakePDCClient):
        def winners_url(self) -> str:
            return "https://mirror.example/resource/abc.json"

    await harvest_raw(tmp_path, biennium=BIENNIUM, pdc_client=MirrorClient())
    store = RawStore(tmp_path, SOURCE_SLUG)
    manifest = json.loads(store.manifest_paths()[0].read_text())
    assert all(e["url"].startswith("https://mirror.example/") for e in manifest["entries"])
