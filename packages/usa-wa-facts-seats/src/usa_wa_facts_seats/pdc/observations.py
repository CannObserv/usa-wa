"""House-position observation projector (#79) — PDC winners → tenure observations (pure).

The archive-first Phase B analog of the retired per-biennium House-positions normalizer:
instead of emitting one per-biennium Assignment per winner, it projects each year's cohort into
:class:`~clearinghouse_domain_legislative.tenure_spans.Observation`s that the span builder merges
across years into one Assignment per contiguous House tenure (#78/#82 model). It reuses the
same #69 within-LD match and #74 mid-biennium mover inference, but:

- **Era-matched** — the caller pairs each cohort with the roster of the biennium it *seated*
  (``[Y+1, Y+2]``), fixing the #75 current-snapshot limitation. This projector is agnostic to
  which biennium's roster it gets; the driver supplies the right one.
- **Pure** — no DB / session. Person resolution, LD-jurisdiction resolution, and Role
  get-or-create happen at emission time (:mod:`pdc_span_emit`), not here. LD validity is
  *not* checked here — an unsynced LD surfaces as a skipped span at emit time, logged there.

Outputs (:class:`HousePositionProjection`):

- ``observations`` — one per seated winner (direct **or** inferred), keyed on the House
  span discriminator ``ld-{n}-position-{p}``.
- ``pdc_identifiers`` — ``(member_id, pdc_person_id)`` links for directly-seated winners,
  **position-less winners resolved by within-LD surname (link only, no observation, #138)**,
  and confirmed movers (cross-linked onto their Senate Person). An **inferred** seat carries
  no identifier (the replacement was appointed, not a PDC winner).
- ``inferred_keys`` — ``(member_id, biennium)`` for each inferred seat, so the driver can log
  the #74 inference (the per-biennium reduced-confidence FactCitation of the daily path does
  not survive span merging; the inference is recorded as a log + this set instead).
- ``summary`` — per-cohort tallies for the coverage-shortfall logging the issue asks for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clearinghouse_domain_legislative.span_kinds import (
    KIND_HOUSE,  # noqa: F401 (re-export for this package's builders/tests)
)
from clearinghouse_domain_legislative.tenure_spans import Observation
from usa_wa_common.names import surname_match_set
from usa_wa_common.parties import canonicalize_party
from usa_wa_common.seats import canonical_position, district_number, house_span_discriminator
from usa_wa_facts_seats.pdc.matching import (
    HouseRosterEntry,
    SenateEntry,
    find_confirming_senator,
    match_house_member,
)


@dataclass
class SenateIdentityLinks:
    """The Senate cohort's ``person_wa_pdc`` links + robustness tally (#75, era-matched)."""

    identifiers: list[tuple[str, str]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def build_senate_identity_links(
    winners: list[dict],
    *,
    senate_roster: dict[int, list[SenateEntry]],
) -> SenateIdentityLinks:
    """Match each PDC Senate winner to its LD's WSL Senator (single seat/LD → unique) and emit
    a ``(member_id, pdc_person_id)`` link — the identifier-only Senate contribution (#75),
    era-matched here. A zero/ambiguous match is left unresolved (a WSL robustness signal),
    never guessed. Pure — Person resolution happens at emit time."""
    links = SenateIdentityLinks()
    matched = unresolved = incomplete = 0
    for row in winners:
        pdc_id = str(row.get("person_id") or "").strip()
        ld = district_number(row.get("legislative_district"))
        if not pdc_id or ld is None:
            incomplete += 1
            continue
        keys = surname_match_set(row.get("filer_name") or "")
        candidates = [s for s in senate_roster.get(ld, []) if s.folded_last in keys]
        if len(candidates) != 1:
            unresolved += 1
            continue
        links.identifiers.append((candidates[0].member_id, pdc_id))
        matched += 1
    links.summary = {
        "winners": len(winners),
        "matched": matched,
        "unresolved": unresolved,
        "incomplete": incomplete,
    }
    return links


@dataclass(frozen=True)
class _Deferred:
    """A PDC winner that matched no House roster member — a #74 mover-inference candidate.

    ``qualifier`` is the PDC ballot position, or ``None`` for a position-less winner (pre-2018,
    #138): such a deferral can still cross-link as a mover (position-independent), but can't
    seed the inferred-seat observation (no discriminator) — that path is qualifier-guarded.

    ``candidate_count`` is the number of within-LD roster members whose folded surname matched
    the winner at phase-1 time — it classifies a residual position-less deferral as *ambiguous*
    (``> 1``, a surname tie ``position`` once broke) vs *unmatched* (``0``, no roster member /
    a roster gap), which Option B's surname tiebreak would not fix (#138 review)."""

    qualifier: str | None
    filer_name: str
    pdc_person_id: str
    candidate_count: int


@dataclass
class HousePositionProjection:
    """One cohort's projection: observations + identifier links + inference markers + tally."""

    observations: list[Observation] = field(default_factory=list)
    pdc_identifiers: list[tuple[str, str]] = field(default_factory=list)
    inferred_keys: list[tuple[str, str]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def build_house_position_observations(
    winners: list[dict],
    *,
    house_roster: dict[int, list[HouseRosterEntry]],
    senate_roster: dict[int, list[SenateEntry]],
    biennium: str,
) -> HousePositionProjection:
    """Project one election cohort's winners against ``biennium``'s WSL roster (pure).

    PDC-only since #101 (the seat is the WSL+SOS builder's; this projector runs only for the
    ``pdc_identifiers`` links). A winner PDC didn't position (pre-2018, plus stray specials of any
    era) still resolves to its LD member by surname and emits the identifier link — position is
    not needed for the link (#138), only for the *observation* discriminator (which the builder
    discards). A position-less winner that can't be uniquely resolved is declined and split:
    ``positionless_ambiguous`` when the LD holds >1 surname candidate (the tie position once
    broke), or ``positionless_unmatched`` when it holds none (a roster gap, not a tie)."""
    proj = HousePositionProjection()
    seen_members: set[str] = set()
    deferred: dict[int, list[_Deferred]] = {}
    direct_seated = inferred_seated = movers_linked = unresolved = incomplete = 0
    positionless_matched = positionless_ambiguous = positionless_unmatched = 0

    # Phase 1 — direct within-LD match of each winner to a House roster member.
    for row in winners:
        pdc_id = str(row.get("person_id") or "").strip()
        pdc_qualifier = canonical_position(row.get("position"))
        ld = district_number(row.get("legislative_district"))
        # A link needs only pdc_id + ld; position is not required (#138). A row missing either
        # can't be keyed at all → incomplete.
        if not pdc_id or ld is None:
            incomplete += 1
            continue
        tokens = surname_match_set(row.get("filer_name") or "")
        match = match_house_member(
            house_roster, ld, tokens, canonicalize_party(row.get("party_code"))
        )
        if match is None:
            # Capture the within-LD surname-candidate count now (the roster is untouched) so a
            # residual position-less deferral can be split ambiguous (>1) vs unmatched (0) (#138).
            candidate_count = sum(1 for e in house_roster.get(ld, []) if e.folded_last in tokens)
            deferred.setdefault(ld, []).append(
                _Deferred(
                    qualifier=pdc_qualifier,
                    filer_name=row.get("filer_name") or "",
                    pdc_person_id=pdc_id,
                    candidate_count=candidate_count,
                )
            )
            continue
        if match.member_id in seen_members:
            continue  # a member already seated this cohort (double-match) — skip the dup
        # The seat observation needs a ballot position for its discriminator; a position-less
        # winner emits ONLY the identifier link (the seat is the WSL+SOS builder's since #101,
        # and this projector's observations are discarded by build_pdc_spans anyway).
        if pdc_qualifier is not None:
            proj.observations.append(
                Observation(
                    member_id=match.member_id,
                    kind=KIND_HOUSE,
                    discriminator=house_span_discriminator(ld, pdc_qualifier),
                    biennium=biennium,
                )
            )
            direct_seated += 1
        else:
            positionless_matched += 1
        proj.pdc_identifiers.append((match.member_id, pdc_id))
        seen_members.add(match.member_id)

    # Phase 2 — reconcile mid-biennium replacements by within-LD elimination (#74).
    for ld, deferrals in deferred.items():
        unmatched = [m for m in house_roster.get(ld, []) if m.member_id not in seen_members]
        movers = [
            (d, senator)
            for d in deferrals
            if (senator := find_confirming_senator(d.filer_name, ld, senate_roster)) is not None
        ]
        mover_deferrals = {id(d) for d, _ in movers}
        for deferral, senator in movers:
            # The mover's PDC winner identity is theirs even though they left the House.
            proj.pdc_identifiers.append((senator.member_id, deferral.pdc_person_id))
            movers_linked += 1

        # Inference needs a positioned deferral to key the replacement's seat discriminator.
        attempted = (
            len(deferrals) == 1
            and len(unmatched) == 1
            and len(movers) == 1
            and deferrals[0].qualifier is not None
        )
        if attempted:
            proj.observations.append(
                Observation(
                    member_id=unmatched[0].member_id,
                    kind=KIND_HOUSE,
                    discriminator=house_span_discriminator(ld, deferrals[0].qualifier),
                    biennium=biennium,
                )
            )
            proj.inferred_keys.append((unmatched[0].member_id, biennium))
            seen_members.add(unmatched[0].member_id)
            inferred_seated += 1
        else:
            # Residual deferrals that neither matched, moved, nor seeded an inference. Split a
            # position-less residual into ambiguous (a within-LD surname tie position once broke)
            # vs unmatched (no roster candidate — a gap Option B's tiebreak would not fix) (#138).
            for d in deferrals:
                if id(d) in mover_deferrals:
                    continue
                if d.qualifier is not None:
                    unresolved += 1
                elif d.candidate_count > 1:
                    positionless_ambiguous += 1
                else:
                    positionless_unmatched += 1

    proj.summary = {
        "winners": len(winners),
        "direct_seated": direct_seated,
        "inferred_seated": inferred_seated,
        "movers_linked": movers_linked,
        "unresolved": unresolved,
        "incomplete": incomplete,
        "positionless_matched": positionless_matched,
        "positionless_ambiguous": positionless_ambiguous,
        "positionless_unmatched": positionless_unmatched,
    }
    return proj
