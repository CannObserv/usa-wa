"""Roster annotations → dated clauses (#226, epic #219 Phase 2). Pure, no writes.

The roster is the only source that dates a succession. :func:`parse_annotation` splits a
record's annotation into clauses, each carrying its verb and the most precise date it actually
states; the pre-1991 projector reads those clauses to refine a successor's coverage at biennium
grain.

Two rules the corpus forces, each of which is a refusal rather than a guess:

* **State the precision, never round it.** ``Deceased July 1977`` and ``Elected in 1922`` are
  real annotations. Rounding either to a day invents a boundary the source never asserted, so
  only a ``day``-precision :class:`ParsedDate` carries a ``value``; coarser ones carry a
  ``window`` instead (#360).
* **A session reference is not a date.** ``Appointed to serve 1951 2nd Ex. S.`` and ``Holdover
  from District 21, 1901 Session`` name legislative sessions. Reading those years as dates would
  attach an event to the wrong thing entirely.

The proposal layer that turned these clauses into operator events (``propose_events``,
``proposals_for_seat`` and the ``roster_pdf.resolve`` resolver) fed the Postgres succession
backfill; both were deleted in #471 once #412 PR F retired that backfill.
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date

#: Clause verbs the corpus uses, mapped from their leading text.
_VERBS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deceased", re.compile(r"^\(?\s*(?:deceased|died)\b", re.I)),
    ("sworn_in", re.compile(r"^\(?\s*sworn\s+in\b", re.I)),
    ("appointed", re.compile(r"^\(?\s*appointed\b", re.I)),
    ("resigned", re.compile(r"^\(?\s*resigned\b", re.I)),
    ("elected", re.compile(r"^\(?\s*elected\b", re.I)),
    ("redistricted", re.compile(r"^\(?\s*redistricted\b", re.I)),
    ("holdover", re.compile(r"^\(?\s*holdover\b", re.I)),
    ("changed_party", re.compile(r"^\(?\s*changed\s+party\b", re.I)),
)

#: Phrases whose years name a *session*, not a date. Stripped before date extraction so
#: ``to serve 1951 2nd Ex. S.`` cannot be read as an appointment in 1951.
_SESSION_NOISE = re.compile(
    r"to\s+serve\s+(?:the\s+)?\d{4}[^;]*|"
    r"\b\d{4}\s+(?:\d(?:st|nd|rd|th)\s+)?ex\.?\s*s\.?|"
    r"\b\d{4}\s+session\b|"
    r"from\s+district\s+\d+[^;]*",
    re.I,
)

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
)

#: ``Nov. 12, 1980`` / ``December 6., 2024`` / ``February 4. 2013`` — the source's punctuation is
#: inconsistent, including a stray period where a comma belongs.
_DAY_DATE = re.compile(rf"\b({_MONTHS})\.?\s+(\d{{1,2}})\s*[.,]?\s*,?\s*(\d{{4}})\b", re.I)
_MONTH_DATE = re.compile(rf"\b({_MONTHS})\.?\s*,?\s+(\d{{4}})\b", re.I)
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

_MONTH_NUMBER = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

#: ``Appointed to temporarily serve from July 18, 2017 until July 23, 2017`` — a single clause


@dataclass(frozen=True)
class ParsedDate:
    """A date and how precisely the source stated it.

    ``precision`` is ``day``/``month``/``year``/``none``; only ``day`` carries a
    ``value`` (the boundary ``projector`` reads), and widening that would silently
    start seating people on the 1st of a month the clerk never wrote.

    ``window`` is the smallest range the source actually bounds the event to —
    ``(the 13th, the 13th)`` for a day, the whole of October for "Oct. 1971",
    the whole year for a bare year. **A window exists exactly when a credible
    date was stated**: ``window is not None`` iff ``precision != "none"``, so a
    consumer may branch on either and get the same answer (CR 126). A year
    outside :data:`_YEAR_FLOOR`/:data:`_YEAR_CEILING` states no credible date
    and reads as ``none`` — the `_YEAR` pattern admits 1800-1888 and 2050-2099,
    which the day and month branches already refuse, and Washington was not a
    state before 1889 (CR 125). It
    exists because a month-only date is not *no* date (#360): the parser used to
    keep the precision label and throw the month away, so "Appointed Oct. 1971"
    reached the span builder as nothing and the successor fell back to their
    biennium floor — overlapping a predecessor whose own exit the roster dated
    precisely. A window lets a consumer say "certainly after" without inventing
    a day.
    """

    value: date | None
    precision: str
    raw: str
    window: tuple[date, date] | None = None


@dataclass(frozen=True)
class Clause:
    """One semicolon-delimited clause of an annotation."""

    verb: str | None
    parsed: ParsedDate
    text: str


def _month_number(token: str) -> int | None:
    return _MONTH_NUMBER.get(token.lower().rstrip(".")[:4]) or _MONTH_NUMBER.get(
        token.lower().rstrip(".")[:3]
    )


#: Years a roster annotation can plausibly state: statehood to a generous margin past the
#: newest edition. Outside this window the "date" is a typo — the corpus contains one, LD9
#: 1921's "special election January 7, 2921" — and a millennium-off day-precision date would
#: silently shape #226 boundaries and #228 coverage (CR #75). Out-of-window falls through to
#: coarser precision, where the year-only shape carries no value and is reported as undated.
_YEAR_FLOOR, _YEAR_CEILING = 1889, 2049


def _month_window(year: int, month: int) -> tuple[date, date]:
    """First and last day of a month — leap years included, via the calendar."""
    return (date(year, month, 1), date(year, month, monthrange(year, month)[1]))


def _parse_date(text: str) -> ParsedDate:
    """Extract the most precise date the clause actually states. Session references stripped."""
    cleaned = _SESSION_NOISE.sub(" ", text)
    match = _DAY_DATE.search(cleaned)
    if match:
        month = _month_number(match.group(1))
        if month and _YEAR_FLOOR <= int(match.group(3)) <= _YEAR_CEILING:
            year = int(match.group(3))
            try:
                exact = date(year, month, int(match.group(2)))
            except ValueError:
                # An impossible day (Feb 31) is not a date — but the MONTH it
                # names still bounds the event, and falling through to `_YEAR`
                # would throw that away with the day.
                return ParsedDate(None, "month", match.group(0), _month_window(year, month))
            return ParsedDate(exact, "day", match.group(0), (exact, exact))
    match = _MONTH_DATE.search(cleaned)
    if match:
        month = _month_number(match.group(1))
        if month:
            # group(2) is the year the pattern already captured (CR 127) —
            # re-parsing text this regex just matched invites the two to drift.
            year = int(match.group(2))
            if _YEAR_FLOOR <= year <= _YEAR_CEILING:
                return ParsedDate(None, "month", match.group(0), _month_window(year, month))
    match = _YEAR.search(cleaned)
    if match:
        year = int(match.group(1))
        if _YEAR_FLOOR <= year <= _YEAR_CEILING:
            window = (date(year, 1, 1), date(year, 12, 31))
            return ParsedDate(None, "year", match.group(0), window)
    return ParsedDate(None, "none", "")


def parse_annotation(annotation: str) -> tuple[Clause, ...]:
    """Split an annotation into clauses, each with its verb and the date it actually states.

    Clauses are separated by a semicolon, or by a closing parenthesis immediately followed by
    more text — the source nests whole annotations in brackets (``(Resigned …) (Appointed …)``).
    The rule is deliberately broader than "semicolon only"; no corpus case currently splits
    mid-sentence, but a parenthetical aside followed by prose would (CR finding 15).
    """
    clauses: list[Clause] = []
    for raw in re.split(r"[;)]\s*(?=[A-Za-z(])|;", annotation):
        text = raw.strip().strip("()").strip()
        if not text:
            continue
        verb = next((name for name, pattern in _VERBS if pattern.search(text)), None)
        clauses.append(Clause(verb=verb, parsed=_parse_date(text), text=text))
    return tuple(clauses)
