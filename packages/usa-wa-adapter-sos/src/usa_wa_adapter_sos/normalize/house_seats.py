"""Pure House-seat projector (#101) — WSL roster + SOS position → tenure observations.

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
would be a false structural claim; the genuine pre-1965 at-large seat is power-map#302).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from usa_wa_adapter_pdc.normalize.pdc_matching import HouseRosterEntry
from usa_wa_adapter_pdc.normalize.pdc_observations import KIND_HOUSE
from usa_wa_adapter_pdc.normalize.positions import house_span_discriminator

from usa_wa_adapter_legislature.tenure_spans import Observation
from usa_wa_adapter_sos.normalize.filings import HouseFiling, position_for


@dataclass(frozen=True)
class HouseSeatProjection:
    """One biennium's House-seat projection — the positioned observations plus a per-cohort
    tally so a coverage shortfall (members whose position SOS couldn't supply) is visible."""

    observations: list[Observation] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def build_house_seat_observations(
    house_roster: dict[int, list[HouseRosterEntry]],
    sos_filings: dict[int, list[HouseFiling]],
    *,
    biennium: str,
) -> HouseSeatProjection:
    """Project the sitting House roster + the seating election's SOS filings into positioned
    :class:`Observation`s for ``biennium``.

    For each rostered member, look up the ballot Position for their ``(LD, folded surname,
    party)`` in the SOS filings (:func:`position_for` — zero/ambiguous → ``None``, never
    guessed). A resolved position yields one observation keyed on the House span discriminator
    (``ld-{n}-position-{p}``, identical to the retired PDC-built key so the migration is a
    re-point, #101); an unresolved one emits nothing and is counted."""
    observations: list[Observation] = []
    matched = missing_position = members = 0
    for ld, entries in house_roster.items():
        for entry in entries:
            members += 1
            qualifier = position_for(sos_filings, ld, entry.folded_last, entry.party_slug)
            if qualifier is None:
                missing_position += 1
                continue
            observations.append(
                Observation(
                    entry.member_id, KIND_HOUSE, house_span_discriminator(ld, qualifier), biennium
                )
            )
            matched += 1
    return HouseSeatProjection(
        observations=observations,
        summary={"members": members, "matched": matched, "missing_position": missing_position},
    )
