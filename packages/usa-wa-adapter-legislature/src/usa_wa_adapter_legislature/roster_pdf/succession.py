"""Roster annotations → operator-event **proposals** (#226, epic #219 Phase 2). Pure, no writes.

The roster is the only source that dates a succession. Tenure spans are biennium-quantized, so
every mid-term boundary we hold is currently the biennium floor — LD2 Position 1 has four
consecutive boundaries and all four are wrong. The #107 operator store already knows how to
correct that; it has simply never had a source. This module is that source's read side.

**Proposals, not rows.** Nothing here touches the database. A proposal carries everything an
:class:`~clearinghouse_domain_legislative.operator_events.OperatorEvent` needs *except* the
member id, which requires resolution against Persons. That deliberately splits the risky half
(what should we assert?) from the writing half, so the emission can be inspected before anything
moves a span.

Three rules the corpus forces, each of which is a refusal rather than a guess:

* **Day precision or nothing.** ``Deceased July 1977`` and ``Elected in 1922`` are real
  annotations. Rounding either to a day invents a boundary the source never asserted, and the
  whole point of this backfill is that our existing boundaries are invented.
* **A session reference is not a date.** ``Appointed to serve 1951 2nd Ex. S.`` and ``Holdover
  from District 21, 1901 Session`` name legislative sessions. Reading those years as dates would
  attach an event to the wrong thing entirely.
* **A House seat cannot be named yet.** Seat-scoped events key on ``ld-{n}-position-{p}`` and the
  roster carries no Position (#229 supplies it). Person-scoped ``departed`` needs no seat, so
  deaths and full resignations are proposable in both chambers today; House ``seated``/``vacated``
  defer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord

#: Deferral reasons — why an annotation yielded no proposal. Report-don't-drop.
DEFER_NO_DAY_PRECISION = "no_day_precision"
DEFER_HOUSE_SEAT_UNRESOLVED = "house_seat_unresolved"
DEFER_NO_SUCCESSION_VERB = "no_succession_verb"
DEFER_AMBIGUOUS = "ambiguous"

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

#: A clause naming the *other* chamber marks a move rather than a departure.
_MOVE = re.compile(
    r"appointed\s+to\s+the\s+(senate|house)|elected\s+to\s+the\s+(senate|house)", re.I
)


@dataclass(frozen=True)
class ParsedDate:
    """A date and how precisely the source stated it. ``precision`` is ``day``/``month``/
    ``year``/``none``; only ``day`` is ever emitted as an effective date."""

    value: date | None
    precision: str
    raw: str


@dataclass(frozen=True)
class Clause:
    """One semicolon-delimited clause of an annotation."""

    verb: str | None
    parsed: ParsedDate
    text: str


@dataclass(frozen=True)
class EventProposal:
    """What an operator event *would* be. Carries no member id — resolution is the write half."""

    member_name: str
    district: int
    chamber: str
    session_year: int
    kind: str
    reason: str
    effective_date: date
    seat_kind: str | None
    seat_discriminator: str | None
    evidence: str


@dataclass(frozen=True)
class Deferred:
    """An annotation that yielded no proposal, and why."""

    member_name: str
    district: int
    chamber: str
    session_year: int
    reason: str
    evidence: str


@dataclass(frozen=True)
class SuccessionReport:
    """Proposals plus deferrals. Every annotation lands in exactly one of the two."""

    proposals: tuple[EventProposal, ...]
    deferred: tuple[Deferred, ...]


def _month_number(token: str) -> int | None:
    return _MONTH_NUMBER.get(token.lower().rstrip(".")[:4]) or _MONTH_NUMBER.get(
        token.lower().rstrip(".")[:3]
    )


def _parse_date(text: str) -> ParsedDate:
    """Extract the most precise date the clause actually states. Session references stripped."""
    cleaned = _SESSION_NOISE.sub(" ", text)
    match = _DAY_DATE.search(cleaned)
    if match:
        month = _month_number(match.group(1))
        if month:
            try:
                return ParsedDate(
                    date(int(match.group(3)), month, int(match.group(2))), "day", match.group(0)
                )
            except ValueError:
                pass  # an impossible day (Feb 31) is not a date; fall through to coarser
    match = _MONTH_DATE.search(cleaned)
    if match:
        month = _month_number(match.group(1))
        if month:
            return ParsedDate(None, "month", match.group(0))
    match = _YEAR.search(cleaned)
    if match:
        return ParsedDate(None, "year", match.group(0))
    return ParsedDate(None, "none", "")


def parse_annotation(annotation: str) -> tuple[Clause, ...]:
    """Split an annotation into clauses, each with its verb and the date it actually states."""
    clauses: list[Clause] = []
    for raw in re.split(r"[;)]\s*(?=[A-Za-z(])|;", annotation):
        text = raw.strip().strip("()").strip()
        if not text:
            continue
        verb = next((name for name, pattern in _VERBS if pattern.search(text)), None)
        clauses.append(Clause(verb=verb, parsed=_parse_date(text), text=text))
    return tuple(clauses)


class _Missing:
    """Sentinel: the clause is absent, as distinct from present-but-undated."""


_MISSING = _Missing()


def _day_value(clauses: Sequence[Clause], verb: str) -> date | None | _Missing:
    """The day-precision date of the first ``verb`` clause.

    Three outcomes, and the caller must tell them apart: :data:`_MISSING` (no such clause, try
    the next rule), ``None`` (the clause exists but states no day, so defer rather than round),
    or the date.
    """
    clause = next((c for c in clauses if c.verb == verb), None)
    if clause is None:
        return _MISSING
    return clause.parsed.value if clause.parsed.precision == "day" else None


def _seat(record: RosterRecord) -> tuple[str, str] | None:
    """The ``(seat_kind, seat_discriminator)`` for a seat-scoped event, or ``None`` when the
    roster cannot name it — the House Position case, which #229 resolves."""
    if record.chamber == "senate":
        return "chamber-senate", str(record.district)
    return None


def _proposal(
    record: RosterRecord, kind: str, reason: str, effective: date, evidence: str
) -> EventProposal | Deferred:
    seat_kind = seat_discriminator = None
    if kind in ("seated", "vacated"):
        seat = _seat(record)
        if seat is None:
            return Deferred(
                member_name=record.name,
                district=record.district,
                chamber=record.chamber,
                session_year=record.year,
                reason=DEFER_HOUSE_SEAT_UNRESOLVED,
                evidence=evidence,
            )
        seat_kind, seat_discriminator = seat
    return EventProposal(
        member_name=record.name,
        district=record.district,
        chamber=record.chamber,
        session_year=record.year,
        kind=kind,
        reason=reason,
        effective_date=effective,
        seat_kind=seat_kind,
        seat_discriminator=seat_discriminator,
        evidence=evidence,
    )


def _propose_one(record: RosterRecord) -> EventProposal | Deferred:
    annotation = record.annotation or ""
    clauses = parse_annotation(annotation)
    moved = bool(_MOVE.search(annotation))
    # ``Appointed to the Senate`` names a move *destination*, not a dated seating. Left in the
    # seating candidates it wins the branch and then defers for having no date, silently losing
    # the resignation clause that actually carries one.
    seating_clauses = [c for c in clauses if not _MOVE.search(c.text)]

    def defer(reason: str) -> Deferred:
        return Deferred(
            member_name=record.name,
            district=record.district,
            chamber=record.chamber,
            session_year=record.year,
            reason=reason,
            evidence=annotation,
        )

    # Seating: prefer the swearing-in over the election — service starts when sworn, and using
    # the ballot date would open the span weeks early.
    for verb, reason in (("sworn_in", "sworn_in"), ("appointed", "appointed")):
        effective = _day_value(seating_clauses, verb)
        if effective is _MISSING:
            continue
        if effective is None:
            return defer(DEFER_NO_DAY_PRECISION)
        return _proposal(record, "seated", reason, effective, annotation)

    effective = _day_value(clauses, "deceased")
    if effective is not _MISSING:
        if effective is None:
            return defer(DEFER_NO_DAY_PRECISION)
        return _proposal(record, "departed", "died", effective, annotation)

    effective = _day_value(clauses, "resigned")
    if effective is not _MISSING:
        if effective is None:
            return defer(DEFER_NO_DAY_PRECISION)
        # A move keeps the member serving, so closing every span would wrongly end their party
        # tenure too — one seat vacates instead.
        if moved:
            return _proposal(record, "vacated", "moved", effective, annotation)
        return _proposal(record, "departed", "resigned", effective, annotation)

    return defer(DEFER_NO_SUCCESSION_VERB)


def propose_events(records: Iterable[RosterRecord]) -> SuccessionReport:
    """Derive event proposals from annotated roster records. Pure; every input is accounted for."""
    proposals: list[EventProposal] = []
    deferred: list[Deferred] = []
    for record in records:
        if not record.annotation:
            continue
        outcome = _propose_one(record)
        if isinstance(outcome, EventProposal):
            proposals.append(outcome)
        else:
            deferred.append(outcome)
    return SuccessionReport(proposals=tuple(proposals), deferred=tuple(deferred))


def summarize(report: SuccessionReport) -> dict[str, int]:
    """Counts by proposal kind and deferral reason — the shape the CLI prints."""
    counts: dict[str, int] = {}
    for proposal in report.proposals:
        counts[f"{proposal.kind}:{proposal.reason}"] = (
            counts.get(f"{proposal.kind}:{proposal.reason}", 0) + 1
        )
    for item in report.deferred:
        counts[f"deferred:{item.reason}"] = counts.get(f"deferred:{item.reason}", 0) + 1
    return counts


def proposals_for_seat(
    report: SuccessionReport, *, district: int, chamber: str
) -> Sequence[EventProposal]:
    """Every proposal touching one district's chamber, oldest first — the acceptance view."""
    return sorted(
        (p for p in report.proposals if p.district == district and p.chamber == chamber),
        key=lambda p: p.effective_date,
    )
