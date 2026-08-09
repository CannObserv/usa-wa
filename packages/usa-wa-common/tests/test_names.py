"""Unit tests for name folding and the token-set surname match (#189).

Cases moved verbatim from `usa-wa-adapter-pdc/tests/test_positions.py` — the matcher was
never PDC-specific; both SOS normalizers use it too.
"""

from __future__ import annotations

import pytest

from usa_wa_common.names import fold_token, surname_match_set


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
