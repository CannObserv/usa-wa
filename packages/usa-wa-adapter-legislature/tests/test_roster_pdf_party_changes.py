"""Party-change annotation parsing (#228 §4) — pure.

The 22 change annotations in the corrected corpus come in three families, and the
vocabulary is **not** the row vocabulary — ``Silver R`` appears only here, and the prose
family spells the party out in full. An annotation that names a change but fits no family
must refuse with a tally, never guess (#227's rule, one layer up).
"""

from __future__ import annotations

from datetime import date

from usa_wa_adapter_legislature.roster_pdf.party_changes import (
    PartyChange,
    PartyChangeUnparsed,
    parse_party_change,
)


def test_year_and_token_family() -> None:
    change = parse_party_change("(Changed party affiliation, 1913) Prog.")
    assert change == PartyChange(effective_year=1913, effective_date=None, token="Prog.")


def test_year_family_without_comma() -> None:
    change = parse_party_change("(Changed party affiliation 1897) Pop.")
    assert change == PartyChange(effective_year=1897, effective_date=None, token="Pop.")


def test_year_family_with_leader_and_two_word_token() -> None:
    """The Silver Republicans: a dot leader, and a two-word token only this vocabulary
    has — folded to the row vocabulary's ``Silver Rep.`` so #227's one resolver serves
    both."""
    change = parse_party_change("(Changed party affiliation, 1897) .............. Silver R")
    assert change is not None and not isinstance(change, PartyChangeUnparsed)
    assert change.effective_year == 1897
    assert change.token == "Silver Rep."


def test_year_family_survives_the_mangled_foss_row() -> None:
    """Louis Foss, LD22 1893: the opening paren is lost and a stray slash trails the token."""
    change = parse_party_change("Changed party affiliation, 1895) . D /")
    assert change == PartyChange(effective_year=1895, effective_date=None, token="D")


def test_dated_family_carries_the_date_and_no_token() -> None:
    """Peter von Reichbauer, LD30: the new party is not in the annotation — the caller
    infers it from the next listing's row token."""
    change = parse_party_change("Changed party affiliation February 13, 1981")
    assert change == PartyChange(effective_year=1981, effective_date=date(1981, 2, 13), token=None)


def test_prose_family_names_the_party_in_full() -> None:
    change = parse_party_change("Changed party affiliation to Democrat, December 13, 2007")
    assert change is not None and not isinstance(change, PartyChangeUnparsed)
    assert change.effective_date == date(2007, 12, 13)
    assert change.token == "D"


def test_non_change_annotation_returns_none() -> None:
    assert parse_party_change("Resigned Dec. 13, 1973") is None
    assert parse_party_change(None) is None


def test_unrecognizable_change_refuses_rather_than_guessing() -> None:
    """A change clause that fits no family is an explicit refusal — the caller tallies it,
    exactly as the #227 vocabulary treats an unknown row token."""
    result = parse_party_change("Changed party affiliation at some point, allegedly")
    assert isinstance(result, PartyChangeUnparsed)
    assert "allegedly" in result.annotation
