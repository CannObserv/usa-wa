"""Unit tests for the #256 producer-side PM dedup adjudicator.

PM's write path does no name matching at all — ``resolve_entity`` looks up ``(type, value)``
and creates on a miss — so every duplicate this misses becomes an admin merge after the fact.
The roster cohort roughly doubles PM's person table in one pass, which is why the check runs
on our side, before the write.
"""

from __future__ import annotations

import pytest

from usa_wa_sync_powermap.dedupe_roster_persons import (
    DISPOSITION_AMBIGUOUS,
    DISPOSITION_EXACT,
    DISPOSITION_GUARDED,
    DISPOSITION_NEW,
    Candidate,
    adjudicate,
)


def _c(pm_id: str, display_name: str) -> Candidate:
    return Candidate(pm_id=pm_id, display_name=display_name)


def test_an_exact_cleaned_name_is_the_only_automatic_match() -> None:
    """The confirm the sidecar already applies: exact equality on the cleaned name.
    Everything the roster prints around a name — honorific, marital parenthetical, printed
    nickname — is removed on both sides first."""
    result = adjudicate(
        "Belle (Mrs. Frank) Reeves", [_c("P1", "Belle Reeves"), _c("P2", "Joel Reeves")]
    )
    assert result.disposition == DISPOSITION_EXACT
    assert result.pm_id == "P1"


def test_two_exact_matches_are_ambiguous_never_merged() -> None:
    """PM's ``identifiers`` table has no uniqueness on ``(type, value)`` and its
    ``resolve_entity`` returns an arbitrary row on a duplicate — so a two-way tie must
    surface, not pick. #228: never silently merge or drop."""
    result = adjudicate("Belle Reeves", [_c("P1", "Belle Reeves"), _c("P2", "Belle Reeves")])
    assert result.disposition == DISPOSITION_AMBIGUOUS
    assert result.pm_id is None


def test_a_dropped_middle_initial_is_guarded_not_automatic() -> None:
    """``Charles P. Moriarty, Jr`` vs PM's ``Charles Moriarty`` is the shape the exact
    confirm cannot see. The given-name-initial guard (#240) says compatible — but compatible
    is not proof, so it is surfaced for review rather than attached."""
    result = adjudicate("Charles P. Moriarty, Jr", [_c("P1", "Charles Moriarty")])
    assert result.disposition == DISPOSITION_GUARDED
    assert result.pm_id == "P1"


def test_the_guard_rejects_a_different_given_name() -> None:
    """Paul Holmes and Pete Holmes share a surname and nothing else. A surname is not a
    person: 47 of the 96 Persons this cohort already minted share one with somebody in PM."""
    result = adjudicate("Paul Holmes", [_c("P1", "Deborah Holmes")])
    assert result.disposition == DISPOSITION_NEW
    assert result.pm_id is None


def test_the_guard_compares_given_names_only() -> None:
    """The surname's own initial is on both sides by construction — the probe matched on it.
    Counting it would make every same-surname candidate 'compatible' and the guard inert."""
    # Counting the surname's 'h' would make {h} & {n, h} non-empty and admit Nils; only
    # the given names decide, and Harold vs Nils share nothing.
    assert adjudicate("Harold Hansen", [_c("P1", "Nils Hansen")]).disposition == DISPOSITION_NEW


def test_two_compatible_candidates_are_ambiguous() -> None:
    result = adjudicate("C. Moriarty", [_c("P1", "Charles Moriarty"), _c("P2", "Clara Moriarty")])
    assert result.disposition == DISPOSITION_AMBIGUOUS
    assert sorted(result.candidates) == ["Charles Moriarty", "Clara Moriarty"]


def test_a_bare_surname_candidate_is_a_stub_not_a_match() -> None:
    """#240's "a missing given name is no signal" rule does NOT survive contact with PM.

    PM holds bare-surname stub records — a Person whose whole display name is ``Morgan`` —
    and treating "no given name" as "never evidence against" makes one stub compatible with
    every local person sharing that surname. Measured on the live sweep: 70 of 165 guarded
    verdicts pointed at a PM id claimed by 2-6 different local Persons, one ``Morgan`` stub
    absorbing six. A record with no given name cannot corroborate one that has them.
    """
    result = adjudicate("Charles Moriarty", [_c("P1", "Moriarty")])
    assert result.disposition == DISPOSITION_NEW


def test_a_stub_does_not_drown_out_a_real_candidate() -> None:
    """The stub is excluded, not counted — so it cannot turn a lone real candidate into a
    two-way ambiguity and suppress a finding."""
    result = adjudicate("Charles P. Moriarty", [_c("P1", "Moriarty"), _c("P2", "Charles Moriarty")])
    assert result.disposition == DISPOSITION_GUARDED
    assert result.pm_id == "P2"


def test_a_different_surname_in_the_candidate_window_is_ignored() -> None:
    """PM's FTS returns prefix matches on the last token, so a surname probe can bring back
    neighbours (``bone`` → ``Bonebrake``). They are not candidates."""
    result = adjudicate("Homer T. Bone", [_c("P1", "Homer Bonebrake")])
    assert result.disposition == DISPOSITION_NEW


def test_an_empty_candidate_window_is_new() -> None:
    assert adjudicate("Belle Reeves", []).disposition == DISPOSITION_NEW


@pytest.mark.parametrize("name", ["???", "Jr."])
def test_a_name_with_no_surname_never_adjudicates(name) -> None:
    """No surname means no probe was possible; there is nothing to compare against."""
    assert adjudicate(name, [_c("P1", "Belle Reeves")]).disposition == DISPOSITION_NEW
