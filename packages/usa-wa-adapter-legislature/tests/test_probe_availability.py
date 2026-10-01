"""Write-free availability probe for the next biennium's WSL rosters (#135 item 4).

Measured, not assumed: when ``GetSponsors``, ``GetCommittees`` and ``GetCommitteeMembers``
start answering for 2027-28 decides how long the rollover's nightly warnings last. The
probe logs one measurement per run and reports a CHANGE as news (exit 4, the alert the
roster re-check also uses as its product); an unchanged measurement is a quiet exit 0.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from zeep.exceptions import Fault

from clearinghouse_core.runs import OUTCOME_DEGRADED, OUTCOME_OK
from usa_wa_adapter_legislature.probe_availability import (
    Availability,
    changed_fields,
    measure,
    probe,
)
from usa_wa_adapter_legislature.transport import WireFetch

BIENNIUM = "2027-28"

#: What GetCommittees answered for 2027-28 on 2026-10-01: not an empty list, a server fault.
UNPUBLISHED_FAULT = (
    "DataPortal.Fetch failed (Object reference not set to an instance of an object.)"
)


def _fetch(records: list[dict]) -> WireFetch:
    return WireFetch(records=records, wire=b"<x/>", content_type="text/xml")


class FakeSponsors:
    def __init__(self, members: list[dict]) -> None:
        self.members = members
        self.calls: list[str] = []

    async def fetch_sponsors(self, biennium: str) -> WireFetch:
        self.calls.append(biennium)
        return _fetch(self.members)


class FakeCommittees:
    def __init__(self, committees: list[dict] | None, members: dict[str, int] | None = None):
        self.committees = committees
        self.members = members or {}
        self.member_calls: list[tuple[str, str, str]] = []

    async def fetch_committees(self, biennium: str) -> WireFetch:
        if self.committees is None:
            raise Fault(UNPUBLISHED_FAULT)
        return _fetch(self.committees)

    async def fetch_historical_committee_members(
        self, biennium: str, agency: str, committee_name: str
    ) -> WireFetch:
        self.member_calls.append((biennium, agency, committee_name))
        return _fetch([{"Id": i} for i in range(self.members.get(committee_name, 0))])


def _members(house: int, senate: int) -> list[dict]:
    return [{"Agency": "House"}] * house + [{"Agency": "Senate"}] * senate


async def test_an_unpublished_biennium_measures_empty_and_faulted() -> None:
    """The state on 2026-10-01: GetSponsors answers empty, GetCommittees faults."""
    committees = FakeCommittees(None)
    result = await measure(BIENNIUM, sponsor_client=FakeSponsors([]), committee_client=committees)

    assert result == Availability(
        biennium=BIENNIUM,
        sponsors_house=0,
        sponsors_senate=0,
        committees=None,
        committees_with_members=0,
        committee_members=0,
        committee_fault=UNPUBLISHED_FAULT,
    )
    assert committees.member_calls == []


async def test_a_published_biennium_counts_every_roster() -> None:
    committees = FakeCommittees(
        [{"Agency": "House", "Name": "Appropriations"}, {"Agency": "Senate", "Name": "Rules"}],
        members={"Appropriations": 30},
    )
    result = await measure(
        BIENNIUM, sponsor_client=FakeSponsors(_members(98, 49)), committee_client=committees
    )

    assert (result.sponsors_house, result.sponsors_senate) == (98, 49)
    assert result.committees == 2
    assert (result.committees_with_members, result.committee_members) == (1, 30)
    assert committees.member_calls == [
        (BIENNIUM, "House", "Appropriations"),
        (BIENNIUM, "Senate", "Rules"),
    ]
    assert result.committee_fault is None


def _availability(**over) -> Availability:
    fields = {
        "biennium": BIENNIUM,
        "sponsors_house": 0,
        "sponsors_senate": 0,
        "committees": None,
        "committees_with_members": 0,
        "committee_members": 0,
        "committee_fault": UNPUBLISHED_FAULT,
    }
    return Availability(**{**fields, **over})


def test_a_first_empty_measurement_is_a_baseline_not_news() -> None:
    assert changed_fields(None, _availability()) == []


def test_a_first_measurement_that_finds_rows_is_news() -> None:
    assert changed_fields(None, _availability(sponsors_house=12)) == ["sponsors_house"]


def test_the_fault_text_alone_is_not_news() -> None:
    """Only the counts are the measurement; a reworded server error is not."""
    previous = json.loads(json.dumps(_availability().as_record(datetime.now(UTC))))
    assert changed_fields(previous, _availability(committee_fault="something else")) == []


def test_committees_answering_for_the_first_time_is_news() -> None:
    previous = _availability().as_record(datetime.now(UTC))
    assert changed_fields(previous, _availability(committees=0, committee_fault=None)) == [
        "committees"
    ]


async def test_probe_logs_every_run_and_reports_a_change_as_degraded(tmp_path) -> None:
    log = tmp_path / "availability.jsonl"
    empty = (FakeSponsors([]), FakeCommittees(None))

    first = await probe(BIENNIUM, log, sponsor_client=empty[0], committee_client=empty[1])
    again = await probe(BIENNIUM, log, sponsor_client=empty[0], committee_client=empty[1])
    published = await probe(
        BIENNIUM,
        log,
        sponsor_client=FakeSponsors(_members(60, 25)),
        committee_client=FakeCommittees(None),
    )

    assert (first.outcome, again.outcome) == (OUTCOME_OK, OUTCOME_OK)
    assert published.outcome == OUTCOME_DEGRADED
    assert published.counters["changed"] == ["sponsors_house", "sponsors_senate"]
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) == 3
    assert records[-1]["sponsors_house"] == 60
    assert records[-1]["checked_at"].endswith("Z")


async def test_the_log_is_per_biennium(tmp_path) -> None:
    """A measurement of another biennium in the same file is not this one's baseline."""
    log = tmp_path / "availability.jsonl"
    await probe(
        "2029-30",
        log,
        sponsor_client=FakeSponsors(_members(98, 49)),
        committee_client=FakeCommittees(None),
    )

    result = await probe(
        BIENNIUM, log, sponsor_client=FakeSponsors([]), committee_client=FakeCommittees(None)
    )

    assert result.outcome == OUTCOME_OK


class Unreachable:
    """A client that must never be called."""

    def __getattr__(self, name):
        raise AssertionError(f"the probe asked WSL ({name}) after its window closed")


async def test_after_its_window_the_probe_asks_nothing_and_logs_nothing(tmp_path) -> None:
    """The timer is a plain daily one (the timer-doc drift guard reads no date ranges), so
    the window closes here: past ``until``, mid-biennium committee churn must not keep
    mailing "news" for a question the rollover already answered."""
    log = tmp_path / "availability.jsonl"
    result = await probe(
        BIENNIUM,
        log,
        until=date(2027, 2, 28),
        today=date(2027, 3, 1),
        sponsor_client=Unreachable(),
        committee_client=Unreachable(),
    )

    assert result.outcome == OUTCOME_OK
    assert result.counters == {"window_closed": "2027-02-28"}
    assert not log.exists()


async def test_the_last_day_of_the_window_still_measures(tmp_path) -> None:
    log = tmp_path / "availability.jsonl"
    await probe(
        BIENNIUM,
        log,
        until=date(2027, 2, 28),
        today=date(2027, 2, 28),
        sponsor_client=FakeSponsors([]),
        committee_client=FakeCommittees(None),
    )

    assert log.exists()
