"""Sponsor→observation projection (#78 increment 2, Phase B) — pure.

Projects archived WSL ``GetSponsors`` member rows (``{biennium: [rows]}``, re-parsed offline
from the sponsor archive) into tenure :class:`~usa_wa_adapter_legislature.tenure_spans.Observation`s
the span builder consumes. Per named member row (name-blanked stubs skipped):

- a **party** observation (major party only — ``canonicalize_party`` folds independent/blank
  to ``None``, which emits nothing, preserving the major-party-only rule the retired
  per-biennium ``_emit_party`` enforced, #78-2c), and
- for a **Senate** row with a parseable district, a **chamber-senate** seat observation keyed
  on the LD.

House chamber tenure needs a ballot Position PDC supplies (#79), and committee membership
comes from #82 — both emit their own observations into the *same* builder. The discriminator
choices (party slug; Senate LD) are this projection's semantic decision (see the span
builder's note on redistricting).
"""

from __future__ import annotations

from usa_wa_adapter_legislature.normalize.members import (
    canonicalize_party,
    district_number,
    is_person,
)
from usa_wa_adapter_legislature.tenure_spans import Observation

#: Tenure ``kind`` discriminators emitted here (the span builder is generic over them).
KIND_PARTY = "party"
KIND_SENATE = "chamber-senate"


def build_sponsor_observations(
    members_by_biennium: dict[str, list[dict]],
) -> list[Observation]:
    """Project ``{biennium: [member rows]}`` into party + Senate-seat :class:`Observation`s.

    Order-preserving over the input; the span builder groups/sorts, so callers need not."""
    observations: list[Observation] = []
    for biennium, members in members_by_biennium.items():
        for member in members:
            if not is_person(member):
                continue
            member_id = str(member["Id"])
            party_slug = canonicalize_party(member.get("Party"))
            if party_slug is not None:
                observations.append(Observation(member_id, KIND_PARTY, party_slug, biennium))
            if member.get("Agency") == "Senate":
                ld = district_number(member.get("District"))
                if ld is not None:
                    observations.append(Observation(member_id, KIND_SENATE, str(ld), biennium))
    return observations
