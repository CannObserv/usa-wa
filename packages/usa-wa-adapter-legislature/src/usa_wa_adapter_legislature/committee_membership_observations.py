"""Committee-roster→observation projection (#82) — pure.

Projects archived ``GetCommitteeMembers`` rosters (``{(biennium, committee_id): [member
rows]}``, re-parsed offline from the historical member archive) into tenure
:class:`~usa_wa_adapter_legislature.tenure_spans.Observation`s the span builder consumes.

One observation per **named** member per (committee, biennium): a member who sat on
Appropriations in 2013-14 and 2015-16 yields two observations under one key, which the span
builder merges into a single contiguous membership span. Leaving the committee for a biennium
and returning later opens a *second* span (dormancy breaks a tenure — see the builder's note).

The discriminator is the committee's stable WSL ``Id`` (the Org's ``source_id``), not its
name: WSL re-keys committees across eras, and a re-key is a genuinely different committee
(the sub-project-3 model-A identity decision). So a re-keyed committee's membership is a new
span, correctly.

Name-blanked stubs are skipped (:func:`is_person`), matching every other member projection.
"""

from __future__ import annotations

from usa_wa_adapter_legislature.normalize.members import is_person
from usa_wa_adapter_legislature.tenure_spans import Observation

#: Tenure ``kind`` for committee membership (the span builder is generic over kinds).
KIND_COMMITTEE = "committee"


def build_committee_membership_observations(
    rosters: dict[tuple[str, str], list[dict]],
) -> list[Observation]:
    """Project ``{(biennium, committee_source_id): [member rows]}`` into membership
    :class:`Observation`s.

    Order-preserving over the input; the span builder groups/sorts, so callers need not."""
    observations: list[Observation] = []
    for (biennium, committee_source_id), members in rosters.items():
        for member in members:
            if not is_person(member):
                continue
            observations.append(
                Observation(
                    member_id=str(member["Id"]),
                    kind=KIND_COMMITTEE,
                    discriminator=committee_source_id,
                    biennium=biennium,
                )
            )
    return observations
