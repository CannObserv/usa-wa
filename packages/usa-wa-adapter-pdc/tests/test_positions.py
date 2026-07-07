"""Unit tests for the pure PDC position/seat helpers."""

from __future__ import annotations

import pytest
from usa_wa_adapter_pdc.normalize.positions import (
    PDC_PERSON_ID_SCHEME,
    canonical_position,
    fold_token,
    house_seat_assignment_source_id,
    house_seat_role_source_id,
    pdc_person_identifier_source_id,
    surname_match_set,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", "Position 1"), ("2", "Position 2"), (" 1 ", "Position 1"), (1, "Position 1")],
)
def test_canonical_position_maps_to_qualifier(raw, expected) -> None:
    assert canonical_position(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", None, "0", "3", "abc"])
def test_canonical_position_rejects_non_house_positions(raw) -> None:
    assert canonical_position(raw) is None


def test_house_seat_role_source_id_is_deterministic_per_ld_position() -> None:
    a = house_seat_role_source_id(42, "Position 1")
    assert a == house_seat_role_source_id(42, "Position 1")
    assert a != house_seat_role_source_id(42, "Position 2")
    assert a != house_seat_role_source_id(7, "Position 1")


def test_house_seat_assignment_source_id_is_role_independent() -> None:
    # Keyed on the WSL member id + chamber dimension + biennium (role is a value).
    assert house_seat_assignment_source_id("34024", "2025-26") == "34024:chamber-house:2025-26"


def test_pdc_person_identifier_source_id_scoped_by_scheme() -> None:
    assert pdc_person_identifier_source_id("159") == f"159:{PDC_PERSON_ID_SCHEME}"
    assert PDC_PERSON_ID_SCHEME == "wa_pdc"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Peterson", "peterson"), ("García", "garcia"), (" WILCOX ", "wilcox"), ("O'Brien", "obrien")],
)
def test_fold_token(raw, expected) -> None:
    assert fold_token(raw) == expected


@pytest.mark.parametrize(
    ("filer_name", "wsl_last_name"),
    [
        # Messy PDC filer_name formats — the WSL surname must land among the match keys.
        ("Strom Peterson", "Peterson"),
        ("JACOBSEN CYNTHIA P (Cyndy Jacobsen)", "Jacobsen"),  # LAST FIRST (nick last)
        ("J.T. Wilcox (JT Wilcox)", "Wilcox"),
        ("Drew Hansen (DREW HANSEN)", "Hansen"),
        ("José García", "Garcia"),  # unaccented WSL side still matches
        # Intra-surname hyphen/apostrophe must NOT split the token (real WA members —
        # Ortiz-Self LD21; a bare whole-name split would shred these and never match).
        ("Lillian Ortiz-Self", "Ortiz-Self"),
        ("Mia Su-Ling Gregerson", "Gregerson"),
        ("Danny O'Brien", "O'Brien"),
        ("ORTIZ-SELF, LILLIAN (Lillian Ortiz-Self)", "Ortiz-Self"),  # LAST, FIRST w/ comma
        # Multi-word / particle surnames — WSL joins (fold strips the space) while the PDC
        # name is space-split; the consecutive-join set bridges the two.
        ("Kevin Van De Wege", "Van De Wege"),
        ("Maria De La Cruz", "De La Cruz"),
        ("John St. Clair (Jack St. Clair)", "St. Clair"),
    ],
)
def test_surname_match_set_contains_wsl_surname(filer_name, wsl_last_name) -> None:
    assert fold_token(wsl_last_name) in surname_match_set(filer_name)


def test_surname_match_set_excludes_non_matching_surname() -> None:
    # A concatenation must be *consecutive* — non-adjacent tokens don't join.
    assert fold_token("Peterstrom") not in surname_match_set("Strom Peterson")  # reversed order
    assert fold_token("Barkis") not in surname_match_set("Strom Peterson")
