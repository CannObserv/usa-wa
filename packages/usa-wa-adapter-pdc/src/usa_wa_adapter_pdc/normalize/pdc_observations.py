"""House-position observation projector (#79) — PDC winners → tenure observations (pure).

The archive-first Phase B analog of the daily :func:`normalize_house_positions`: instead of
emitting one per-biennium Assignment per winner, it projects each year's cohort into
:class:`~usa_wa_adapter_legislature.tenure_spans.Observation`s that the span builder merges
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
- ``pdc_identifiers`` — ``(member_id, pdc_person_id)`` links for directly-seated winners and
  for confirmed movers (cross-linked onto their Senate Person). An **inferred** seat carries
  no identifier (the replacement was appointed, not a PDC winner).
- ``inferred_keys`` — ``(member_id, biennium)`` for each inferred seat, so the driver can log
  the #74 inference (the per-biennium reduced-confidence FactCitation of the daily path does
  not survive span merging; the inference is recorded as a log + this set instead).
- ``summary`` — per-cohort tallies for the coverage-shortfall logging the issue asks for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from usa_wa_adapter_legislature.normalize.members import canonicalize_party, district_number
from usa_wa_adapter_legislature.tenure_spans import Observation
from usa_wa_adapter_pdc.normalize.pdc_matching import (
    HouseRosterEntry,
    SenateEntry,
    find_confirming_senator,
    match_house_member,
)
from usa_wa_adapter_pdc.normalize.positions import (
    canonical_position,
    house_span_discriminator,
    surname_match_set,
)

#: Tenure ``kind`` for a House Position seat — matches the legacy per-biennium dimension so
#: the migration (#79 inc4) can recognise the rows it supersedes.
KIND_HOUSE = "chamber-house"


@dataclass(frozen=True)
class _Deferred:
    """A PDC winner that matched no House roster member — a #74 mover-inference candidate."""

    qualifier: str
    filer_name: str
    pdc_person_id: str


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
    """Project one election cohort's winners against ``biennium``'s WSL roster (pure)."""
    proj = HousePositionProjection()
    seen_members: set[str] = set()
    deferred: dict[int, list[_Deferred]] = {}
    direct_seated = inferred_seated = movers_linked = unresolved = incomplete = 0

    # Phase 1 — direct within-LD match of each winner to a House roster member.
    for row in winners:
        pdc_id = str(row.get("person_id") or "").strip()
        qualifier = canonical_position(row.get("position"))
        ld = district_number(row.get("legislative_district"))
        if not pdc_id or qualifier is None or ld is None:
            incomplete += 1
            continue
        match = match_house_member(
            house_roster,
            ld,
            surname_match_set(row.get("filer_name") or ""),
            canonicalize_party(row.get("party_code")),
        )
        if match is None:
            deferred.setdefault(ld, []).append(
                _Deferred(
                    qualifier=qualifier,
                    filer_name=row.get("filer_name") or "",
                    pdc_person_id=pdc_id,
                )
            )
            continue
        if match.member_id in seen_members:
            continue  # a member already seated this cohort (double-match) — skip the dup
        proj.observations.append(
            Observation(
                member_id=match.member_id,
                kind=KIND_HOUSE,
                discriminator=house_span_discriminator(ld, qualifier),
                biennium=biennium,
            )
        )
        proj.pdc_identifiers.append((match.member_id, pdc_id))
        seen_members.add(match.member_id)
        direct_seated += 1

    # Phase 2 — reconcile mid-biennium replacements by within-LD elimination (#74).
    for ld, deferrals in deferred.items():
        unmatched = [m for m in house_roster.get(ld, []) if m.member_id not in seen_members]
        movers = [
            (d, senator)
            for d in deferrals
            if (senator := find_confirming_senator(d.filer_name, ld, senate_roster)) is not None
        ]
        for deferral, senator in movers:
            # The mover's PDC winner identity is theirs even though they left the House.
            proj.pdc_identifiers.append((senator.member_id, deferral.pdc_person_id))
            movers_linked += 1

        attempted = len(deferrals) == 1 and len(unmatched) == 1 and len(movers) == 1
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
            unresolved += len(deferrals)

    proj.summary = {
        "winners": len(winners),
        "direct_seated": direct_seated,
        "inferred_seated": inferred_seated,
        "movers_linked": movers_linked,
        "unresolved": unresolved,
        "incomplete": incomplete,
    }
    return proj
