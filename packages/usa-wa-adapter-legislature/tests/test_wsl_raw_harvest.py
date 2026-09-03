"""WSL raw-tier harvest (#304): the daily SOAP set + member fan-out into files.

The fan-out enumerates committees from the wire fetched in the same run (not
from Postgres, as the daily refresh does) — the raw tier must be buildable with
no database at all.
"""

import json
from dataclasses import dataclass

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.meetings.windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.raw_harvest import SOURCE_SLUG, harvest_raw

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
