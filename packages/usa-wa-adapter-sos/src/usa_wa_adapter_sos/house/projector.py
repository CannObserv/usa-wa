"""Pure House-seat projector (#101/#103) — WSL roster + SOS position → tenure observations.

The re-partition's core projection. The House Position seat is now **WSL+SOS-primary,
symmetric with the Senate** (#75): WSL owns *who sits* (the sponsor roster — LD + party), SOS
owns *which position* (the ballot Position 1/2 from the votewa filing archive). This projector
joins them per biennium into :class:`~usa_wa_adapter_legislature.tenure_spans.Observation`s the
merged-span builder consumes — the House analog of :func:`sponsor_observations` (which emits the
Senate seat). No PDC winner cohort: PDC is demoted to the ``person_wa_pdc`` cross-link only.

Pure — no DB, no session. A sitting member with **no resolvable SOS position** (LD not in the
archive, an SOS match miss, or a pre-2008 biennium below the votewa floor) emits **nothing**,
counted ``missing_position`` (OQ1 / #101: a post-1965 unknown position is a data gap, not a
position-less ``state_representative`` seat — PM rejects that via ``requires_qualifier`` and it
would be a false structural claim; the genuine pre-1965 at-large seat is power-map#302) —
**unless** the within-LD elimination (#103) resolves it: the chamber seats exactly 2 members/LD,
so an LD with exactly one ballot-claimed seat and exactly one unmatched sitting member gives
that member the remaining position deterministically. This seats a mid-biennium appointee (never
on the ballot — Obras/Salahuddin 2025-26) and heals a ballot↔roster name change
(Caldier→Valdez, McCabe→Mosbrucker) alike; inferred seats are tracked in ``inferred_keys``
(the PDC #74 precedent) so the emitter can cite the roster wire and the operator can audit.

A ``seed_positions`` entry (#118, from :mod:`.backchain`) likewise seats an otherwise-unmatched
member rostered in that LD — a Position back-chained from a later biennium's ballot anchor — and
feeds the elimination like a ballot-claimed seat. Seeded seats join ``inferred_keys`` (roster-
cited) and are also tracked in ``seeded_keys`` for the back-chain's carry-back bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from usa_wa_adapter_legislature.tenure_spans import Observation
from usa_wa_adapter_pdc.normalize.pdc_matching import HouseRosterEntry
from usa_wa_adapter_pdc.normalize.pdc_observations import KIND_HOUSE
from usa_wa_adapter_pdc.normalize.positions import house_span_discriminator
from usa_wa_adapter_sos.positions import HousePosition, position_for

#: The two WA House seats per LD — the closed world the elimination inference (#103) rests on.
_HOUSE_QUALIFIERS = frozenset({"Position 1", "Position 2"})


@dataclass(frozen=True)
class HouseSeatProjection:
    """One biennium's House-seat projection — the positioned observations plus a per-cohort
    tally so a coverage shortfall (members whose position SOS couldn't supply) is visible.
    ``inferred_keys`` marks the roster-cited ``(member_id, biennium)`` pairs — elimination-seated
    (#103) **and** back-chain-seeded (#118). ``matched_keys`` (ballot-matched) and ``seeded_keys``
    (back-chain-seeded, a subset of ``inferred_keys``) are the ballot-class members the #118
    orchestrator carries back to earlier biennia."""

    observations: list[Observation] = field(default_factory=list)
    inferred_keys: list[tuple[str, str]] = field(default_factory=list)
    matched_keys: list[tuple[str, str]] = field(default_factory=list)
    seeded_keys: list[tuple[str, str]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def build_house_seat_observations(
    house_roster: dict[int, list[HouseRosterEntry]],
    sos_filings: dict[int, list[HousePosition]],
    *,
    biennium: str,
    seed_positions: dict[int, dict[str, str]] | None = None,
) -> HouseSeatProjection:
    """Project the sitting House roster + the seating election's SOS filings into positioned
    :class:`Observation`s for ``biennium``.

    For each rostered member, look up the ballot Position for their ``(LD, folded surname,
    party)`` in the SOS filings (:func:`position_for` — zero/ambiguous → ``None``, never
    guessed). A resolved position yields one observation keyed on the House span discriminator
    (``ld-{n}-position-{p}``, identical to the retired PDC-built key so the migration is a
    re-point, #101). An LD left with exactly one unmatched member takes the remaining position
    by elimination (#103) when the roster has exactly 2 sitting members and exactly one seat is
    ballot-claimed — no party constraint (the position is the seat, not the ballot name).
    Guardrails decline everything else: a 3-member roster (a named predecessor still claims
    their seat — sequential occupancy), a double-unmatched LD, and the pre-2008 era (no seat is
    ever ballot-claimed) all stay honest ``missing_position`` gaps.

    ``seed_positions`` (``{ld: {member_id: qualifier}}``, #118) back-chains a member's Position
    from a later biennium's ballot anchor. A seed is applied only to an otherwise-unmatched
    member **rostered in that LD** whose seeded qualifier isn't already ballot-claimed there — so
    an LD move or a tenure gap breaks the chain at the roster, and ballot evidence always wins.
    An applied seed counts a claimed position (feeding the #103 elimination of its mate — the
    1-hop within-biennium cascade) and cites the roster (joins ``inferred_keys``)."""
    seed_positions = seed_positions or {}
    observations: list[Observation] = []
    inferred_keys: list[tuple[str, str]] = []
    matched_keys: list[tuple[str, str]] = []
    seeded_keys: list[tuple[str, str]] = []
    matched = seeded = inferred = missing_position = members = 0
    for ld, entries in house_roster.items():
        resolved: list[tuple[HouseRosterEntry, str]] = []
        unmatched: list[HouseRosterEntry] = []
        # Pass 1 — ballot matches (the strongest evidence, always wins).
        for entry in entries:
            members += 1
            qualifier = position_for(sos_filings, ld, entry.folded_last, entry.party_slug)
            if qualifier is None:
                unmatched.append(entry)
            else:
                resolved.append((entry, qualifier))
                matched_keys.append((entry.member_id, biennium))
                matched += 1
        # Pass 2 — apply back-chain seeds (#118) to still-unmatched members in this LD, unless
        # the seeded qualifier is already ballot-claimed here.
        ld_seeds = seed_positions.get(ld, {})
        if ld_seeds:
            claimed = {qualifier for _, qualifier in resolved}
            still_unmatched: list[HouseRosterEntry] = []
            for entry in unmatched:
                qualifier = ld_seeds.get(entry.member_id)
                if qualifier is None or qualifier in claimed:
                    still_unmatched.append(entry)
                    continue
                resolved.append((entry, qualifier))
                claimed.add(qualifier)
                seeded_keys.append((entry.member_id, biennium))
                inferred_keys.append((entry.member_id, biennium))
                seeded += 1
            unmatched = still_unmatched
        # Pass 3 — #103 within-LD elimination over the combined resolved set.
        remaining = _HOUSE_QUALIFIERS - {qualifier for _, qualifier in resolved}
        if len(entries) == 2 and len(unmatched) == 1 and len(remaining) == 1:
            entry = unmatched.pop()
            resolved.append((entry, next(iter(remaining))))
            inferred_keys.append((entry.member_id, biennium))
            inferred += 1
        missing_position += len(unmatched)
        for entry, qualifier in resolved:
            observations.append(
                Observation(
                    entry.member_id, KIND_HOUSE, house_span_discriminator(ld, qualifier), biennium
                )
            )
    return HouseSeatProjection(
        observations=observations,
        inferred_keys=inferred_keys,
        matched_keys=matched_keys,
        seeded_keys=seeded_keys,
        summary={
            "members": members,
            "matched": matched,
            "seeded": seeded,
            "inferred": inferred,
            "missing_position": missing_position,
        },
    )
