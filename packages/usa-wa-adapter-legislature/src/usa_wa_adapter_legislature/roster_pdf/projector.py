"""Pre-1991 roster → tenure observations (#228 §4/§5, epic #219 Phase 3b). Pure.

Turns :class:`~usa_wa_adapter_legislature.roster_pdf.identity.RosterIdentity` groups into
the :class:`~clearinghouse_domain_legislative.tenure_spans.Observation` shapes the sponsor
Phase B already emits — ``(member, party, slug, biennium)`` and ``(member, chamber-senate,
LD, biennium)`` — so the span builder consumes pre-1991 roster tenure exactly the way it
consumes 1991+ wire tenure. A WSL-joined identity keys on its member id (its roster spans
extend the same Person the sponsor builder owns); a minted identity keys on its roster key.

**The Senate expansion (§5).** The roster lists a member only in the session year their
term begins, so a term expands ``TERM_YEARS`` forward — bounded by the **next listing on
the seat** (a flat expansion overruns the next occupant 145 times; redistricting, not
turnover, drives most of those) and by the 1991 identity floor (that era belongs to the
WSL sponsor archive). The year arithmetic hands each covered session year to the term
calendar's biennium quantizer rather than inventing a date convention — the class of
defect #226 spent three review rounds on.

**Succession refinement, at biennium grain only.** A successor row is listed under its
predecessor's term-start year; its own annotation dates the real boundary (Beck: appointed
Feb 11, 1974, printed under 1971). A dated start opens the row's coverage at the
boundary's biennium; a dated departure closes it there. Rows without dates genuinely
overlap at this grain — a same-biennium handoff is two people who both served in the
biennium — so overlaps are **reported**, never silently kept or dropped. Day-precision
correction stays #226's job; this module emits spans and no events (§5).

**Party follows the seat coverage (§4)**, split where a change annotation says so. The
dated-no-token family names no new party; the member's next listing does, and only this
module can see that row. A token #227 declines (the two power-map#442 adjudications)
withholds the party observation and tallies the reason — the seat spans build normally
(§6).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from clearinghouse_domain_legislative.span_kinds import KIND_PARTY, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import Observation
from clearinghouse_domain_legislative.terms import biennium_for_date, biennium_start_year
from usa_wa_adapter_legislature.roster_pdf.audit import TERM_YEARS
from usa_wa_adapter_legislature.roster_pdf.identity import (
    ROSTER_IDENTITY_FLOOR,
    RosterIdentity,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.party_changes import (
    PartyChange,
    PartyChangeUnparsed,
    parse_party_change,
)
from usa_wa_adapter_legislature.roster_pdf.succession import parse_annotation
from usa_wa_common.parties import resolve_party_token


def _biennium(year: int) -> str:
    """The ``YYYY-YY`` biennium covering ``year`` — the term calendar's quantizer, which
    owns the odd-year floor (spec §5: this module must not invent a date convention)."""
    return biennium_for_date(date(year, 1, 1))


def _start_date(annotation: str | None) -> date | None:
    """The dated boundary that opens a successor row's service, if its annotation has one.

    Swearing-in wins over appointment wins over election — service begins when sworn, and
    the ballot date would open the span early. Mirrors the #226 succession semantics
    without adopting its event shapes: here the date only picks a starting biennium.
    """
    if not annotation:
        return None
    clauses = parse_annotation(annotation)
    for verb in ("sworn_in", "appointed", "elected"):
        for clause in clauses:
            if clause.verb == verb and clause.parsed.precision == "day":
                return clause.parsed.value
    return None


def _end_date(annotation: str | None) -> date | None:
    """The dated boundary that closes a row's service, if its annotation has one."""
    if not annotation:
        return None
    clauses = parse_annotation(annotation)
    for verb in ("deceased", "resigned"):
        for clause in clauses:
            if clause.verb == verb and clause.parsed.precision == "day":
                return clause.parsed.value
    return None


@dataclass(frozen=True)
class DeclinedParty:
    """One withheld party assignment (§6), attributed so the residue is actionable."""

    member: str
    year: int
    token: str
    reason: str


@dataclass(frozen=True)
class UncoveredRow:
    """A record whose refined coverage is empty — reported, never silently dropped."""

    member: str
    record: RosterRecord
    reason: str


@dataclass(frozen=True)
class UnresolvedChange:
    """A change annotation that could not shape a split — tallied, never guessed at."""

    member: str
    year: int
    reason: str
    annotation: str


@dataclass(frozen=True)
class SeatOverlap:
    """Two members covering one Senate seat in one biennium — the handoff residue."""

    district: int
    biennium: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class RosterProjection:
    """Observations plus every withheld or unresolved input — report-don't-drop."""

    observations: tuple[Observation, ...]
    #: Withheld party assignments with their member and reason (§6 — Welty and Hill).
    declined_parties: tuple[DeclinedParty, ...]
    #: Unrecognized row tokens, counted by token — must stay empty on the archived edition.
    unrecognized_parties: Counter
    unresolved_changes: tuple[UnresolvedChange, ...]
    seat_overlaps: tuple[SeatOverlap, ...]
    #: Records contributing zero bienniums, with why (CR #74).
    uncovered_rows: tuple[UncoveredRow, ...]


def _covered_years(
    record: RosterRecord,
    listings: dict[tuple[str, int], list[int]],
) -> tuple[list[int], str | None]:
    """The session years ``record``'s listing covers, per the §5 rule.

    ``span_end_year = min(term_start + TERM_YEARS, next_listing_on_this_seat) - 1``,
    clamped below the identity floor; a dated start pushes the first covered year to the
    boundary's biennium, a dated departure drops the years after its own. All year→biennium
    quantization defers to the term calendar.

    Returns ``(years, empty_reason)``: an empty coverage is legitimate more often than not —
    a member who died between election and swearing-in never sat the term; a 1989-listed
    successor appointed in 1991 belongs to the WSL era — but it must be *reported*, so the
    reason distinguishes those shapes from a defect (CR #74; the measured corpus has 10,
    one of them the #252 LD43 residual).
    """
    term = TERM_YEARS[record.chamber]
    seat_years = listings.get((record.chamber, record.district), [])
    next_listing = next((y for y in seat_years if y > record.year), None)
    end = record.year + term
    if next_listing is not None:
        end = min(end, next_listing)
    end = min(end, ROSTER_IDENTITY_FLOOR)

    start_year = record.year
    started = _start_date(record.annotation)
    if started is not None and started.year > start_year:
        # Service opening mid-biennium covers that biennium.
        start_year = biennium_start_year(biennium_for_date(started))
    ended = _end_date(record.annotation)
    if ended is not None:
        end = min(end, biennium_start_year(biennium_for_date(ended)) + 2)

    years = [y for y in range(start_year, end, 2)]
    if years:
        return years, None
    if ended is not None and ended < date(record.year, 1, 1):
        return [], "ended_before_term"
    if start_year >= ROSTER_IDENTITY_FLOOR:
        return [], "starts_at_floor"
    return [], "empty_horizon"


def _party_slug(
    token: str,
    year: int,
    member: str,
    declined: list[DeclinedParty],
    unrecognized: Counter,
) -> str | None:
    resolution = resolve_party_token(token, year=year)
    if resolution.slug is not None:
        return resolution.slug
    if resolution.disposition == "declined":
        declined.append(
            DeclinedParty(member=member, year=year, token=token, reason=resolution.reason or "")
        )
    else:
        unrecognized[token] += 1
    return None


def build_pre1991_observations(
    identities: Iterable[RosterIdentity],
    all_records: Iterable[RosterRecord],
) -> RosterProjection:
    """Project resolved identities into party + Senate-seat observations.

    ``all_records`` supplies the seat listing index for the §5 truncation bound — the next
    listing on a seat bounds a term whoever the next occupant resolves to, so the index
    must see every parsed record, refused groups included.
    """
    listings: dict[tuple[str, int], list[int]] = defaultdict(list)
    for record in all_records:
        key = (record.chamber, record.district)
        if record.year not in listings[key]:
            listings[key].append(record.year)
    for years in listings.values():
        years.sort()

    observations: list[Observation] = []
    declined: list[DeclinedParty] = []
    unrecognized: Counter = Counter()
    unresolved: list[UnresolvedChange] = []
    uncovered: list[UncoveredRow] = []
    senate_cover: dict[tuple[int, str], set[str]] = defaultdict(set)

    for identity in identities:
        member = identity.wsl_member_id or identity.key
        if member is None:
            raise ValueError(f"identity {identity.fold!r} has neither WSL member id nor key")
        ordered = sorted(identity.records, key=lambda r: (r.year, r.order))
        for index, record in enumerate(ordered):
            years, empty_reason = _covered_years(record, listings)
            if empty_reason is not None:
                uncovered.append(UncoveredRow(member=member, record=record, reason=empty_reason))
            if record.chamber == "senate":
                for year in years:
                    biennium = _biennium(year)
                    observations.append(
                        Observation(member, KIND_SENATE, str(record.district), biennium)
                    )
                    senate_cover[(record.district, biennium)].add(member)

            # Party: the row token holds over the covered years, split by a change clause.
            change = parse_party_change(record.annotation)
            change_year: int | None = None
            new_token: str | None = None
            if isinstance(change, PartyChangeUnparsed):
                unresolved.append(
                    UnresolvedChange(
                        member=member,
                        year=record.year,
                        reason="unparsed",
                        annotation=change.annotation,
                    )
                )
            elif isinstance(change, PartyChange):
                change_year = change.effective_year
                new_token = change.token
                if new_token is None:
                    # The dated family: the new party is the next listing's row token.
                    following = next(
                        (r for r in ordered[index + 1 :] if r.year > record.year), None
                    )
                    if following is not None:
                        new_token = following.party_token
                    else:
                        unresolved.append(
                            UnresolvedChange(
                                member=member,
                                year=record.year,
                                reason="no_next_listing",
                                annotation=record.annotation or "",
                            )
                        )
                        change_year = None

            for year in years:
                token = record.party_token
                token_year = record.year
                if change_year is not None and new_token is not None and year >= change_year:
                    token, token_year = new_token, change_year
                slug = _party_slug(token, token_year, member, declined, unrecognized)
                if slug is not None:
                    observations.append(Observation(member, KIND_PARTY, slug, _biennium(year)))

    overlaps = tuple(
        SeatOverlap(district=district, biennium=biennium, members=tuple(sorted(members)))
        for (district, biennium), members in sorted(senate_cover.items())
        if len(members) > 1
    )
    return RosterProjection(
        observations=tuple(observations),
        declined_parties=tuple(declined),
        unrecognized_parties=unrecognized,
        unresolved_changes=tuple(unresolved),
        seat_overlaps=overlaps,
        uncovered_rows=tuple(uncovered),
    )
