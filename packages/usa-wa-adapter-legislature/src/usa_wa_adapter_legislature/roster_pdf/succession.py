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
* **A House seat cannot be named from the roster.** Seat-scoped events key on
  ``ld-{n}-position-{p}`` and the roster carries no Position. That is a missing *discriminator*,
  not a missing boundary, so those land in :attr:`SuccessionReport.unseated` with the date
  intact — the write half positions them against the existing House Position span corpus
  (2003→present), and #229 supplies the rest. Person-scoped ``departed`` needs no seat, so
  deaths and full resignations are proposable in both chambers today.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from clearinghouse_domain_legislative.span_kinds import KIND_HOUSE, KIND_SENATE
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord

#: Clause verbs that assert a tenure boundary — as opposed to Speaker/Redistricted/Holdover/
#: party-change, which are real annotations but not succession events.
_SUCCESSION_VERBS = frozenset({"deceased", "sworn_in", "appointed", "resigned"})

#: Deferral reasons — why an annotation yielded no proposal. Report-don't-drop.
#: A House seat is *not* among them: an unpositioned House boundary is a real proposal with a
#: missing discriminator, and lands in :attr:`SuccessionReport.unseated`.
DEFER_NO_DAY_PRECISION = "no_day_precision"
DEFER_NO_SUCCESSION_VERB = "no_succession_verb"

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
#: stating BOTH ends of a tenure. Taking only the start asserts the member held the seat
#: indefinitely, which for a five-day substitution is worse than the biennium floor it replaces.
_UNTIL = re.compile(r"\buntil\b(?P<tail>.*)$", re.I)

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
    """What an operator event *would* be. Carries no member id — resolution is the write half.

    ``seat_discriminator`` is ``None`` on exactly one shape: a House seat-scoped boundary,
    which the roster dates but cannot position. Those live in :attr:`SuccessionReport.unseated`
    rather than :attr:`~SuccessionReport.proposals`, so "ready to write" stays a property the
    type can be trusted for.
    """

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
    #: The source page, so an operator can get back into a 233-page document.
    page_number: int


@dataclass(frozen=True)
class Deferred:
    """An annotation that yielded no proposal, and why."""

    member_name: str
    district: int
    chamber: str
    session_year: int
    reason: str
    evidence: str
    page_number: int


@dataclass(frozen=True)
class SuccessionReport:
    """Proposals, unseated proposals and deferrals. Every annotation lands in at least one.

    The three are graded by *what is missing*, not by whether the roster spoke:

    * ``proposals`` — a dated boundary that can name everything it needs to. Only a member id
      is outstanding, and that is a lookup the write half always has to do.
    * ``unseated`` — a dated House seat boundary with no Position. The roster stated the fact;
      the discriminator has to come from the span corpus (2003→present) or from #229.
    * ``deferred`` — the roster stated no boundary we are willing to date. A genuine refusal.
    """

    proposals: tuple[EventProposal, ...]
    deferred: tuple[Deferred, ...]
    unseated: tuple[EventProposal, ...] = ()


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


def _seat(record: RosterRecord) -> tuple[str, str | None]:
    """The ``(seat_kind, seat_discriminator)`` for a seat-scoped event.

    The Senate keys on its LD, which the roster states outright. The House keys on
    ``ld-{n}-position-{p}`` and the roster carries no Position, so the discriminator comes back
    ``None`` — known seat kind, unknown seat."""
    if record.chamber == "senate":
        return KIND_SENATE, str(record.district)
    return KIND_HOUSE, None


def _proposal(
    record: RosterRecord, kind: str, reason: str, effective: date, evidence: str
) -> EventProposal:
    """Build a proposal. A seat-scoped House kind comes back with a ``None`` discriminator —
    :func:`_propose_one` routes it to ``unseated`` rather than ``proposals``."""
    seat_kind = seat_discriminator = None
    if kind in ("seated", "vacated"):
        seat_kind, seat_discriminator = _seat(record)
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
        page_number=record.page_number,
    )


def _dated(clauses: Sequence[Clause], verb: str) -> date | None:
    """The day-precision date of the first ``verb`` clause that actually states one.

    A clause present but undated is **skipped, not fatal** (CR finding 10): ``Resigned August
    24, 1949; Appointed Employment Security Commissioner`` carries a dateless external
    appointment, and treating that as the seating discarded the dated resignation entirely —
    30 departures across the corpus.
    """
    for clause in clauses:
        if clause.verb == verb and clause.parsed.precision == "day":
            return clause.parsed.value
    return None


def _start_boundary(clauses: Sequence[Clause]) -> tuple[str, date] | None:
    """The single tenure **start**, or ``None``.

    An appointment and its swearing-in are two dates for *one* boundary, so they collapse rather
    than putting two starts on one seat. The swearing-in wins: service begins when sworn, and
    the appointment — or worse, the ballot — date would open the span early.
    """
    for verb, reason in (("sworn_in", "sworn_in"), ("appointed", "appointed")):
        value = _dated(clauses, verb)
        if value is not None:
            return reason, value
    return None


def _end_boundary(clauses: Sequence[Clause], *, moved: bool) -> tuple[str, str, date] | None:
    """The single tenure **end** as ``(kind, reason, date)``, or ``None``.

    A move keeps the member serving, so it vacates one seat rather than closing every span —
    closing everything would wrongly end their party tenure too.
    """
    value = _dated(clauses, "deceased")
    if value is not None:
        return "departed", "died", value
    value = _dated(clauses, "resigned")
    if value is not None:
        return ("vacated", "moved", value) if moved else ("departed", "resigned", value)
    return None


def _temporary_end(clauses: Sequence[Clause]) -> date | None:
    """The end date of a ``from … until …`` temporary appointment, if stated.

    Person-scoped ``departed``: a substitute's service stops entirely when the incumbent
    returns, so every span closes — and person-scoped needs no seat, keeping the House
    unblocked for this shape.
    """
    for clause in clauses:
        match = _UNTIL.search(clause.text)
        if match is None:
            continue
        parsed = _parse_date(match.group("tail"))
        if parsed.precision == "day":
            return parsed.value
    return None


def _propose_one(
    record: RosterRecord,
) -> tuple[list[EventProposal], list[EventProposal], list[Deferred]]:
    """Derive every dated boundary the annotation states — at most one start and one end.

    Returning a single outcome truncated real tenures: 19 annotations state two or more
    distinct dated boundaries (CR finding 11).
    """
    annotation = record.annotation or ""
    clauses = parse_annotation(annotation)
    moved = bool(_MOVE.search(annotation))
    # ``Appointed to the Senate`` names a move *destination*, not a dated seating.
    seating_clauses = [c for c in clauses if not _MOVE.search(c.text)]

    proposals: list[EventProposal] = []
    unseated: list[EventProposal] = []
    deferred: list[Deferred] = []

    def add(kind: str, reason: str, effective: date) -> None:
        proposal = _proposal(record, kind, reason, effective, annotation)
        target = (
            unseated
            if proposal.seat_kind is not None and proposal.seat_discriminator is None
            else proposals
        )
        target.append(proposal)

    start = _start_boundary(seating_clauses)
    if start is not None:
        add("seated", start[0], start[1])

    end = _end_boundary(clauses, moved=moved)
    if end is not None:
        add(end[0], end[1], end[2])
    elif start is not None:
        temporary = _temporary_end(clauses)
        if temporary is not None:
            add("departed", "resigned", temporary)

    if not proposals and not unseated and not deferred:
        # Distinguish "the source states no succession" from "it states one we refuse to date".
        reason = (
            DEFER_NO_DAY_PRECISION
            if any(c.verb in _SUCCESSION_VERBS for c in clauses)
            else DEFER_NO_SUCCESSION_VERB
        )
        deferred.append(
            Deferred(
                member_name=record.name,
                district=record.district,
                chamber=record.chamber,
                session_year=record.year,
                reason=reason,
                evidence=annotation,
                page_number=record.page_number,
            )
        )
    return proposals, unseated, deferred


def propose_events(records: Iterable[RosterRecord]) -> SuccessionReport:
    """Derive event proposals from annotated roster records. Pure; every input is accounted for."""
    proposals: list[EventProposal] = []
    unseated: list[EventProposal] = []
    deferred: list[Deferred] = []
    for record in records:
        if not record.annotation:
            continue
        record_proposals, record_unseated, record_deferred = _propose_one(record)
        proposals.extend(record_proposals)
        unseated.extend(record_unseated)
        deferred.extend(record_deferred)
    return SuccessionReport(
        proposals=tuple(proposals), deferred=tuple(deferred), unseated=tuple(unseated)
    )


def summarize(report: SuccessionReport) -> dict[str, int]:
    """Counts by proposal kind and deferral reason — the shape the CLI prints.

    ``unseated`` is counted under its own prefix rather than folded into either neighbour: it
    is neither writable today nor refused, and reporting it as a deferral is what hid 224
    dated boundaries behind a single "house_seat_unresolved" tally."""
    counts: dict[str, int] = {}
    for proposal in report.proposals:
        counts[f"{proposal.kind}:{proposal.reason}"] = (
            counts.get(f"{proposal.kind}:{proposal.reason}", 0) + 1
        )
    for proposal in report.unseated:
        key = f"unseated:{proposal.kind}:{proposal.reason}"
        counts[key] = counts.get(key, 0) + 1
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
