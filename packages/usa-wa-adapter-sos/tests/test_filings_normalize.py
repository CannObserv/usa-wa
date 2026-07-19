"""Pure SOS filing → House-position lookup tests (#100).

Unit tests pin the parsing + within-LD position resolution; one archive-backed test locks the
projector to the *real* votewa CSV shape (via the recorded 2016 cassette wire), the analog of
the transport round-trip.
"""

from __future__ import annotations

import pytest
from usa_wa_adapter_pdc.normalize.positions import fold_token, surname_match_set
from usa_wa_adapter_sos.filings.normalize import (
    HouseFiling,
    build_house_filings,
    filing_ld,
    house_position_qualifier,
    position_for,
    sos_party_slug,
)
from usa_wa_adapter_sos.filings.transport import SOSFilingsClient


def _row(race, ld, ballot, party="(Prefers Democratic Party)"):
    return {
        "RaceName": race,
        "RaceJurisdictionName": f"Legislative District {ld}",
        "BallotName": ballot,
        "PartyName": party,
    }


def test_house_position_qualifier_maps_pos_digit() -> None:
    assert house_position_qualifier("State Representative Pos. 1") == "Position 1"
    assert house_position_qualifier("State Representative Pos. 2") == "Position 2"
    assert house_position_qualifier("State Senator") is None
    assert house_position_qualifier("Justice of the Supreme Court Pos. 1") is None


def test_filing_ld_parses_district() -> None:
    assert filing_ld("Legislative District 15") == 15
    assert filing_ld("Congressional District 3") is None
    assert filing_ld("") is None


def test_sos_party_slug_extracts_from_prefers_form() -> None:
    assert sos_party_slug("(Prefers Republican Party)") == "republican"
    assert sos_party_slug("(Prefers Democratic Party)") == "democratic"
    assert sos_party_slug("(Prefers Fifth Republic Party)") is None
    assert sos_party_slug("") is None


def test_build_house_filings_groups_by_ld_skips_non_house() -> None:
    rows = [
        _row("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Republican Party)"),
        _row("State Representative Pos. 2", 5, "Andrew Barkis", "(Prefers Republican Party)"),
        _row("State Senator", 5, "Some Senator"),  # skipped — not a House race
    ]
    filings = build_house_filings(rows)
    assert set(filings) == {5}
    assert {f.qualifier for f in filings[5]} == {"Position 1", "Position 2"}


def test_position_for_unique_surname() -> None:
    filings = build_house_filings(
        [
            _row("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Republican Party)"),
            _row("State Representative Pos. 2", 5, "Andrew Barkis", "(Prefers Republican Party)"),
        ]
    )
    assert position_for(filings, 5, fold_token("Rivers"), "republican") == "Position 1"
    assert position_for(filings, 5, fold_token("Barkis"), "republican") == "Position 2"


def test_position_for_shared_surname_broken_by_party() -> None:
    filings = build_house_filings(
        [
            _row("State Representative Pos. 1", 5, "Ann Smith", "(Prefers Democratic Party)"),
            _row("State Representative Pos. 2", 5, "Bob Smith", "(Prefers Republican Party)"),
        ]
    )
    assert position_for(filings, 5, fold_token("Smith"), "republican") == "Position 2"
    # Same surname, no party disambiguation → ambiguous, never guessed.
    assert position_for(filings, 5, fold_token("Smith"), None) is None


def test_position_for_no_hit_returns_none() -> None:
    filings = build_house_filings([_row("State Representative Pos. 1", 5, "Ann Rivers")])
    assert position_for(filings, 5, fold_token("Nobody"), "democratic") is None
    assert position_for(filings, 99, fold_token("Rivers"), "democratic") is None


def test_multiword_surname_matches_via_fold_set() -> None:
    # WSL folds "Van De Wege" → "vandewege"; the SOS ballot name space-splits, so the fold set
    # must contain the joined token (reusing surname_match_set).
    filings = build_house_filings([_row("State Representative Pos. 1", 24, "Kevin Van De Wege")])
    assert "vandewege" in surname_match_set("Kevin Van De Wege")
    assert position_for(filings, 24, fold_token("Van De Wege"), "democratic") == "Position 1"


@pytest.mark.asyncio
async def test_real_2016_cohort_resolves_a_known_seat(sos_vcr) -> None:
    """Lock the projector to the real votewa CSV shape: parse the recorded 2016 cohort and
    resolve a known LD-15 seat (Bruce Chandler, Pos. 1)."""
    with sos_vcr.use_cassette("whofiled_2016.yaml"):
        fetch = await SOSFilingsClient().fetch_whofiled(2016)
    filings = build_house_filings(fetch.records)
    assert filings, "expected House filings parsed from the real cohort"
    assert position_for(filings, 15, fold_token("Chandler"), "republican") == "Position 1"


def test_house_filing_is_hashable_frozen() -> None:
    f = HouseFiling(
        qualifier="Position 1", name_keys=frozenset({"rivers"}), party_slug="republican"
    )
    assert f in {f}
