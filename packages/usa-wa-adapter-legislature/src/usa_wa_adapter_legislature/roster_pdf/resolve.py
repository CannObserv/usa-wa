"""Roster proposals → writable operator events (#226 write half, epic #219 Phase 2). Pure.

An :class:`~usa_wa_adapter_legislature.roster_pdf.succession.EventProposal` states *what
happened and when*. An :class:`~clearinghouse_domain_legislative.operator_events.OperatorEvent`
additionally needs *to whom* — a WSL member id — and, for a seat-scoped kind, *which seat*.
Neither is in the roster: it names people in prose and never states a House Position. This
module supplies both from corpora already on disk, and refuses when they cannot.

**Two lookups, two corpora, two different floors.**

* *Which member* comes from the archived WSL sponsor roster, which reaches back to **1991**.
  Before that no ``Person`` exists at all, so the great majority of the roster's 8,584 records
  resolve to nobody — that is a coverage floor, not a parser failure, and it lifts only when
  historical Persons are minted.
* *Which House Position* comes from the existing House Position span corpus, which reaches back
  to **2003** (the #118 back-chain floor). A pre-2003 House ``seated``/``vacated`` is dated by
  the roster and positioned by nothing; #229 owns that.

**Surname within a seat, not surname globally.** The match is scoped to one chamber+LD in one
year, where at most two people sit, then tested with the shared
:func:`~usa_wa_common.names.surname_match_set` token-set rule — the same folding the PDC and
SOS matchers use. A name that matches two members of the same seat is **ambiguous and refused**,
never resolved by picking one: the corpus really does contain same-surname pairs in one district.

**A window of candidate years, because a boundary outlives its row.** ``Deceased June 15, 1979``
sits on a *1977* roster row — the death is in the next biennium. Keying only on the row's session
year loses those; keying only on the effective date's year loses an appointment made just before
the term it opens. Both are tried, plus one biennium either side (#277,
:data:`SEATING_ADJACENT_BIENNIA`) — listing years are odd, so a boundary dated in an even year
otherwise reached no listing at all. Matches across the window are unioned, so a genuine
two-holder collision still surfaces as ambiguity rather than being silently resolved.

Nothing here writes. :mod:`usa_wa_adapter_legislature.roster_pdf.backfill` is the write side.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from clearinghouse_domain_legislative.span_kinds import KIND_HOUSE
from usa_wa_adapter_legislature.roster_pdf.succession import EventProposal
from usa_wa_common.names import (
    fold_token,
    folded_tokens,
    strip_other_party_parts,
    surname_match_set,
)

#: Refusal reasons — why a dated boundary is still not writable. Report-don't-drop, as
#: everywhere else in this source: an unresolved proposal is reported, never dropped.
UNRESOLVED_NO_MEMBER = "no_member"
UNRESOLVED_AMBIGUOUS_MEMBER = "ambiguous_member"
UNRESOLVED_NO_POSITION = "no_position"
UNRESOLVED_AMBIGUOUS_POSITION = "ambiguous_position"
UNRESOLVED_GIVEN_NAME_MISMATCH = "given_name_mismatch"


@dataclass(frozen=True)
class Seating:
    """One member holding one chamber+LD in one calendar year — a row of the identity index.

    Built from the archived WSL sponsor roster, which is per *biennium*; the loader expands
    each biennium into its two years so a boundary can be matched by the year it happened in.
    """

    member_id: str
    chamber: str
    district: int
    year: int
    surname: str
    #: The member's WSL ``FirstName``, when the roster carries one. Empty is common enough
    #: that its absence must never be read as evidence against a match (#240).
    given_name: str = ""


#: How many years before a Position span a seating may fall and still belong to it.
#:
#: A mid-biennium appointee is frequently absent from the sponsor roster of the biennium they
#: were appointed into — that snapshot was taken before they arrived — so their first Position
#: span opens at the *following* biennium. Graham Hunt is the worked case: appointed
#: 2014-01-17, his only span is ``ld-2-position-1:2015-16``. The span starting too late is the
#: very defect this backfill corrects, so requiring containment would refuse precisely the
#: events worth writing. One year reaches the span a seating opens without letting a tenure
#: two bienniums away supply a Position the member may not have held then.
POSITION_LOOKBACK_YEARS = 1


#: How many bienniums either side of a boundary's own the seating index is searched (#277).
#:
#: The seating index is keyed on **listing years**, which are biennium starts and therefore
#: odd. The original window was ``{session_year, effective_date.year}``, so an appointment
#: dated December of an *even* year covered no listing year at all — and the appointee's first
#: listing is the *following* odd year, one step outside it. Departures are the mirror: a
#: member who leaves days into a biennium was last listed in the previous one.
#:
#: This is the same asymmetry :data:`POSITION_LOOKBACK_YEARS` encodes on the Position index —
#: *"a mid-biennium appointee is absent from the sponsor roster of the biennium they were
#: appointed into"* — which the seating index never got. One biennium reaches the adjacent
#: listing without letting a tenure two bienniums away supply identity for this boundary.
#:
#: Widening also widens the collision surface, which is why it ships with the full-given-token
#: guard in :meth:`SuccessionResolver._member_ids`: the ambiguity check stays the arbiter, and
#: a tie is still reported rather than picked.
SEATING_ADJACENT_BIENNIA = 1


def _candidate_years(proposal: EventProposal) -> set[int]:
    """Every listing year the boundary may match, per :data:`SEATING_ADJACENT_BIENNIA`.

    The boundary's own years are always kept, so this only ever *widens* — a seating recorded
    under an even year (the index is not contractually odd-only) stays reachable.
    """
    own = {proposal.session_year, proposal.effective_date.year}
    starts = {year if year % 2 else year - 1 for year in own}
    span = range(-SEATING_ADJACENT_BIENNIA, SEATING_ADJACENT_BIENNIA + 1)
    return own | {start + 2 * step for start in starts for step in span}


@dataclass(frozen=True)
class PositionTenure:
    """One House Position tenure from the existing span corpus, as a closed year window.

    ``first_year``/``last_year`` are the span's biennium-quantized bounds.

    **This index answers "which Position digit", not "which span".** Span selection belongs to
    the overlay at apply time. Because the question is only 1-or-2 — a value that is stable
    across a member's continuous tenure in one LD — the match window can reach one year before
    the span (see :data:`POSITION_LOOKBACK_YEARS`) without asserting anything about the span
    itself. A member showing *two different* Positions in that window is refused, not picked.
    """

    member_id: str
    district: int
    position: str
    first_year: int
    last_year: int

    @property
    def discriminator(self) -> str:
        """The span discriminator the House builder keys on."""
        return f"ld-{self.district}-position-{self.position}"

    def covers(self, year: int) -> bool:
        """Whether a boundary in ``year`` can take its Position from this tenure."""
        return self.first_year - POSITION_LOOKBACK_YEARS <= year <= self.last_year


@dataclass(frozen=True)
class ResolvedEvent:
    """A proposal with its member and seat resolved — everything an ``OperatorEvent`` needs.

    The boundary fields are copied verbatim from the proposal. Resolution adds identity and
    never re-decides what happened.
    """

    member_id: str
    kind: str
    reason: str
    effective_date: date
    seat_kind: str | None
    seat_discriminator: str | None
    proposal: EventProposal

    @property
    def evidence(self) -> str:
        """The roster annotation this event was derived from."""
        return self.proposal.evidence


@dataclass(frozen=True)
class Unresolved:
    """A dated boundary that cannot be written yet, and why."""

    proposal: EventProposal
    reason: str


@dataclass(frozen=True)
class ResolutionOutcome:
    """Resolved events plus refusals. Every proposal lands in exactly one."""

    resolved: tuple[ResolvedEvent, ...]
    unresolved: tuple[Unresolved, ...]


class SuccessionResolver:
    """Resolves proposals against a seating index and a House Position index. Pure."""

    def __init__(self, *, seatings: Iterable[Seating], positions: Iterable[PositionTenure]) -> None:
        self._seatings: dict[tuple[str, int, int], list[Seating]] = defaultdict(list)
        for seating in seatings:
            self._seatings[(seating.chamber, seating.district, seating.year)].append(seating)
        self._positions: dict[str, list[PositionTenure]] = defaultdict(list)
        for position in positions:
            self._positions[position.member_id].append(position)

    def _surname_matches(self, proposal: EventProposal) -> list[Seating]:
        """Every seating whose surname matches the roster row, across all candidate years."""
        keys = surname_match_set(proposal.member_name)
        years = _candidate_years(proposal)
        return [
            seating
            for year in years
            for seating in self._seatings.get((proposal.chamber, proposal.district, year), ())
            if fold_token(seating.surname) in keys
        ]

    def _member_ids(self, proposal: EventProposal) -> tuple[set[str], set[str]]:
        """``(compatible, rejected)`` member ids for the roster row.

        A surname match alone is not identity. The ambiguity check below cannot save us when
        the row's true subject is **absent from the index**: the single surviving match is then
        a *false* match, not an ambiguous one, and it resolves silently. That is #240 — William
        A. Grant died 2009-01-04, before the 2009-10 sponsor snapshot, so the only ``grant`` in
        LD16 House was his successor Laura Grant-Herriot, whom WSL records under ``LastName``
        ``Grant``. His death closed every one of her spans 18 days before she was appointed.

        The discriminator is the given name, tested in two tiers (#277). A **whole token**
        shared with the roster row is the strong signal: ``tony`` picks ``Tony P`` over
        ``August P``, ``robert`` picks ``Robert C`` over ``Ruthe``. When *some* candidate
        matches that way, the ones that do not are rejected. When **none** does, the tier falls
        back to the **initial**, which is what carries the benign variants the corpus is full of
        — nicknames (``Mike``/``Michael``), formal names (``Moyne``/``Mike``), initials-only
        rows (``J. Bruce``/``Jeffrey``), middle-name-first rows (``C Louise``/``Louise``) —
        while two different people generally do not share one (``William`` vs ``Laura``).

        Tiering rather than replacing matters: the initial rule alone let a same-initial
        relative stay compatible (all four live ``ambiguous_member`` refusals were that), and
        the full-token rule alone would refuse every initials-only row the initial rule exists
        to keep.

        The roster row is read through :func:`~usa_wa_common.names.strip_other_party_parts`
        first: ``Frances (Mrs. Thomas A.) Swayze`` otherwise contributes ``thomas`` and ``a``
        as *her own* tokens, making her husband a compatible candidate. Deliberately not the
        broader ``strip_non_name_parts`` — that also drops quoted nicknames, and a nickname is
        this person's own name (WSL records ``Robert "Bob" McCaslin,`` as ``Bob``, so dropping
        it refuses the match outright).

        Still a heuristic, not a proof: two candidates matching in full stay ambiguous, and a
        blank WSL given name stays compatible however its siblings match, because absence of
        the signal is never evidence against a match. Rejections are reported rather than
        folded into ``no_member`` so the residue stays visible.
        """
        row_tokens = set(folded_tokens(strip_other_party_parts(proposal.member_name)))
        row_full = {token for token in row_tokens if len(token) > 1}
        row_initials = {token[0] for token in row_tokens}

        matches = self._surname_matches(proposal)
        # A given name can itself be several tokens — WSL carries "C Louise" for the member the
        # roster prints as "Louise Miller" — so *any* of them agreeing is agreement. Folding it
        # to one token would refuse a real member.
        given = {seating.member_id: set(folded_tokens(seating.given_name)) for seating in matches}
        # Single letters are excluded from the full tier: a shared middle initial ("Tony P" vs
        # "August P") is exactly the false agreement this tier exists to break.
        full_matched = {
            member_id
            for member_id, tokens in given.items()
            if {token for token in tokens if len(token) > 1} & row_full
        }

        compatible: set[str] = set()
        rejected: set[str] = set()
        for seating in matches:
            tokens = given[seating.member_id]
            if not tokens:
                # No given name on the WSL side is no signal — never evidence against the match.
                compatible.add(seating.member_id)
            elif full_matched:
                # Some candidate agrees in full, so a bare initial no longer buys compatibility.
                (compatible if seating.member_id in full_matched else rejected).add(
                    seating.member_id
                )
            elif {token[0] for token in tokens} & row_initials:
                compatible.add(seating.member_id)
            else:
                rejected.add(seating.member_id)
        return compatible, rejected

    def _position(self, member_id: str, proposal: EventProposal) -> set[str]:
        """The Position discriminators covering this member's LD at the boundary's year."""
        year = proposal.effective_date.year
        return {
            tenure.discriminator
            for tenure in self._positions.get(member_id, ())
            if tenure.district == proposal.district and tenure.covers(year)
        }

    def resolve(self, proposal: EventProposal) -> ResolvedEvent | Unresolved:
        """Resolve one proposal, or refuse with a reason."""
        member_ids, rejected = self._member_ids(proposal)
        if not member_ids:
            # Distinguish "nobody by that surname sat here" from "somebody did, but they are a
            # different person" — the second is the #240 shape and worth seeing separately.
            reason = UNRESOLVED_GIVEN_NAME_MISMATCH if rejected else UNRESOLVED_NO_MEMBER
            return Unresolved(proposal=proposal, reason=reason)
        if len(member_ids) > 1:
            return Unresolved(proposal=proposal, reason=UNRESOLVED_AMBIGUOUS_MEMBER)
        member_id = member_ids.pop()

        seat_kind, seat_discriminator = proposal.seat_kind, proposal.seat_discriminator
        if seat_kind == KIND_HOUSE and seat_discriminator is None:
            found = self._position(member_id, proposal)
            if not found:
                return Unresolved(proposal=proposal, reason=UNRESOLVED_NO_POSITION)
            if len(found) > 1:
                return Unresolved(proposal=proposal, reason=UNRESOLVED_AMBIGUOUS_POSITION)
            seat_discriminator = found.pop()

        return ResolvedEvent(
            member_id=member_id,
            kind=proposal.kind,
            reason=proposal.reason,
            effective_date=proposal.effective_date,
            seat_kind=seat_kind,
            seat_discriminator=seat_discriminator,
            proposal=proposal,
        )

    def resolve_all(self, proposals: Iterable[EventProposal]) -> ResolutionOutcome:
        """Resolve a batch. Every input lands in exactly one bucket."""
        resolved: list[ResolvedEvent] = []
        unresolved: list[Unresolved] = []
        for proposal in proposals:
            outcome = self.resolve(proposal)
            if isinstance(outcome, ResolvedEvent):
                resolved.append(outcome)
            else:
                unresolved.append(outcome)
        return ResolutionOutcome(resolved=tuple(resolved), unresolved=tuple(unresolved))


def resolution_summary(outcome: ResolutionOutcome) -> dict[str, int]:
    """Counts by resolved kind and by refusal reason — the shape the CLI prints."""
    counts: dict[str, int] = {}
    for event in outcome.resolved:
        key = f"{event.kind}:{event.reason}"
        counts[key] = counts.get(key, 0) + 1
    for item in outcome.unresolved:
        key = f"unresolved:{item.reason}"
        counts[key] = counts.get(key, 0) + 1
    return counts
