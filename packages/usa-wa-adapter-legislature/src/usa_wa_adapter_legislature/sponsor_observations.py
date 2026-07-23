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

from clearinghouse_domain_legislative.span_kinds import (
    KIND_PARTY,  # noqa: F401 (re-export for this package's builders/tests)
    KIND_SENATE,  # noqa: F401 (re-export for this package's builders/tests)
)
from usa_wa_adapter_legislature.normalize.members import (
    canonicalize_party,
    district_number,
    is_person,
)
from usa_wa_adapter_legislature.tenure_spans import Observation

# Tenure ``kind`` discriminators emitted here are the canonical domain span kinds
# (imported above so this package and the domain guard cannot drift, #114).


def build_sponsor_observations(
    members_by_biennium: dict[str, list[dict]],
    exclude_ids_by_biennium: dict[str, set[str]] | None = None,
) -> list[Observation]:
    """Project ``{biennium: [member rows]}`` into party + Senate-seat :class:`Observation`s.

    Order-preserving over the input; the span builder groups/sorts, so callers need not.

    ``exclude_ids_by_biennium`` (#105 (b)) drops a member's observations for the bienniums a
    caller has corroborated as stale (:mod:`roster_hygiene` — the departed-but-still-named
    Kilduff/Senn/Nguyen rows), so their party / Senate-seat spans end at the real departure
    boundary instead of staying open on ghost rows."""
    exclusions = exclude_ids_by_biennium or {}
    observations: list[Observation] = []
    for biennium, members in members_by_biennium.items():
        excluded = exclusions.get(biennium, set())
        for member in members:
            if not is_person(member):
                continue
            member_id = str(member["Id"])
            if member_id in excluded:
                continue
            party_slug = canonicalize_party(member.get("Party"))
            if party_slug is not None:
                observations.append(Observation(member_id, KIND_PARTY, party_slug, biennium))
            if member.get("Agency") == "Senate":
                ld = district_number(member.get("District"))
                if ld is not None:
                    observations.append(Observation(member_id, KIND_SENATE, str(ld), biennium))
    return observations
