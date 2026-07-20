"""Sponsor-wire hygiene (#105) — committee-corroborated stale-row exclusion.

WSL's ``GetSponsors`` keeps fully-named rows for members who departed years ago (Kilduff left
Dec 2020, still named in every wire through 2025-26; Senn resigned Jan 2025; Nguyen resigned
Jan 2025 — a Senate instance). The wire itself carries no discriminator, but the committee
rosters do: a departed member drops off every committee roster at the departure boundary while
every sitting member (including fresh appointees) is committee-active. These tests pin the
exclusion rule and its coverage guardrail (a thin/partial committee archive must not read as
mass departure — the #44/#56 floor pattern).
"""

from __future__ import annotations

import logging

from usa_wa_adapter_legislature.roster_hygiene import (
    STALE_MIN_COVERAGE_DEFAULT,
    committee_member_ids_by_biennium,
    stale_exclusions_by_biennium,
    stale_member_ids,
)


def _member(mid, last="Rivers", agency="House"):
    return {
        "Id": mid,
        "FirstName": "Ann",
        "LastName": last,
        "District": "5",
        "Party": "D",
        "Agency": agency,
        "Name": f"Ann {last}",
    }


def _stub(mid):
    """A name-blanked departed-member stub (#81) — not a person, never counted."""
    return {"Id": mid, "FirstName": None, "LastName": None, "Agency": "House", "Name": None}


# --- committee_member_ids_by_biennium ------------------------------------------------------


def test_committee_ids_grouped_by_biennium_and_stringified():
    rosters = {
        ("2025-26", "31631"): [{"Id": 100}, {"Id": 200}],
        ("2025-26", "31632"): [{"Id": 200}, {"Id": 300}],
        ("2023-24", "31631"): [{"Id": 400}],
    }
    ids = committee_member_ids_by_biennium(rosters)
    assert ids == {"2025-26": {"100", "200", "300"}, "2023-24": {"400"}}


def test_committee_ids_skips_idless_rows():
    assert committee_member_ids_by_biennium({("2025-26", "1"): [{"Name": "x"}]}) == {
        "2025-26": set()
    }


# --- stale_member_ids ----------------------------------------------------------------------


def test_stale_member_absent_from_committees_is_excluded(caplog):
    """Named member off every committee roster (Kilduff/Senn/Nguyen) → stale; the sitting
    members (on committees) are not. Exclusion is logged for the operator audit trail."""
    members = [_member(100), _member(200, last="Kilduff"), _member(300, agency="Senate")]
    with caplog.at_level(logging.INFO):
        stale = stale_member_ids(members, {"100", "300"}, biennium="2025-26", min_coverage=0.5)
    assert stale == {"200"}
    assert any(r.message == "sponsor_stale_row_excluded" for r in caplog.records)


def test_stale_senate_member_is_excluded_too():
    """The Nguyen case: the stale-row class spans both chambers."""
    members = [_member(100, agency="Senate"), _member(200, last="Nguyen", agency="Senate")]
    assert stale_member_ids(members, {"100"}, biennium="2025-26", min_coverage=0.5) == {"200"}


def test_low_coverage_skips_exclusion_entirely(caplog):
    """Guardrail: committee cohort covering < min_coverage of the named rows (1999-00's 31/148
    archive-floor artifact; a partial harvest) → no exclusions at all, loudly."""
    members = [_member(100), _member(200), _member(300)]
    with caplog.at_level(logging.WARNING):
        stale = stale_member_ids(members, {"100"}, biennium="1999-00")
    assert stale == set()
    assert any(r.message == "stale_exclusion_skipped_low_coverage" for r in caplog.records)


def test_empty_committee_cohort_skips_exclusion():
    """Pre-1999-00 (no committee archive at all) → coverage 0 → skip, not mass exclusion."""
    assert stale_member_ids([_member(100)], set(), biennium="1997-98") == set()


def test_no_named_members_is_a_noop():
    assert stale_member_ids([_stub(100)], {"100"}, biennium="2025-26") == set()


def test_blanked_stubs_do_not_count_toward_coverage():
    """Stubs are not people: 1 named member, committee-active, plus stubs → coverage 1.0,
    no exclusion (stubs must not drag coverage under the floor)."""
    members = [_member(100), _stub(200), _stub(300)]
    assert stale_member_ids(members, {"100"}, biennium="2025-26") == set()


def test_full_coverage_boundary_is_not_skipped():
    """Coverage exactly at the floor still runs: 9 of 10 on committees (0.9 = default)."""
    members = [_member(i) for i in range(100, 110)]
    active = {str(i) for i in range(100, 109)}  # 9/10
    assert STALE_MIN_COVERAGE_DEFAULT == 0.9
    assert stale_member_ids(members, active, biennium="2025-26") == {"109"}


# --- stale_exclusions_by_biennium (the tail rule) ------------------------------------------


def test_tail_rule_rescues_archive_gap_member():
    """The Shewmake case: a sitting member missing from her House-era committee rosters (a
    WSL archive gap) but committee-present in a LATER biennium is NOT stale — a genuine
    departure is terminal. Only tail-absence excludes, so an exclusion can never punch a
    mid-tenure hole (no span splits, no superseded rows)."""
    members = {
        "2019-20": [_member(100), _member(200, last="Shewmake")],
        "2021-22": [_member(100), _member(200, last="Shewmake")],
        "2023-24": [_member(100), _member(200, last="Shewmake")],
    }
    committee_ids = {
        "2019-20": {"100"},
        "2021-22": {"100"},
        "2023-24": {"100", "200"},  # present later → the earlier absences are archive gaps
    }
    exclusions = stale_exclusions_by_biennium(members, committee_ids, min_coverage=0.5)
    assert exclusions == {"2019-20": set(), "2021-22": set(), "2023-24": set()}


def test_tail_rule_keeps_terminal_ghosts_excluded():
    """The Kilduff case: named in every wire after departure, committee-absent in the stale
    bienniums AND all later ones → excluded there; the genuinely-served bienniums keep her."""
    members = {
        "2019-20": [_member(100), _member(900, last="Kilduff")],
        "2021-22": [_member(100), _member(900, last="Kilduff")],
        "2023-24": [_member(100), _member(900, last="Kilduff")],
    }
    committee_ids = {
        "2019-20": {"100", "900"},
        "2021-22": {"100"},
        "2023-24": {"100"},
    }
    exclusions = stale_exclusions_by_biennium(members, committee_ids, min_coverage=0.5)
    assert exclusions == {"2019-20": set(), "2021-22": {"900"}, "2023-24": {"900"}}


def test_tail_rule_excludes_never_present_ghost():
    """A row whose member has NO committee presence in any biennium (Roulstone/'Marlo Braun'
    pure ghosts) is excluded wherever coverage permits."""
    members = {"2013-14": [_member(100), _member(500, last="Roulstone")]}
    exclusions = stale_exclusions_by_biennium(members, {"2013-14": {"100"}}, min_coverage=0.5)
    assert exclusions == {"2013-14": {"500"}}


def test_tail_rule_respects_per_biennium_coverage_floor():
    """A thin-coverage biennium contributes no exclusions even when the tail rule fires."""
    members = {
        "1999-00": [_member(100), _member(200), _member(300)],
        "2001-02": [_member(100), _member(200), _member(300)],
    }
    committee_ids = {"1999-00": {"100"}, "2001-02": {"100", "200"}}
    exclusions = stale_exclusions_by_biennium(members, committee_ids, min_coverage=0.6)
    assert exclusions["1999-00"] == set()  # 1/3 coverage < 0.6 → skipped
    assert exclusions["2001-02"] == {"300"}  # 2/3 ≥ 0.6, 300 never committee-present
