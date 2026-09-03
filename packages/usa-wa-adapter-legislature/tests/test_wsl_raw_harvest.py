"""WSL raw-tier harvest (#304): the daily SOAP set + member fan-out into files.

The fan-out enumerates committees from the wire fetched in the same run (not
from Postgres, as the daily refresh does) — the raw tier must be buildable with
no database at all.
"""

import json
from dataclasses import dataclass

import pytest

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.meetings.windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.raw_harvest import SOURCE_SLUG, harvest_raw, job_outcome

BIENNIUM = "2025-26"

COMMITTEES = [
    {"Id": 1, "Agency": "House", "Name": "Agriculture"},
    {"Id": 2, "Agency": "Senate", "Name": "Ways & Means"},
]


@dataclass
class _Wire:
    wire: bytes
    content_type: str = "text/xml"


class FakeCommitteeClient:
    def __init__(self, *, fail_member_ids: set[int] | None = None) -> None:
        self.fail_member_ids = fail_member_ids or set()

    async def fetch_committees(self, biennium: str) -> _Wire:
        return _Wire(wire=f"committees-{biennium}".encode())

    async def parse_committees(self, wire: bytes) -> list[dict]:
        return COMMITTEES

    async def fetch_active_committees(self) -> _Wire:
        return _Wire(wire=b"active-committees")

    async def fetch_historical_committee_members(
        self, biennium: str, agency: str, committee_name: str
    ) -> _Wire:
        committee = next(c for c in COMMITTEES if c["Name"] == committee_name)
        if committee["Id"] in self.fail_member_ids:
            raise RuntimeError("soap fault")
        return _Wire(wire=f"members-{committee['Id']}".encode())


class FakeMeetingClient:
    async def fetch_committee_meetings(self, begin, end) -> _Wire:
        return _Wire(wire=b"meetings")


class FakeSponsorClient:
    async def fetch_sponsors(self, biennium: str) -> _Wire:
        return _Wire(wire=f"sponsors-{biennium}".encode())


async def test_daily_set_plus_member_fanout(tmp_path) -> None:
    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=FakeCommitteeClient(),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
    )
    store = RawStore(tmp_path, SOURCE_SLUG)
    [manifest_path] = store.manifest_paths()
    manifest = json.loads(manifest_path.read_text())
    recorded = {e["resource_id"] for e in manifest["entries"]}

    begin, end = biennium_window(BIENNIUM)
    expected = {
        f"committees-roster:{BIENNIUM}",
        f"committees:{BIENNIUM}",
        f"sponsors:{BIENNIUM}",
        meetings_resource_id(begin, end),
    } | {
        committee_members_hist_resource_id(BIENNIUM, str(c["Id"]), c["Agency"], c["Name"])
        for c in COMMITTEES
    }
    assert recorded == expected
    assert summary["errors"] == 0
    assert summary["fetched"] == len(expected)


async def test_member_fanout_failure_is_contained(tmp_path) -> None:
    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=FakeCommitteeClient(fail_member_ids={1}),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
    )
    assert summary["errors"] == 1
    store = RawStore(tmp_path, SOURCE_SLUG)
    manifest = json.loads(store.manifest_paths()[0].read_text())
    by_status = {e["resource_id"]: e["status"] for e in manifest["entries"]}
    failed = committee_members_hist_resource_id(BIENNIUM, "1", "House", "Agriculture")
    assert by_status[failed] == "err"
    ok = committee_members_hist_resource_id(BIENNIUM, "2", "Senate", "Ways & Means")
    assert by_status[ok] == "ok"


async def test_roster_fetch_failure_degrades_fanout_only(tmp_path) -> None:
    """A dead committee roster kills the fan-out but the independent pulls land."""

    class DeadRosterClient(FakeCommitteeClient):
        async def fetch_committees(self, biennium: str) -> _Wire:
            raise RuntimeError("soap down")

    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=DeadRosterClient(),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
    )
    assert summary["errors"] >= 1
    store = RawStore(tmp_path, SOURCE_SLUG)
    manifest = json.loads(store.manifest_paths()[0].read_text())
    by_status = {e["resource_id"]: e["status"] for e in manifest["entries"]}
    assert by_status[f"committees-roster:{BIENNIUM}"] == "err"
    assert by_status[f"sponsors:{BIENNIUM}"] == "ok"
    assert by_status[f"committees:{BIENNIUM}"] == "ok"


async def test_fresh_roster_still_enumerates_fanout(tmp_path) -> None:
    """A TTL-fresh roster must not suppress the member fan-out: each member wire
    makes its own TTL decision, so an errored member is retried (#302 CR)."""
    await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=FakeCommitteeClient(fail_member_ids={1}),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
    )

    class NoRosterFetchClient(FakeCommitteeClient):
        async def fetch_committees(self, biennium: str) -> _Wire:
            raise AssertionError("a fresh roster must not be re-fetched")

    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=NoRosterFetchClient(),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
        ttl_days=7,
    )
    # the errored member (id 1) is the only fetch; everything else is fresh
    assert summary["fetched"] == 1
    assert summary["errors"] == 0
    assert summary["fanout_skipped"] == 0
    store = RawStore(tmp_path, SOURCE_SLUG)
    retried = committee_members_hist_resource_id(BIENNIUM, "1", "House", "Agriculture")
    assert store.latest()[retried]["sha256"]


async def test_unparseable_roster_is_contained_and_flagged(tmp_path) -> None:
    """An HTTP-200 roster that fails to parse skips the fan-out, closes the run
    ledger, and flags the loss for the job-level degraded decision."""

    class UnparseableRosterClient(FakeCommitteeClient):
        async def parse_committees(self, wire: bytes) -> list[dict]:
            raise ValueError("malformed envelope")

    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=UnparseableRosterClient(),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
    )
    assert summary["fanout_skipped"] == 1
    assert summary["fetched"] == 4  # the daily set still landed
    store = RawStore(tmp_path, SOURCE_SLUG)
    [manifest_path] = store.manifest_paths()
    manifest = json.loads(manifest_path.read_text())
    assert {e["resource_id"] for e in manifest["entries"]} == {
        f"committees-roster:{BIENNIUM}",
        f"committees:{BIENNIUM}",
        f"sponsors:{BIENNIUM}",
        meetings_resource_id(*biennium_window(BIENNIUM)),
    }


async def test_uncontained_failure_still_closes_run(tmp_path) -> None:
    """A crash after successful fetches must not abandon them as unmanifested
    strays: ``run.close()`` runs on the error path too (#302 CR)."""
    with pytest.raises(ValueError):
        await harvest_raw(
            tmp_path,
            biennium="not-a-biennium",  # biennium_window raises after two fetches land
            committee_client=FakeCommitteeClient(),
            meeting_client=FakeMeetingClient(),
            sponsor_client=FakeSponsorClient(),
        )
    store = RawStore(tmp_path, SOURCE_SLUG)
    [manifest_path] = store.manifest_paths()
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["entries"]) == 2


def test_job_outcome_degrades_on_masked_outage_and_lost_fanout() -> None:
    """#49 alerting fires for a TTL-masked outage and for a lost fan-out."""
    base = {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 0, "fanout_skipped": 0}
    assert job_outcome({**base, "fetched": 6}).outcome == "ok"
    assert job_outcome(base).outcome == "degraded"  # landed nothing
    assert job_outcome({**base, "skipped_fresh": 3, "errors": 2}).outcome == "degraded"
    assert job_outcome({**base, "fetched": 4, "fanout_skipped": 1}).outcome == "degraded"
    # one member error on a run that otherwise landed wires: still degraded-free? No —
    # the sibling wsl-refresh fails on any committee error; errors with fetches stay ok
    assert job_outcome({**base, "fetched": 5, "errors": 1}).outcome == "ok"


async def test_benign_empty_roster_is_not_degraded(tmp_path) -> None:
    """CR 38: an empty roster wire is the archived form of the #82 benign fault
    (biennium out of coverage) — it parses to no committees, not to a lost
    fan-out. The transport's own parse raises on zero bytes, so the harvester
    must short-circuit before parsing."""

    class EmptyRosterClient(FakeCommitteeClient):
        async def fetch_committees(self, biennium: str) -> _Wire:
            return _Wire(wire=b"")

        async def parse_committees(self, wire: bytes) -> list[dict]:
            raise AssertionError("an empty wire must never reach the parser")

    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=EmptyRosterClient(),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
    )
    assert summary["fanout_skipped"] == 0
    assert summary["fetched"] == 4
    assert job_outcome(summary).outcome == "ok"


async def test_total_fetch_layer_fanout_loss_degrades(tmp_path) -> None:
    """CR 39: the base pulls landing while EVERY member fetch errors is a lost
    fan-out — the run's whole point beyond four wires — and must alert."""
    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=FakeCommitteeClient(fail_member_ids={1, 2}),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
    )
    assert summary["fanout_attempted"] == 2
    assert summary["fanout_landed"] == 0
    assert job_outcome(summary).outcome == "degraded"


async def test_partial_fanout_failure_stays_ok(tmp_path) -> None:
    summary = await harvest_raw(
        tmp_path,
        biennium=BIENNIUM,
        committee_client=FakeCommitteeClient(fail_member_ids={1}),
        meeting_client=FakeMeetingClient(),
        sponsor_client=FakeSponsorClient(),
    )
    assert summary["fanout_landed"] == 1
    assert job_outcome(summary).outcome == "ok"
