"""C1a committee lifecycle-window derivation (usa-wa#124).

Per-Id, objective: from each committee WSL ``Id``'s roster-presence bienniums,
derive its ``founded`` (first observed biennium start) / ``dissolved`` (last
observed biennium end, non-heads only). ``founded`` is floor-gated — omitted for a
committee present in the earliest archived biennium (it may predate the archive).
"""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.committee_lifecycle import (
    CommitteeWindow,
    collect_committee_presence,
    derive_committee_windows,
)

ARCHIVE = ["1999-00", "2001-02", "2003-04", "2005-06", "2019-20", "2021-22", "2023-24", "2025-26"]
CURRENT = "2025-26"


def _derive(presence):
    return derive_committee_windows(presence, current_biennium=CURRENT, archived_bienniums=ARCHIVE)


def test_current_head_is_active_no_dissolved():
    """A committee present in the current biennium is the live head: no dissolved."""
    out = _derive({"28244": ["2019-20", "2021-22", "2023-24", "2025-26"]})
    w = out["28244"]
    assert w.is_current is True
    assert w.dissolved_year is None
    assert w.founded_year == 2019  # first observed, safely after floor


def test_historical_id_gets_dissolved_at_last_biennium_end():
    """A committee last seen in 2003-04 and absent since dissolved ~2004."""
    out = _derive({"10171": ["2001-02", "2003-04"]})
    w = out["10171"]
    assert w.is_current is False
    assert w.founded_year == 2001
    assert w.dissolved_year == 2004  # end year of the last observed biennium


def test_founded_gated_when_present_in_floor_biennium():
    """A committee present in the earliest archived biennium may predate it → no founded."""
    out = _derive({"rules": ["1999-00", "2001-02", "2003-04", "2025-26"]})
    w = out["rules"]
    assert w.founded_year is None  # floor-gated: can't assert a true founding
    assert w.is_current is True
    assert w.dissolved_year is None


def test_founded_gated_historical_still_dissolves():
    """A floor-present committee that later disappears: no founded, but a dissolved."""
    out = _derive({"old": ["1999-00", "2001-02"]})
    w = out["old"]
    assert w.founded_year is None
    assert w.dissolved_year == 2002


def test_newly_founded_current_committee():
    """First appears in the current biennium → founded now, still active."""
    out = _derive({"new": ["2025-26"]})
    w = out["new"]
    assert w.founded_year == 2025
    assert w.is_current is True
    assert w.dissolved_year is None


def test_unordered_presence_is_sorted():
    out = _derive({"x": ["2023-24", "2019-20", "2021-22"]})
    w = out["x"]
    assert w.founded_year == 2019
    assert w.dissolved_year == 2024  # last = 2023-24


def test_empty_presence_skipped():
    assert _derive({"x": []}) == {}


def test_no_archive_domain_omits_founded():
    """With no known floor, founded cannot be asserted for anyone."""
    out = derive_committee_windows(
        {"x": ["2021-22"]}, current_biennium=CURRENT, archived_bienniums=[]
    )
    assert out["x"].founded_year is None


def test_window_is_frozen_dataclass():
    w = CommitteeWindow(source_id="1", is_current=True, founded_year=None, dissolved_year=None)
    with pytest.raises(Exception):
        w.founded_year = 2000  # type: ignore[misc]


class _FakeProvider:
    def __init__(self, bienniums, records_by_biennium):
        self._bienniums = bienniums
        self._records = records_by_biennium

    async def archived_bienniums(self):
        return list(self._bienniums)

    async def roster_records(self, biennium):
        return list(self._records.get(biennium, []))


async def test_collect_committee_presence_reads_provider():
    provider = _FakeProvider(
        ["2001-02", "2003-04"],
        {
            "2001-02": [{"Id": "10171", "LongName": "A"}, {"Id": "28244", "LongName": "B"}],
            "2003-04": [{"Id": "28244", "LongName": "B"}],
        },
    )
    presence = await collect_committee_presence(provider)
    assert presence["10171"] == {"2001-02"}
    assert presence["28244"] == {"2001-02", "2003-04"}
