"""WSL raw-tier harvest (#304): the daily SOAP set + member fan-out into files.

The fan-out enumerates committees from the wire fetched in the same run (not
from Postgres, as the deleted daily refresh did) — the raw tier must be buildable with
no database at all.
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import date

import pytest
from zeep.exceptions import Fault

from clearinghouse_core.job import JobContext, JobFailure
from clearinghouse_core.rawstore import RawRun, RawStore
from usa_wa_adapter_legislature import raw_harvest
from usa_wa_adapter_legislature.meetings.windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.raw_harvest import (
    ACCEPTED_ROSTER_FAULTS,
    SOURCE_SLUG,
    AcceptedRosterFault,
    harvest_raw,
    job_outcome,
)
from usa_wa_adapter_legislature.resources import committee_members_hist_resource_id

BIENNIUM = "2025-26"

COMMITTEES = [
    {"Id": 1, "Agency": "House", "Name": "Agriculture"},
    {"Id": 2, "Agency": "Senate", "Name": "Ways & Means"},
]


@dataclass
class _Wire:
    wire: bytes
    content_type: str = "text/xml"


@dataclass
class _Records(_Wire):
    """A wire that also carries the derived rows, as the real ``WireFetch`` does."""

    records: list[dict] = field(default_factory=list)


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
    with pytest.raises(JobFailure) as caught:
        await harvest_raw(
            tmp_path,
            biennium="not-a-biennium",  # biennium_window raises after two fetches land
            committee_client=FakeCommitteeClient(),
            meeting_client=FakeMeetingClient(),
            sponsor_client=FakeSponsorClient(),
        )
    assert isinstance(caught.value.__cause__, ValueError)
    store = RawStore(tmp_path, SOURCE_SLUG)
    [manifest_path] = store.manifest_paths()
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["entries"]) == 2


class _NullWireSponsorClient:
    async def fetch_sponsors(self, biennium: str) -> _Wire:
        return _Wire(wire=None)


async def test_broken_transport_contract_fails_with_the_counters_reached(tmp_path) -> None:
    """#331: ``wire=None`` is a broken transport contract, so ``record_fetch`` raises
    (CR 44) — but the failure carries the counters, so the alert says how far it got."""
    with pytest.raises(JobFailure) as caught:
        await harvest_raw(
            tmp_path,
            biennium=BIENNIUM,
            committee_client=FakeCommitteeClient(),
            meeting_client=FakeMeetingClient(),
            sponsor_client=_NullWireSponsorClient(),  # the fourth fetch
        )
    assert isinstance(caught.value.__cause__, ValueError)
    assert caught.value.counters["fetched"] == 3
    assert caught.value.counters["errors"] == 0


async def test_a_failed_manifest_write_still_reports_the_counters(tmp_path, monkeypatch) -> None:
    """CR 1: ``run.close()`` is the manifest write — the I/O step a full disk fails —
    so its failure is wrapped too, not only the fetch loop's."""

    def _disk_full(self) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(RawRun, "close", _disk_full)
    with pytest.raises(JobFailure) as caught:
        await harvest_raw(
            tmp_path,
            biennium=BIENNIUM,
            committee_client=FakeCommitteeClient(),
            meeting_client=FakeMeetingClient(),
            sponsor_client=FakeSponsorClient(),
        )
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.counters["fetched"] == 4 + len(COMMITTEES)


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


class _UnpublishedRosterClient(FakeCommitteeClient):
    """WSL before it publishes a biennium: ``GetCommittees`` answers a server fault."""

    async def fetch_committees(self, biennium: str) -> _Wire:
        raise Fault("DataPortal.Fetch failed (Object reference not set to an instance)")


class _SponsorRows:
    def __init__(self, rows: int) -> None:
        self.rows = rows

    async def fetch_sponsors(self, biennium: str) -> _Records:
        return _Records(wire=b"sponsors", records=[{"Id": i} for i in range(self.rows)])


async def _rollover_night(tmp_path, *, sponsors: int, committee_client=None) -> dict:
    return await harvest_raw(
        tmp_path,
        biennium="2027-28",
        committee_client=committee_client or _UnpublishedRosterClient(),
        meeting_client=FakeMeetingClient(),
        sponsor_client=_SponsorRows(sponsors),
    )


class TestRolloverCounters:
    """#452: the run says WHICH failure it had and what ``GetSponsors`` answered, so the
    job-level decision can tell the rollover's signature from an outage."""

    async def test_the_run_names_its_biennium(self, tmp_path) -> None:
        assert (await _rollover_night(tmp_path, sponsors=0))["biennium"] == "2027-28"

    async def test_a_roster_server_fault_is_flagged(self, tmp_path) -> None:
        summary = await _rollover_night(tmp_path, sponsors=0)
        assert summary["roster_faulted"] == 1
        assert summary["errors"] == 1
        assert summary["fanout_skipped"] == 1

    async def test_a_roster_transport_error_is_not_a_fault(self, tmp_path) -> None:
        """An outage, not the rollover: the acceptance must not cover it."""

        class DeadRosterClient(FakeCommitteeClient):
            async def fetch_committees(self, biennium: str) -> _Wire:
                raise RuntimeError("connection reset")

        summary = await _rollover_night(tmp_path, sponsors=0, committee_client=DeadRosterClient())
        assert summary["roster_faulted"] == 0
        assert summary["errors"] == 1

    async def test_sponsor_rows_counts_what_get_sponsors_answered(self, tmp_path) -> None:
        assert (await _rollover_night(tmp_path, sponsors=0))["sponsor_rows"] == 0
        assert (await _rollover_night(tmp_path, sponsors=147))["sponsor_rows"] == 147

    async def test_sponsor_rows_is_unknown_when_the_payload_carries_no_rows(self, tmp_path) -> None:
        """A wire with no derived ``records`` says nothing about emptiness: unknown, never 0,
        so a transport contract change cannot satisfy the acceptance's condition."""
        summary = await harvest_raw(
            tmp_path,
            biennium="2027-28",
            committee_client=_UnpublishedRosterClient(),
            meeting_client=FakeMeetingClient(),
            sponsor_client=FakeSponsorClient(),
        )
        assert summary["sponsor_rows"] is None
        assert job_outcome(summary).outcome == "degraded"

    async def test_sponsor_rows_is_unknown_when_sponsors_did_not_land(self, tmp_path) -> None:
        class DeadSponsors:
            async def fetch_sponsors(self, biennium: str) -> _Wire:
                raise RuntimeError("connection reset")

        summary = await harvest_raw(
            tmp_path,
            biennium="2027-28",
            committee_client=_UnpublishedRosterClient(),
            meeting_client=FakeMeetingClient(),
            sponsor_client=DeadSponsors(),
        )
        assert summary["sponsor_rows"] is None


class TestAcceptedRosterFaults:
    """#452: from 2027-01-01 until WSL publishes 2027-28, ``GetCommittees(2027-28)`` raises a
    server fault and the harvest degrades every night — an email nobody can act on, which is
    how alerting stops being read (#333). Accepted while ``sponsors:<current>`` is empty, and
    stale (degraded, naming itself) the night sponsors land, which forces its removal."""

    ACCEPT = (
        AcceptedRosterFault(
            biennium="2027-28",
            reason="unpublished",
            issue="#452",
            observed="2026-10-01",
            follow_up=("remove this entry",),
        ),
    )

    async def test_the_rollover_signature_is_accepted(self, tmp_path, caplog) -> None:
        summary = await _rollover_night(tmp_path, sponsors=0)
        with caplog.at_level(logging.WARNING):
            result = job_outcome(summary, accepted=self.ACCEPT)
        assert result.outcome == "ok"
        assert result.counters["accepted_faults"] == ["2027-28"]
        [record] = [r for r in caplog.records if r.message == "wsl_raw_harvest_accepted_fault"]
        assert record.issues == ["#452"]

    async def test_without_the_acceptance_the_same_night_degrades(self, tmp_path) -> None:
        summary = await _rollover_night(tmp_path, sponsors=0)
        assert job_outcome(summary, accepted=()).outcome == "degraded"

    async def test_another_biennium_is_not_covered(self, tmp_path) -> None:
        """The biennium pin is the time box: dormant before 2027-01-01."""
        summary = {**(await _rollover_night(tmp_path, sponsors=0)), "biennium": "2025-26"}
        result = job_outcome(summary, accepted=self.ACCEPT)
        assert result.outcome == "degraded"
        assert result.counters["stale_acceptances"] == []

    async def test_a_transport_error_on_the_roster_is_not_covered(self, tmp_path) -> None:
        class DeadRosterClient(FakeCommitteeClient):
            async def fetch_committees(self, biennium: str) -> _Wire:
                raise RuntimeError("connection reset")

        summary = await _rollover_night(tmp_path, sponsors=0, committee_client=DeadRosterClient())
        assert job_outcome(summary, accepted=self.ACCEPT).outcome == "degraded"

    async def test_a_second_error_is_not_covered(self) -> None:
        """The runbook's tell for an outage: more than one error."""
        summary = {
            "biennium": "2027-28",
            "fetched": 2,
            "unchanged": 0,
            "skipped_fresh": 0,
            "errors": 2,
            "fanout_skipped": 1,
            "fanout_attempted": 0,
            "fanout_landed": 0,
            "roster_faulted": 1,
            "sponsor_rows": 0,
        }
        assert job_outcome(summary, accepted=self.ACCEPT).outcome == "degraded"

    async def test_unknown_sponsors_are_not_covered(self, tmp_path) -> None:
        summary = {**(await _rollover_night(tmp_path, sponsors=0)), "sponsor_rows": None}
        result = job_outcome(summary, accepted=self.ACCEPT)
        assert result.outcome == "degraded"
        assert result.counters["stale_acceptances"] == []

    async def test_sponsors_landing_makes_the_acceptance_stale(self, tmp_path, caplog) -> None:
        """WSL published: the acceptance is now a false claim, so it degrades and names its
        whole cleanup — even on a night the roster answers too."""
        summary = await _rollover_night(
            tmp_path, sponsors=147, committee_client=FakeCommitteeClient()
        )
        with caplog.at_level(logging.WARNING):
            result = job_outcome(summary, accepted=self.ACCEPT)
        assert result.outcome == "degraded"
        assert result.counters["stale_acceptances"] == ["2027-28"]
        [record] = [r for r in caplog.records if r.message == "wsl_raw_harvest_acceptance_stale"]
        assert record.follow_up == ["remove this entry"]

    async def test_sponsors_landing_while_the_roster_still_faults_is_not_covered(
        self, tmp_path
    ) -> None:
        summary = await _rollover_night(tmp_path, sponsors=147)
        result = job_outcome(summary, accepted=self.ACCEPT)
        assert result.outcome == "degraded"
        assert result.counters["accepted_faults"] == []

    async def test_a_published_roster_with_empty_sponsors_is_ok_and_not_stale(
        self, tmp_path
    ) -> None:
        """The condition is sponsors, as the decision states: committees first is fine."""
        summary = await _rollover_night(
            tmp_path, sponsors=0, committee_client=FakeCommitteeClient()
        )
        result = job_outcome(summary, accepted=self.ACCEPT)
        assert result.outcome == "ok"
        assert result.counters["stale_acceptances"] == []
        assert result.counters["accepted_faults"] == []

    def test_nothing_is_shipped_between_rollover_windows(self) -> None:
        """Pins what is shipped, so adding an entry is a deliberate diff. The 2027-28 entry
        was removed when WSL published 2027-28 sponsors on 2026-11-24 (#135), before it
        ever applied."""
        assert ACCEPTED_ROSTER_FAULTS == ()


class TestWsdlDrift:
    """#563: the offline parse binds to vendored WSDLs, so a live WSDL that changed shape
    must reach the operator. It degrades the harvest (contained: the chain still builds and
    publishes), then OnFailure= mails."""

    OK_RUN = {"biennium": BIENNIUM, "fetched": 6, "unchanged": 0, "skipped_fresh": 0, "errors": 0}

    @staticmethod
    def _ctx(tmp_path) -> JobContext:
        """The harvest job's context, as the harness builds it for a databaseless run."""
        return JobContext(
            name=raw_harvest.JOB_SLUG,
            args=argparse.Namespace(root=str(tmp_path), ttl_days=0.0),
            session=None,
            session_factory=None,
            dry_run=False,
        )

    def test_drift_degrades_and_names_the_services(self, caplog) -> None:
        with caplog.at_level(logging.ERROR):
            result = job_outcome(
                {**self.OK_RUN, "wsdl_drift": ["SponsorService"], "wsdl_unchecked": []}
            )
        assert result.outcome == "degraded"
        [record] = [r for r in caplog.records if r.message == "wsl_raw_harvest_wsdl_drift"]
        assert record.services == ["SponsorService"]
        assert record.issue == "#563"

    def test_an_unchecked_binding_alone_stays_ok(self) -> None:
        """An outage before the first call loads no WSDL; the outage reports itself."""
        result = job_outcome(
            {**self.OK_RUN, "wsdl_drift": [], "wsdl_unchecked": ["CommitteeMeetingService"]}
        )
        assert result.outcome == "ok"

    async def test_a_rollover_acceptance_does_not_cover_drift(self, tmp_path) -> None:
        summary = await _rollover_night(tmp_path, sponsors=0)
        summary["wsdl_drift"] = ["CommitteeService"]
        assert job_outcome(summary, accepted=TestAcceptedRosterFaults.ACCEPT).outcome == "degraded"

    async def test_the_job_checks_the_clients_it_harvested_with(
        self, tmp_path, monkeypatch
    ) -> None:
        harvested: dict[str, object] = {}
        checked: list[object] = []

        async def fake_harvest(root, *, ttl_days, **clients):
            harvested.update(clients)
            return dict(self.OK_RUN)

        def fake_drift(clients):
            checked.extend(clients)
            return {"wsdl_drift": ["SponsorService"], "wsdl_unchecked": []}

        monkeypatch.setattr(raw_harvest, "harvest_raw", fake_harvest)
        monkeypatch.setattr(raw_harvest, "binding_drift", fake_drift)
        ctx = self._ctx(tmp_path)
        result = await raw_harvest._harvest_job(ctx)

        assert {c.service for c in harvested.values()} == {
            "CommitteeService",
            "CommitteeMeetingService",
            "SponsorService",
        }
        assert sorted(map(id, checked)) == sorted(map(id, harvested.values()))
        assert result.counters["wsdl_drift"] == ["SponsorService"]
        assert result.outcome == "degraded"

    async def test_a_drift_check_that_crashes_is_contained(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        """The check runs after the night's wires landed: its own crash (a zeep internals
        change, an unparseable vendored file) degrades the run with the harvest's counters
        intact — never a ``failed`` job that hides how far the harvest got."""

        async def fake_harvest(root, *, ttl_days, **clients):
            return dict(self.OK_RUN)

        def crashing_drift(clients):
            raise AttributeError("zeep moved an internal")

        monkeypatch.setattr(raw_harvest, "harvest_raw", fake_harvest)
        monkeypatch.setattr(raw_harvest, "binding_drift", crashing_drift)
        ctx = self._ctx(tmp_path)
        with caplog.at_level(logging.ERROR):
            result = await raw_harvest._harvest_job(ctx)

        assert result.outcome == "degraded"
        assert result.counters["fetched"] == self.OK_RUN["fetched"]
        assert result.counters["wsdl_check_failed"] == 1
        assert any(r.message == "wsl_raw_harvest_wsdl_check_failed" for r in caplog.records)

    def test_a_failed_drift_check_degrades(self) -> None:
        assert job_outcome({**self.OK_RUN, "wsdl_check_failed": 1}).outcome == "degraded"


NEXT = "2027-28"
#: The day WSL first answered 2027-28 — pinned, so "next" is judged against a fixed clock.
PUBLISHED_ON = date(2026, 11, 24)


class _SponsorsByBiennium:
    """``GetSponsors`` answering per biennium: rows where published, an empty envelope
    elsewhere (what WSL answers for an unpublished one)."""

    def __init__(self, rows: dict[str, int], *, dead: frozenset[str] = frozenset()) -> None:
        self.rows = rows
        self.dead = dead

    async def fetch_sponsors(self, biennium: str) -> _Records:
        if biennium in self.dead:
            raise RuntimeError("connection reset")
        count = self.rows.get(biennium, 0)
        return _Records(
            wire=f"sponsors-{biennium}".encode(), records=[{"Id": i} for i in range(count)]
        )


class _CommitteesByBiennium(FakeCommitteeClient):
    """``GetCommittees`` faulting for an unpublished biennium; members failing per biennium."""

    def __init__(
        self,
        *,
        unpublished: frozenset[str] = frozenset(),
        members_fail_in: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self.unpublished = unpublished
        self.members_fail_in = members_fail_in
        self.active_calls = 0
        self.roster_asked: list[str] = []

    async def fetch_committees(self, biennium: str) -> _Wire:
        self.roster_asked.append(biennium)
        if biennium in self.unpublished:
            raise Fault("DataPortal.Fetch failed (Object reference not set to an instance)")
        return await super().fetch_committees(biennium)

    async def fetch_active_committees(self) -> _Wire:
        self.active_calls += 1
        return await super().fetch_active_committees()

    async def fetch_historical_committee_members(
        self, biennium: str, agency: str, committee_name: str
    ) -> _Wire:
        if biennium in self.members_fail_in:
            raise RuntimeError("soap fault")
        committee = next(c for c in COMMITTEES if c["Name"] == committee_name)
        return _Wire(wire=f"members-{biennium}-{committee['Id']}".encode())


async def _with_next(tmp_path, *, sponsors, committees, biennium: str = BIENNIUM) -> dict:
    return await harvest_raw(
        tmp_path,
        biennium=biennium,
        committee_client=committees,
        meeting_client=FakeMeetingClient(),
        sponsor_client=sponsors,
        today=PUBLISHED_ON,
    )


def _entries(tmp_path) -> dict[str, str]:
    store = RawStore(tmp_path, SOURCE_SLUG)
    [manifest_path] = store.manifest_paths()
    return {e["resource_id"]: e["status"] for e in json.loads(manifest_path.read_text())["entries"]}


class TestNextBiennium:
    """#599: WSL published 2027-28 on 2026-11-24, while 2025-26 was current. The run also
    lands the next biennium's rosters from the night they answer: sponsors, the committee
    roster and its member fan-out. Never ``GetActiveCommittees`` (no biennium argument: it
    would archive the current snapshot under the next id) and never a meetings window."""

    async def test_lands_the_next_biennium_once_published(self, tmp_path) -> None:
        committees = _CommitteesByBiennium()
        summary = await _with_next(
            tmp_path, sponsors=_SponsorsByBiennium({BIENNIUM: 2, NEXT: 3}), committees=committees
        )
        recorded = set(_entries(tmp_path))
        landed_next = {r for r in recorded if NEXT in r}
        assert landed_next == {f"sponsors:{NEXT}", f"committees-roster:{NEXT}"} | {
            committee_members_hist_resource_id(NEXT, str(c["Id"]), c["Agency"], c["Name"])
            for c in COMMITTEES
        }
        assert committees.active_calls == 1
        assert summary["next_biennium"] == {
            "biennium": NEXT,
            "sponsor_rows": 3,
            "fetched": 4,
            "unchanged": 0,
            "skipped_fresh": 0,
            "errors": 0,
            "roster_unpublished": 0,
            "fanout_skipped": 0,
            "fanout_attempted": 2,
            "fanout_landed": 2,
        }
        assert job_outcome(summary).outcome == "ok"

    async def test_the_current_counters_are_the_current_biennium_alone(self, tmp_path) -> None:
        summary = await _with_next(
            tmp_path,
            sponsors=_SponsorsByBiennium({BIENNIUM: 2, NEXT: 3}),
            committees=_CommitteesByBiennium(),
        )
        assert summary["fetched"] == 4 + len(COMMITTEES)
        assert summary["fanout_landed"] == len(COMMITTEES)

    async def test_an_unpublished_next_biennium_lands_nothing_and_asks_no_roster(
        self, tmp_path
    ) -> None:
        committees = _CommitteesByBiennium(unpublished=frozenset({NEXT}))
        summary = await _with_next(
            tmp_path, sponsors=_SponsorsByBiennium({BIENNIUM: 2}), committees=committees
        )
        assert not [r for r in _entries(tmp_path) if NEXT in r]
        assert committees.roster_asked == [BIENNIUM]
        assert summary["next_biennium"]["sponsor_rows"] == 0
        assert summary["next_biennium"]["fetched"] == 0
        assert job_outcome(summary).outcome == "ok"

    async def test_sponsors_before_committees_is_not_yet_not_an_error(self, tmp_path) -> None:
        """A biennium's sponsors can answer while its ``GetCommittees`` still faults: the
        sponsors land, the roster is "not yet", and nothing is recorded ``err``."""
        summary = await _with_next(
            tmp_path,
            sponsors=_SponsorsByBiennium({BIENNIUM: 2, NEXT: 3}),
            committees=_CommitteesByBiennium(unpublished=frozenset({NEXT})),
        )
        entries = _entries(tmp_path)
        assert entries[f"sponsors:{NEXT}"] == "ok"
        assert f"committees-roster:{NEXT}" not in entries
        assert summary["next_biennium"]["roster_unpublished"] == 1
        assert summary["next_biennium"]["errors"] == 0
        assert job_outcome(summary).outcome == "ok"

    async def test_a_next_biennium_transport_error_degrades(self, tmp_path) -> None:
        summary = await _with_next(
            tmp_path,
            sponsors=_SponsorsByBiennium({BIENNIUM: 2}, dead=frozenset({NEXT})),
            committees=_CommitteesByBiennium(),
        )
        assert _entries(tmp_path)[f"sponsors:{NEXT}"] == "err"
        assert summary["next_biennium"]["errors"] == 1
        assert summary["errors"] == 0
        assert job_outcome(summary).outcome == "degraded"

    async def test_a_lost_next_biennium_fanout_degrades(self, tmp_path) -> None:
        summary = await _with_next(
            tmp_path,
            sponsors=_SponsorsByBiennium({BIENNIUM: 2, NEXT: 3}),
            committees=_CommitteesByBiennium(members_fail_in=frozenset({NEXT})),
        )
        assert summary["next_biennium"]["fanout_attempted"] == 2
        assert summary["next_biennium"]["fanout_landed"] == 0
        assert summary["fanout_landed"] == len(COMMITTEES)
        assert job_outcome(summary).outcome == "degraded"

    async def test_the_next_biennium_follows_the_run(self, tmp_path) -> None:
        summary = await _with_next(
            tmp_path,
            sponsors=_SponsorsByBiennium({NEXT: 3}),
            committees=_CommitteesByBiennium(),
            biennium=NEXT,
        )
        assert summary["next_biennium"]["biennium"] == "2029-30"

    async def test_a_run_pinned_to_a_past_biennium_lands_no_other(self, tmp_path) -> None:
        """CR 1: "next" is ahead of the clock, not of the pin. A backfill pinned to 2019-20
        must not also land 2021-22 — a biennium the run was never asked for."""
        committees = _CommitteesByBiennium()
        summary = await _with_next(
            tmp_path,
            sponsors=_SponsorsByBiennium({"2019-20": 2, "2021-22": 3}),
            committees=committees,
            biennium="2019-20",
        )
        assert summary["next_biennium"] is None
        assert not [r for r in _entries(tmp_path) if "2021-22" in r]
        assert committees.roster_asked == ["2019-20"]

    async def test_the_current_biennium_pinned_still_looks_ahead(self, tmp_path) -> None:
        summary = await _with_next(
            tmp_path,
            sponsors=_SponsorsByBiennium({BIENNIUM: 2, NEXT: 3}),
            committees=_CommitteesByBiennium(),
        )
        assert summary["next_biennium"]["biennium"] == NEXT
