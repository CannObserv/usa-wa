"""Committee-member normalizer — ``CommitteeService.GetActiveCommitteeMembers`` →
committee-membership Assignments.

Per member of one committee's current roster: a :class:`Person` (deduped against the
sponsor pull by the stable WSL ``Id``) + its identifier, and a **membership Assignment**
(``Person → member Role`` on the committee Org, power-map#269, session-scoped to the
biennium). No chair/vice-chair — WSL's ``GetActiveCommitteeMembers`` carries no position
field (spec Lossy ← known-limits), so every member is a plain ``member``.

The committee Org is resolved by its WSL ``Id`` (``source_id`` = ``committee_source_id``,
carried on the resource id) — the daily refresh pulls committees before fanning out over
members, so the Org exists; a missing Org yields an empty batch + a warning rather than a
crash. See :mod:`normalize.members` for the session-touching get-or-create rationale.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.normalize.members import (
    EntityCollector,
    assignment_source_id,
    build_assignment,
    build_person_identifier,
    committee_member_role_source_id,
    get_or_create_person,
    get_or_create_role,
    is_person,
)
from usa_wa_adapter_legislature.synthesis import parse_biennium

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
_MEMBER_ROLE_NAME = "Member"
_MEMBER_ROLE_TYPE = "member"


async def normalize_committee_members(
    payload: FetchedPayload,
    *,
    session: AsyncSession,
    committee_source_id: str,
    biennium: str,
) -> NormalizedBatch:
    """Parse a committee-members payload and emit per-member membership Assignments.

    ``committee_source_id`` is the committee's WSL ``Id`` (the org's ``source_id``); the
    Person/Role get-or-create helpers resolve ids so each Assignment carries real FKs."""
    if payload.parsed is not None:
        members = payload.parsed
    else:
        members = json.loads(payload.body.decode("utf-8"))

    committee = (
        await session.execute(
            select(Organization).where(
                Organization.source == _SOURCE,
                Organization.source_id == committee_source_id,
            )
        )
    ).scalar_one_or_none()
    if committee is None:
        logger.warning(
            "wsl_committee_members_unknown_committee",
            extra={"committee_source_id": committee_source_id},
        )
        return NormalizedBatch(entities=[])

    start_year, _ = parse_biennium(biennium)
    valid_from = date(start_year, 1, 1)
    collector = EntityCollector()

    for member in members:
        if not is_person(member):
            # Expected per run (name-blanked stubs) — debug, not INFO.
            logger.debug(
                "wsl_committee_member_skip_non_person",
                extra={
                    "member_id": member.get("Id"),
                    "committee_source_id": committee_source_id,
                },
            )
            continue
        person = await get_or_create_person(session, member)
        collector.add(person)
        collector.add(build_person_identifier(person, member))
        # The committee's shared ``member`` Role — added lazily (only when the roster has
        # at least one member), deduped by the collector so it appears once.
        role = await get_or_create_role(
            session,
            source_id=committee_member_role_source_id(committee_source_id),
            organization_id=committee.id,
            name=_MEMBER_ROLE_NAME,
            role_type=_MEMBER_ROLE_TYPE,
        )
        collector.add(role)
        collector.add(
            build_assignment(
                source_id=assignment_source_id(
                    person.source_id, f"committee:{committee_source_id}", biennium
                ),
                person_id=person.id,
                role_id=role.id,
                valid_from=valid_from,
            )
        )

    return NormalizedBatch(entities=collector.entities)
