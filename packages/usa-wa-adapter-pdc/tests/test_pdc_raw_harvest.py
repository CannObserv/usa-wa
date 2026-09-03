"""PDC raw-tier harvest (#304): winner-cohort wires into the file store."""

import json
from dataclasses import dataclass

import pytest

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_pdc.harvest import biennium_resource_ids
from usa_wa_adapter_pdc.raw_harvest import SOURCE_SLUG, harvest_raw

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
