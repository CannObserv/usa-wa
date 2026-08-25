"""Unit tests for name folding and the token-set surname match (#189).

Cases moved verbatim from `usa-wa-adapter-pdc/tests/test_positions.py` — the matcher was
never PDC-specific; both SOS normalizers use it too.
"""

from __future__ import annotations

import pytest

from usa_wa_common.names import (
    fold_token,
    probe_surname,
    split_name,
    strip_non_name_parts,
    strip_other_party_parts,
    surname_match_set,
)


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


# ---------------------------------------------------------------------------
# usa-wa#256 — the shared "what counts as a name" rule and the search probe


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Parentheticals: marital forms and leaked roster annotations alike.
        ("Belle (Mrs. Frank) Reeves", "Belle Reeves"),
        ("Margaret (Mrs. Joseph E.) Hurley", "Margaret Hurley"),
        # Quoted nicknames, straight and curly.
        ('Frank "Buster" Brouillet', "Frank Brouillet"),
        ("W. H. “Bill” Garson", "W. H. Garson"),
        # Bare honorific tokens — the branch no other test exercises, and the one that
        # decides whether ``Dr. A. C. Wingrove`` can ever confirm against PM's curated
        # ``A. C. Wingrove``.
        ("Dr. A. C. Wingrove", "A. C. Wingrove"),
        ("Mrs. Eva Anderson", "Eva Anderson"),
        ("Rev. John Doe", "John Doe"),
        ("Hon. Jane Roe", "Jane Roe"),
        # Generational suffixes are NOT honorifics: they distinguish two real people
        # (usa-wa#228's Bill Day / Bill Day Jr), so they survive intact.
        ("Kemper Freeman, Jr.", "Kemper Freeman, Jr."),
        ("Charles D. Ulmer, Sr", "Charles D. Ulmer, Sr"),
    ],
)
def test_strip_non_name_parts(raw, expected) -> None:
    assert strip_non_name_parts(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A parenthetical marital form names the HUSBAND — those tokens are not hers, and an
        # identity guard that keeps them lets him pass as her (usa-wa#277).
        ("Frances (Mrs. Thomas A.) Swayze", "Frances Swayze"),
        ("Belle (Mrs. Frank) Reeves", "Belle Reeves"),
        # A quoted nickname is this person's OWN other name and SURVIVES here — the one place
        # this differs from strip_non_name_parts. WSL records Bob McCaslin Jr's FirstName as
        # "Bob", so dropping it removes the only token the two sides share.
        ("Robert \u201cBob\u201d McCaslin,", "Robert \u201cBob\u201d McCaslin,"),
        ('Frank "Buster" Brouillet', 'Frank "Buster" Brouillet'),
        # Honorifics name nobody, so they go either way.
        ("Dr. A. C. Wingrove", "A. C. Wingrove"),
        # Generational suffixes distinguish two real people; they survive, as above.
        ("Kemper Freeman, Jr.", "Kemper Freeman, Jr."),
    ],
)
def test_strip_other_party_parts(raw, expected) -> None:
    assert strip_other_party_parts(raw) == expected


def test_the_two_strips_differ_only_on_the_quoted_nickname() -> None:
    """The distinction the roster resolver depends on, pinned as a contrast so a future edit
    cannot quietly collapse the two functions back together."""
    row = "Frances (Mrs. Thomas A.) \u201cFran\u201d Swayze"
    assert strip_non_name_parts(row) == "Frances Swayze"
    assert strip_other_party_parts(row) == "Frances \u201cFran\u201d Swayze"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jay Inslee", "inslee"),
        ("Belle (Mrs. Frank) Reeves", "reeves"),  # the parenthetical is not the surname
        ("Dr. A. C. Wingrove", "wingrove"),  # nor is the honorific
        # The suffix is part of the person but never the search key: probing ``jr``
        # searches every PM name carrying that token instead of the surname's cohort.
        ("Kemper Freeman, Jr.", "freeman"),
        ("Charles D. Ulmer, Sr", "ulmer"),
        ("Albert C. Thompson, Jr.", "thompson"),
        ("Homer T. Bone III", "bone"),
        # Nothing to probe with — an empty ``q`` would match on PM's ranking alone.
        ("???", None),
        ("Jr.", None),
        ("", None),
    ],
)
def test_probe_surname(raw, expected) -> None:
    assert probe_surname(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jay Inslee", (["jay"], "inslee")),
        ("Belle (Mrs. Frank) Reeves", (["belle"], "reeves")),
        ("Dr. A. C. Wingrove", (["a", "c"], "wingrove")),
        # The suffix is neither given name nor surname: it falls out with the surname.
        ("Kemper Freeman, Jr.", (["kemper"], "freeman")),
        ("Charles P. Moriarty, Jr", (["charles", "p"], "moriarty")),
        # A bare surname has no given tokens — the shape PM's stub records take.
        ("Moriarty", ([], "moriarty")),
        ("???", None),
        ("Jr.", None),
    ],
)
def test_split_name(raw, expected) -> None:
    """One definition of where the surname sits.

    Three call sites were re-deriving `len(tokens) - 1 - tokens[::-1].index(surname)`
    independently (usa-wa#226 CR): the roster identity seating index and the PM dedup
    adjudicator. A divergence there mismatches people silently rather than erroring, which is
    the failure this module exists to prevent.
    """
    assert split_name(raw) == expected
