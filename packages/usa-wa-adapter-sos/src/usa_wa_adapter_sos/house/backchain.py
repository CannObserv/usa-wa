"""Pure #118 back-chain orchestrator — pre-2009 House Position depth from continuous tenure.

The SOS ballot archive floors at the 2008 general (#140), so pre-2009 House members carry no
Position seat. But a WA rep runs for a *specific* Position and holds it continuously, so a
ballot-anchored Position propagates **backward** through an uninterrupted same-LD tenure (the
direct seed), and seeding one seat in an LD lets the #103 within-LD elimination resolve the mate
(the 1-hop within-biennium cascade). This walks the archived biennia newest→oldest, carrying each
**ballot-class** member's Position back one biennium at a time and re-projecting so the elimination
cascades — the reachable window is the 2001-map era's pre-2009 biennia (2003-04→2007-08).

**Phase 1 (this module).** Only ballot-class positions carry back — a member matched by their own
ballot, or seeded by their own back-chain (the same seat). A member resolved *only* by
within-biennium elimination is emitted but is **not** a carry-back source; recursively chaining an
eliminated mate through their own tenure is Phase 2, deferred to bound the false-positive risk (a
wrong hop asserts a false structural seat — the #101 "absence is honest" stance).

**Guardrails.**
- **Redistricting era breaks** (:data:`REDISTRICTING_ERA_START_BIENNIA`) — WA keeps LD numbers
  across a plan, so the discriminator can't auto-break; the walk explicitly refuses to carry a
  Position from an era-start biennium back into the prior era (a renumbered district is a different
  seat).
- **LD move / tenure gap** — a seed is applied by the projector only if the member is rostered in
  that LD that biennium (:func:`build_house_seat_observations`), so a move or a gap breaks the
  chain at the roster with no special-casing here.
- **max_hops** — confidence decays with distance from the ballot anchor; the cap bounds it (the
  era break is the hard stop within scope).

Pure — no DB, no session. Runs inside the ONE builder (`house.build`) before span merging, so daily
and backfill share span identity (no #100-CR depth mismatch).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from usa_wa_adapter_legislature.synthesis import parse_biennium
from usa_wa_adapter_legislature.tenure_spans import Observation
from usa_wa_adapter_pdc.normalize.pdc_matching import HouseRosterEntry
from usa_wa_adapter_pdc.normalize.positions import parse_house_span_discriminator
from usa_wa_adapter_sos.house.projector import build_house_seat_observations
from usa_wa_adapter_sos.positions import HousePosition

#: WA biennia that **start** a redistricting map-era (first seated after a new plan: 1992→1993-94,
#: 2002→2003-04, 2012→2013-14, 2022→2023-24). A Position must not back-chain from one of these into
#: the prior era — the LD number persists but the district identity changed (#118, #140).
REDISTRICTING_ERA_START_BIENNIA = frozenset({"1993-94", "2003-04", "2013-14", "2023-24"})

#: Default cap on back-chain hops from a ballot anchor. 4 spans the 2001-map era's reachable depth
#: (a 2010 anchor → 2003-04 floor); the era break is the hard correctness stop within scope.
MAX_BACKCHAIN_HOPS_DEFAULT = 4


@dataclass(frozen=True)
class BackchainResult:
    """The combined observation set across all archived biennia, plus the back-chain provenance.

    ``inferred_keys`` = every roster-cited seat (elimination #103 + back-chain seed #118), the
    union of the per-biennium projections. ``backchain_keys`` is the back-chained subset (for the
    emitter's roster citation and operator audit). ``depth`` maps a ballot-class ``(member,
    biennium)`` to its hop distance from a real ballot (0 = ballot-matched). ``coverage`` is the
    per-biennium projector summary."""

    observations: list[Observation] = field(default_factory=list)
    inferred_keys: list[tuple[str, str]] = field(default_factory=list)
    backchain_keys: list[tuple[str, str]] = field(default_factory=list)
    depth: dict[tuple[str, str], int] = field(default_factory=dict)
    coverage: dict[str, dict[str, int]] = field(default_factory=dict)


def _previous_biennium(biennium: str) -> str:
    """The biennium immediately before ``biennium`` (start year − 2)."""
    start, _ = parse_biennium(biennium)
    prev = start - 2
    return f"{prev}-{(prev + 1) % 100:02d}"


def backchain_house_observations(
    rosters: dict[str, dict[int, list[HouseRosterEntry]]],
    positions: dict[str, dict[int, list[HousePosition]]],
    *,
    max_hops: int = MAX_BACKCHAIN_HOPS_DEFAULT,
    era_start_biennia: frozenset[str] = REDISTRICTING_ERA_START_BIENNIA,
) -> BackchainResult:
    """Project every archived biennium and back-chain ballot-class Positions into earlier ones.

    ``rosters`` / ``positions`` are keyed by biennium (the caller maps the seating election to its
    biennium). Biennia are walked newest→oldest: each is projected with the seeds carried from its
    successor, then each ballot-class member's Position is seeded onto the previous biennium (under
    the member's current LD) unless the current biennium starts a new map-era or the next hop would
    exceed ``max_hops``. The projector applies a seed only to a member rostered in that LD, so LD
    moves and tenure gaps break the chain naturally."""
    ordered = sorted(rosters, key=lambda b: parse_biennium(b)[0])
    seeds: dict[str, dict[int, dict[str, str]]] = {}
    seed_hops: dict[str, dict[str, int]] = {}

    result = BackchainResult()
    for biennium in reversed(ordered):
        proj = build_house_seat_observations(
            rosters[biennium],
            positions.get(biennium, {}),
            biennium=biennium,
            seed_positions=seeds.get(biennium),
        )
        result.observations.extend(proj.observations)
        result.inferred_keys.extend(proj.inferred_keys)
        result.backchain_keys.extend(proj.seeded_keys)
        result.coverage[biennium] = proj.summary

        disc_by_member = {o.member_id: o.discriminator for o in proj.observations}
        hops_here = seed_hops.get(biennium, {})
        # Ballot-class members = ballot-matched (hop 0) ∪ back-chain-seeded (hop k). NOT the
        # elimination-only mates (Phase-1 carry-back boundary).
        ballot_class: dict[str, int] = {}
        for member, _ in proj.matched_keys:
            ballot_class[member] = 0
        for member, _ in proj.seeded_keys:
            ballot_class[member] = hops_here[member]
        for member, hops in ballot_class.items():
            result.depth[(member, biennium)] = hops

        if biennium in era_start_biennia:
            continue
        prev = _previous_biennium(biennium)
        if prev not in rosters:
            continue
        for member, hops in ballot_class.items():
            if hops + 1 > max_hops:
                continue
            ld, qualifier = parse_house_span_discriminator(disc_by_member[member])
            seeds.setdefault(prev, {}).setdefault(ld, {})[member] = qualifier
            seed_hops.setdefault(prev, {})[member] = hops + 1
    return result
