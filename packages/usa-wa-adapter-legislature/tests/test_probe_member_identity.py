"""Unit tests for the write-free member-identity probe (P1b step 0).

The probe answers one question before any member ingest: **is the WSL member ``Id`` a
stable ``Person.source_id``** — the same value for a person across the sponsor and
committee-member endpoints, and across bienniums for a re-elected member? Its pure
comparison functions are exercised here with fakes (no live WSL); the CLI runs live to
record the finding in the plan's Revisions.
"""

from __future__ import annotations

from usa_wa_adapter_legislature.probe_member_identity import (
    compare_id_stability,
    is_person,
    name_key,
    probe_member_identity,
)
from usa_wa_adapter_legislature.transport import WireFetch


def _m(id_, first, last, district="1", party="D", name=None):
    return {
        "Id": id_,
        "FirstName": first,
        "LastName": last,
        "District": district,
        "Party": party,
        "Name": name if name is not None else f"{first} {last}",
    }


# --- is_person / name_key -----------------------------------------------------


def test_is_person_requires_first_and_last() -> None:
    assert is_person(_m(1, "Peter", "Abbarno"))
    # name-blanked stub of a superseded/departed tenure: real Id, blank name/district/party
    assert not is_person({"Id": 2006, "Name": " ", "FirstName": None, "LastName": None})
    assert not is_person({"Id": 3, "FirstName": "", "LastName": ""})


def test_name_key_is_case_and_whitespace_normalized() -> None:
    assert name_key(_m(1, " Peter ", "ABBARNO")) == ("abbarno", "peter")


# --- compare_id_stability -----------------------------------------------------


def test_compare_id_stability_all_same_id_is_stable() -> None:
    a = [_m(10, "Ann", "Rivers"), _m(20, "Joe", "Nguyen")]
    b = [_m(10, "Ann", "Rivers"), _m(20, "Joe", "Nguyen"), _m(30, "New", "Person")]
    r = compare_id_stability(a, b)
    assert r["matched"] == 2
    assert r["same_id"] == 2
    assert r["diff_id"] == 0
    assert r["only_a"] == 0
    assert r["only_b"] == 1  # the b-only newcomer
    assert r["stable"] is True
    assert r["divergences"] == []


def test_compare_id_stability_flags_divergent_id() -> None:
    a = [_m(10, "Ann", "Rivers")]
    b = [_m(999, "Ann", "Rivers")]  # same person, different Id → re-key
    r = compare_id_stability(a, b)
    assert r["matched"] == 1
    assert r["diff_id"] == 1
    assert r["stable"] is False
    assert r["divergences"][0]["id_a"] == 10
    assert r["divergences"][0]["id_b"] == 999


def test_compare_id_stability_empty_overlap_is_not_stable() -> None:
    # No shared names → no evidence of stability (guard against a vacuous "stable").
    r = compare_id_stability([_m(1, "A", "One")], [_m(2, "B", "Two")])
    assert r["matched"] == 0
    assert r["stable"] is False


# --- probe_member_identity (orchestration, fakes) -----------------------------


class _FakeSponsorClient:
    def __init__(self, by_biennium: dict[str, list[dict]]) -> None:
        self._by = by_biennium
        self.calls: list[str] = []

    async def get_sponsors(self, biennium: str) -> list[dict]:
        self.calls.append(biennium)
        return self._by.get(biennium, [])


class _FakeCommitteeClient:
    def __init__(self, active: list[dict], members: dict[tuple[str, str], list[dict]]) -> None:
        self._active = active
        self._members = members
        self.member_calls: list[tuple[str, str]] = []

    async def fetch_active_committees(self) -> WireFetch:
        return WireFetch(records=self._active, wire=b"", content_type="text/xml")

    async def get_active_committee_members(self, agency: str, name: str) -> list[dict]:
        self.member_calls.append((agency, name))
        return self._members.get((agency, name), [])


async def test_probe_reports_stable_id_source() -> None:
    rivers, nguyen = _m(10, "Ann", "Rivers"), _m(20, "Joe", "Nguyen")
    blanked_stub = {"Id": 2006, "Name": " ", "FirstName": None, "LastName": None}
    sponsor = _FakeSponsorClient(
        {
            "2025-26": [rivers, nguyen, blanked_stub],
            "2023-24": [_m(10, "Ann", "Rivers"), _m(20, "Joe", "Nguyen"), _m(40, "Gone", "Member")],
        }
    )
    committee = _FakeCommitteeClient(
        active=[{"Agency": "Senate", "Name": "Rules"}, {"Agency": "House", "Name": "Blank"}],
        members={
            # committee endpoint spells party in full — must not disturb Id matching
            ("Senate", "Rules"): [_m(10, "Ann", "Rivers", party="Republican")],
        },
    )
    result = await probe_member_identity(
        sponsor, committee, biennium="2025-26", prior_biennium="2023-24", committee_sample=8
    )

    assert result["id_is_stable_source_id"] is True
    assert result["recommended_source_id"] == "GetSponsors.Id"
    assert result["sponsor_counts"] == {"total": 3, "persons": 2, "non_person": 1}
    assert result["cross_endpoint"]["stable"] is True
    assert result["cross_biennium"]["stable"] is True
    # only the two named committees were sampled (cap not exceeded)
    assert committee.member_calls == [("Senate", "Rules"), ("House", "Blank")]


async def test_probe_flags_unstable_id_source() -> None:
    sponsor = _FakeSponsorClient(
        {
            "2025-26": [_m(10, "Ann", "Rivers")],
            "2023-24": [_m(999, "Ann", "Rivers")],  # divergent cross-biennium Id
        }
    )
    committee = _FakeCommitteeClient(active=[], members={})
    result = await probe_member_identity(
        sponsor, committee, biennium="2025-26", prior_biennium="2023-24"
    )
    assert result["id_is_stable_source_id"] is False
    assert "name-match" in result["recommended_source_id"]
