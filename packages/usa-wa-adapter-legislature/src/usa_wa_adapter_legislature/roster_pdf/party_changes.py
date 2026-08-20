"""Party-change annotation parsing (#228 §4). Pure.

**Party is not a per-record constant.** A pre-1991 Senate term is listed once, so a mid-term
change has nowhere to appear except the annotation on the member's term-start row; House
changes annotate to record an exact date a biennial re-listing cannot express. The corrected
corpus carries 22 such annotations in three families:

* **year + token** (18): ``(Changed party affiliation, 1913) Prog.`` — sometimes behind a
  dot leader, once with the opening parenthesis lost and a stray slash (Louis Foss, 1893).
  The token vocabulary is **not** the row vocabulary: ``Silver R`` appears only here, where
  the row column prints ``Silver Rep.``
* **dated, party unstated** (3): ``Changed party affiliation February 13, 1981`` — the new
  affiliation is whatever the member's *next listing* shows; the caller owns that inference
  because only it can see the next row.
* **prose** (1): ``Changed party affiliation to Democrat, December 13, 2007`` — the party
  spelled out in full.

A change clause that fits no family returns :class:`PartyChangeUnparsed` for the caller to
tally — refuse, never guess, the same contract ``resolve_party_token`` enforces one layer
down (#227).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: Annotation-only abbreviations, folded to the row vocabulary ``PARTY_TOKENS`` declares so
#: one resolver (#227's) serves both.
_ANNOTATION_TOKEN_FOLD = {
    "silver r": "Silver Rep.",
}

#: Full party names the prose family spells out, folded to row tokens.
_PARTY_NAME_FOLD = {
    "democrat": "D",
    "republican": "R",
}

_MONTHS = {
    name: index
    for index, name in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}

_CHANGE_CUE = re.compile(r"changed party", re.IGNORECASE)

#: ``Changed party affiliation[,] 1913) [leader] TOKEN`` — the year sits inside the (possibly
#: mangled) parenthetical; the tail is cleaned in Python because the token's own trailing
#: period (``Prog.``) must survive while a dot leader and stray punctuation must not.
_YEAR_FAMILY = re.compile(
    r"Changed party affiliation,?\s+(?P<year>1[89]\d{2}|20\d{2})\s*\)(?P<tail>.*)$",
    re.IGNORECASE,
)

#: What a cleaned year-family tail must look like to be a token at all.
_TOKEN_SHAPE = re.compile(r"[A-Za-z][A-Za-z. ]*")

#: ``Changed party affiliation [to Democrat,] February 13, 1981``.
_DATED_FAMILY = re.compile(
    r"Changed party affiliation\s+(?:to\s+(?P<party>[A-Za-z]+),?\s+)?"
    r"(?P<month>[A-Za-z]+)\.?\s+(?P<day>\d{1,2}),\s+(?P<year>1[89]\d{2}|20\d{2})\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PartyChange:
    """One parsed change: when it took effect, and into what (when the source says)."""

    #: The session year the new affiliation begins — for the dated family, the change
    #: date's own year; the biennium quantizer owns the year→biennium mapping (spec §5).
    effective_year: int
    #: Exact date, when the dated or prose family supplies one.
    effective_date: date | None
    #: The new affiliation as a row-vocabulary token, or ``None`` when the annotation does
    #: not state it (the dated family) — the caller infers from the member's next listing.
    token: str | None


@dataclass(frozen=True)
class PartyChangeUnparsed:
    """A change clause no family matches — refused for the caller to tally."""

    annotation: str


def _fold_token(raw: str) -> str:
    folded = " ".join(raw.split())
    return _ANNOTATION_TOKEN_FOLD.get(folded.lower(), folded)


def parse_party_change(
    annotation: str | None,
) -> PartyChange | PartyChangeUnparsed | None:
    """Parse the change clause in ``annotation``, if one is present.

    ``None`` means no change clause; :class:`PartyChangeUnparsed` means a clause is present
    but unrecognizable — count it, never guess at it.
    """
    if not annotation or not _CHANGE_CUE.search(annotation):
        return None

    match = _YEAR_FAMILY.search(annotation)
    if match is not None:
        tail = match.group("tail").strip().strip("/").strip()
        tail = re.sub(r"^\.{2,}\s*", "", tail)  # a dot leader before the token
        tail = re.sub(r"^\.\s+", "", tail)  # the stray ``. `` the Foss row carries
        if tail and _TOKEN_SHAPE.fullmatch(tail):
            return PartyChange(
                effective_year=int(match.group("year")),
                effective_date=None,
                token=_fold_token(tail),
            )
        return PartyChangeUnparsed(annotation=annotation)

    match = _DATED_FAMILY.search(annotation)
    if match is not None:
        month = _MONTHS.get(match.group("month").lower())
        if month is not None:
            effective = date(int(match.group("year")), month, int(match.group("day")))
            party = match.group("party")
            token = _PARTY_NAME_FOLD.get(party.lower()) if party else None
            if party is None or token is not None:
                return PartyChange(
                    effective_year=effective.year,
                    effective_date=effective,
                    token=token,
                )

    return PartyChangeUnparsed(annotation=annotation)
