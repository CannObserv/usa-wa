"""Unit tests for the sponsor→observation projection (#78 increment 2, Phase B).

Pure projection of archived WSL ``GetSponsors`` member rows into tenure
:class:`Observation`s the span builder consumes: a **party** observation (major party only)
and, for a Senate row, a **chamber-senate** seat observation (keyed on LD). House chamber
observations come from PDC (#79); committee from #82.
"""

from __future__ import annotations

from usa_wa_adapter_legislature.sponsor_observations import (
    KIND_PARTY,
    KIND_SENATE,
    build_sponsor_observations,
)
from usa_wa_adapter_legislature.tenure_spans import Observation


def _member(mid, *, agency="Senate", district="5", party="D", first="Ann", last="Rivers"):
    return {
        "Id": mid,
        "FirstName": first,
        "LastName": last,
        "District": district,
        "Party": party,
        "Agency": agency,
        "Name": f"{first} {last}",
    }


def test_senate_member_yields_party_and_seat_observations():
    obs = build_sponsor_observations({"2025-26": [_member(100)]})
    assert set(obs) == {
        Observation("100", KIND_PARTY, "democratic", "2025-26"),
        Observation("100", KIND_SENATE, "5", "2025-26"),
    }


def test_house_member_yields_party_only_no_senate_seat():
    # House chamber seats need a PDC Position (#79) — the projection emits no chamber obs.
    obs = build_sponsor_observations({"2025-26": [_member(200, agency="House", district="42")]})
    assert obs == [Observation("200", KIND_PARTY, "democratic", "2025-26")]


def test_independent_or_blank_party_emits_no_party_observation():
    obs = build_sponsor_observations(
        {"2025-26": [_member(300, party="I", agency="House", district="7")]}
    )
    assert obs == []  # no major party, House → no chamber seat either


def test_name_blanked_stub_is_skipped():
    stub = {"Id": 999, "Name": " ", "FirstName": None, "LastName": None, "Agency": "Senate"}
    assert build_sponsor_observations({"2025-26": [stub]}) == []


def test_senate_row_without_district_emits_party_only():
    obs = build_sponsor_observations({"2025-26": [_member(400, district=None)]})
    assert obs == [Observation("400", KIND_PARTY, "democratic", "2025-26")]


def test_projects_across_multiple_biennia():
    members_by_biennium = {
        "2023-24": [_member(100)],
        "2025-26": [_member(100)],
    }
    obs = build_sponsor_observations(members_by_biennium)
    # Same member observed in both biennia → observations carry the per-biennium label so
    # the span builder can merge them.
    assert Observation("100", KIND_SENATE, "5", "2023-24") in obs
    assert Observation("100", KIND_SENATE, "5", "2025-26") in obs
    assert len(obs) == 4  # party + seat, twice


def test_excluded_member_emits_nothing_in_that_biennium_only():
    """#105 (b): a committee-corroborated stale member (Kilduff/Senn/Nguyen) is excluded
    per-biennium — their party AND chamber-senate observations drop for the stale bienniums,
    while their genuinely-served bienniums still emit (so the merged span ends at the real
    departure boundary)."""
    members_by_biennium = {
        "2019-20": [_member(100)],
        "2021-22": [_member(100)],
    }
    obs = build_sponsor_observations(
        members_by_biennium, exclude_ids_by_biennium={"2021-22": {"100"}}
    )
    assert obs == [
        Observation("100", KIND_PARTY, "democratic", "2019-20"),
        Observation("100", KIND_SENATE, "5", "2019-20"),
    ]
