"""Write-free probe of how far back WSL committee/meeting data reaches (#64).

Walks bienniums backward from a start label, tallying committee rows, meeting
records, and meeting wire bytes per biennium, and stops after N consecutive empty
bienniums (the earliest-boundary heuristic). No runner, no DB, no archival — the
probe answers "how much history exists" before sub-project 3 commits to fetching it.
"""

from types import SimpleNamespace

from usa_wa_adapter_legislature import probe_committee_extent as probe


class _FakeCommitteeClient:
    def __init__(self, by_biennium):
        self._by = by_biennium

    async def get_committees(self, biennium):
        return self._by.get(biennium, [])


class _FakeMeetingClient:
    """Returns a WireFetch-shaped object keyed by the window's begin year."""

    def __init__(self, by_year):
        self._by = by_year
        self.windows = []

    async def fetch_committee_meetings(self, begin, end):
        self.windows.append((begin, end))
        records, wire = self._by.get(begin.year, ([], b""))
        return SimpleNamespace(records=records, wire=wire, content_type="text/xml")


async def test_walks_backward_and_stops_after_two_empties():
    committees = _FakeCommitteeClient({"2025-26": [{"Id": 1}, {"Id": 2}], "2023-24": [{"Id": 1}]})
    meetings = _FakeMeetingClient(
        {
            2025: ([{"Id": 1}], b"x" * 100),
            2023: ([{"Id": 1}, {"Id": 2}], b"y" * 200),
            # 2021, 2019 absent → empty
        }
    )
    result = await probe.probe_extent(committees, meetings, start_biennium="2025-26", max_empty=2)
    labels = [r["biennium"] for r in result["bienniums"]]
    # 2025-26 (data), 2023-24 (data), 2021-22 (empty), 2019-20 (empty) → stop
    assert labels == ["2025-26", "2023-24", "2021-22", "2019-20"]
    assert result["stopped_after_empty"] == 2
    assert result["earliest_with_data"] == "2023-24"


async def test_tallies_counts_and_wire_bytes():
    committees = _FakeCommitteeClient({"2025-26": [{"Id": 1}, {"Id": 2}, {"Id": 3}]})
    meetings = _FakeMeetingClient({2025: ([{"Id": 1}, {"Id": 2}], b"z" * 500)})
    result = await probe.probe_extent(committees, meetings, start_biennium="2025-26", max_empty=2)
    top = result["bienniums"][0]
    assert top["committee_count"] == 3
    assert top["meeting_count"] == 2
    assert top["meeting_wire_bytes"] == 500
    assert result["totals"]["committee_count"] == 3
    assert result["totals"]["meeting_wire_bytes"] == 500


async def test_empty_when_both_services_empty():
    committees = _FakeCommitteeClient({})  # nothing anywhere
    meetings = _FakeMeetingClient({})
    result = await probe.probe_extent(committees, meetings, start_biennium="2025-26", max_empty=2)
    # first two bienniums are already empty → stop immediately
    assert [r["biennium"] for r in result["bienniums"]] == ["2025-26", "2023-24"]
    assert result["earliest_with_data"] is None


async def test_committee_only_biennium_is_not_empty():
    """A biennium with committees but no meetings still counts as data (not empty)."""
    committees = _FakeCommitteeClient({"2025-26": [{"Id": 1}], "2023-24": [{"Id": 1}]})
    meetings = _FakeMeetingClient({})  # no meetings any window
    result = await probe.probe_extent(committees, meetings, start_biennium="2025-26", max_empty=2)
    labels = [r["biennium"] for r in result["bienniums"]]
    # 2025-26 + 2023-24 have committees → not empty; 2021-22, 2019-20 empty → stop
    assert labels == ["2025-26", "2023-24", "2021-22", "2019-20"]
    assert result["earliest_with_data"] == "2023-24"


async def test_safety_bound_caps_the_walk():
    """A source that never goes empty is bounded so the probe can't loop forever."""
    meetings = _FakeMeetingClient({})

    # force never-empty by making every biennium report data via a wildcard client
    class _AlwaysData:
        async def get_committees(self, _b):
            return [{"Id": 1}]

    result = await probe.probe_extent(
        _AlwaysData(), meetings, start_biennium="2025-26", max_empty=2, max_bienniums=5
    )
    assert len(result["bienniums"]) == 5
    assert result["stopped_after_empty"] == 0  # hit the cap, not the empty run
