"""Transport tests — votewa ``SOSFilingsClient`` against a recorded cassette.

The round-trip test replays a real votewa CSV export and pins the field names + House row
shape the position resolver depends on, and proves the offline re-parser recovers the live
parse from the archived wire (the #56 cache path).
"""

from __future__ import annotations

import pytest
from usa_wa_adapter_sos.filings.transport import (
    ALL_COUNTIES,
    DEFAULT_SOS_MIN_REQUEST_INTERVAL,
    SOSFilingsClient,
    _AsyncRateLimiter,
    _env_min_interval,
    general_election_date,
    parse_whofiled,
)

# The 2015-16 biennium's House was up in the Nov 2016 general.
ELECTION_YEAR = 2016


def test_general_election_date_is_november() -> None:
    assert general_election_date(2016) == "201611"
    assert general_election_date(2008) == "200811"


def test_whofiled_params_select_statewide_election() -> None:
    params = SOSFilingsClient.whofiled_params("201611")
    assert params["electionDate"] == "201611"
    assert params["countyCode"] == ALL_COUNTIES


def test_parse_whofiled_reads_header_and_rows() -> None:
    wire = b"RaceName,BallotName,PartyName\r\nState Senator,Jane Doe,(Prefers X Party)\r\n"
    rows = parse_whofiled(wire)
    assert rows == [
        {"RaceName": "State Senator", "BallotName": "Jane Doe", "PartyName": "(Prefers X Party)"}
    ]


def test_parse_whofiled_tolerates_utf8_bom() -> None:
    wire = "﻿RaceName,BallotName\r\nState Senator,Jane Doe\r\n".encode()
    assert parse_whofiled(wire)[0]["RaceName"] == "State Senator"


@pytest.mark.asyncio
async def test_fetch_whofiled_round_trip(sos_vcr) -> None:
    with sos_vcr.use_cassette("whofiled_2016.yaml"):
        fetch = await SOSFilingsClient().fetch_whofiled(ELECTION_YEAR)

    assert fetch.records, "expected candidate filings"
    assert fetch.wire, "expected pristine archival CSV bytes"
    assert "csv" in fetch.content_type

    # State Representative rows carry the position + district + party the resolver keys on.
    reps = [r for r in fetch.records if r.get("RaceName", "").startswith("State Representative")]
    assert reps, "expected State Representative rows"
    for row in reps:
        assert "Pos. 1" in row["RaceName"] or "Pos. 2" in row["RaceName"]
        assert row["RaceJurisdictionName"].startswith("Legislative District")
        assert row["BallotName"]
        assert "Party" in row["PartyName"]

    # Offline re-parse of the archived wire recovers the live parse (#56 cache path).
    assert parse_whofiled(fetch.wire) == fetch.records


class _FakeClock:
    """Monotonic clock where ``sleep(d)`` advances time by ``d`` (models a real sleep)."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


@pytest.mark.asyncio
async def test_rate_limiter_spaces_sequential_calls() -> None:
    clock = _FakeClock()
    lim = _AsyncRateLimiter(1.0, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(3):
        await lim.acquire()
    # First acquire reserves slot 0 (no wait); each subsequent one waits one interval.
    assert clock.sleeps == [1.0, 1.0]


@pytest.mark.asyncio
async def test_rate_limiter_zero_interval_never_sleeps() -> None:
    clock = _FakeClock()
    lim = _AsyncRateLimiter(0.0, monotonic=clock.monotonic, sleep=clock.sleep)
    await lim.acquire()
    assert clock.sleeps == []


def test_env_min_interval_default_and_malformed(monkeypatch) -> None:
    monkeypatch.delenv("USA_WA_SOS_MIN_REQUEST_INTERVAL", raising=False)
    assert _env_min_interval() == DEFAULT_SOS_MIN_REQUEST_INTERVAL
    monkeypatch.setenv("USA_WA_SOS_MIN_REQUEST_INTERVAL", "not-a-number")
    assert _env_min_interval() == DEFAULT_SOS_MIN_REQUEST_INTERVAL
    monkeypatch.setenv("USA_WA_SOS_MIN_REQUEST_INTERVAL", "0")
    assert _env_min_interval() == 0.0
