"""Unit tests for name folding and the token-set surname match (#189).

Cases moved verbatim from `usa-wa-adapter-pdc/tests/test_positions.py` — the matcher was
never PDC-specific.
"""

from __future__ import annotations

import pytest

from usa_wa_common.names import (
    fold_token,
    strip_non_name_parts,
    strip_tenure_notes,
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


# --- strip_tenure_notes (usa-wa#378) ------------------------------------------
#
# Every case below is a real published `name_full` from the 2026-09-10 snapshot.
# The split is the whole point: the roster prints two unrelated things inside
# parentheses, and only one of them is not a name.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Tenure events. Not a name in any sense — a note about the person's
        # service that the roster printed inside the name column.
        ("Geraldine McCormick (Resgnd Dec. 31, 1982)", "Geraldine McCormick"),
        ("D. N. Judson (Apntd Dec. 13 to serve \u201933 Ex. S.)", "D. N. Judson"),
        (
            "A. R. Heilig (Left Seattle July 2, 1900 Named Court Clerk, 3rd Judcl Dvn, AK Terr.)",
            "A. R. Heilig",
        ),
        (
            "James Wickersham (Left Seattle July 2, 1900 Appointed Judge, 3rd Judcl Dvn, AK Terr.)",
            "James Wickersham",
        ),
        # The one with no digit in it — caught on length, which is why the rule
        # cannot be "contains a number".
        (
            "James M. Hogan (Select House Cmte upheld election challenge, "
            "Hogan declared duly elected)",
            "James M. Hogan",
        ),
        # #378's reason for existing: merging this row's entity makes the roster
        # name win survivorship, so without this strip a leave-of-absence note
        # becomes the published legal name of a live, PM-resolved legislator.
        (
            "Myron \u201cMike\u201d Kreidler (On leave of absence for military duty "
            "Jan. 8, 1991 to April 18, 1991)",
            "Myron \u201cMike\u201d Kreidler",
        ),
    ],
)
def test_strip_tenure_notes_removes_an_annotation(raw, expected) -> None:
    assert strip_tenure_notes(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # Marital print forms. Whether SHE should be published under her
        # husband's name is a real editorial question (usa-wa#378 follow-on 2),
        # and it is not this function's to answer — these survive untouched so
        # the decision stays a decision rather than a side effect.
        "Agnes (Mrs. Thomas E.) Kehoe",
        "Belle (Mrs. Frank) Reeves",
        "Frances (Mrs. Thomas A.) Swayze",
        "Margaret (Mrs. Joseph E.) Hurley",
        "Mrs. Irwin LeCocq (Mary)",
        "Mrs. Jurie B.(Nettie Luella) Smith",
        "Mrs. Vincent (Matilda) F. Jones",
        # A legal-name gloss on a nickname — name content both sides of the paren.
        "Jack (John T.) Dootson",
        # Not a person at all: a committee's chamber marker. Organizations go
        # through the same name screen, so the rule has to leave this alone.
        "Law & Justice (H)",
        # Nothing to do.
        "Patty Murray",
        "",
    ],
)
def test_strip_tenure_notes_keeps_name_content(raw) -> None:
    assert strip_tenure_notes(raw) == raw
