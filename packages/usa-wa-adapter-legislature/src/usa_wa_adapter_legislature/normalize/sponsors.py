"""Sponsor normalizer — WSL ``SponsorService.GetSponsors`` → Person + party + Senate seat.

Emits, per **named** member row (step 0's ``is_person`` filters the name-blanked stubs):

- a :class:`Person` (``source_id`` = the stable WSL member ``Id``) + a
  :class:`PersonIdentifier` (the ``wa_legislature_member_id`` scheme),
- a **party Assignment** to the matching Party Org (only for a major-party member;
  independent/blank → none, power-map#270), via a shared ``member`` Role, and
- for a **Senate** row, a **seat Assignment** to the ``(Senate, state_senator, LD,
  qualifier=NULL)`` seat Role (#68). A **House** row emits **no** chamber Role/Assignment
  — deferred whole to #69 (created fresh there; see the spec). House members still get
  Person + party (+ committee memberships via the committee-member normalizer).

Iterates **rows, not members** (a mid-biennium House→Senate mover has two named rows
under one ``Id``); :func:`get_or_create_person` and the :class:`EntityCollector` dedup by
identity so the Person collapses to one while each chamber tenure is handled on its own
row. See :mod:`normalize.members` for why the get-or-create helpers touch the session
(intra-batch FK resolution the runner can't do).
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.members import (
    EntityCollector,
    assignment_source_id,
    build_assignment,
    build_person_identifier,
    canonicalize_party,
    district_number,
    get_or_create_person,
    get_or_create_role,
    is_person,
    party_role_source_id,
    resolve_ld_jurisdiction,
    senate_seat_role_source_id,
)
from usa_wa_adapter_legislature.synthesis import parse_biennium

logger = get_logger(__name__)

_MEMBER_ROLE_NAME = "Member"
_MEMBER_ROLE_TYPE = "member"
_SENATE_SEAT_ROLE_NAME = "State Senator"
_SENATE_SEAT_ROLE_TYPE = "state_senator"


async def normalize_sponsors(
    payload: FetchedPayload,
    *,
    session: AsyncSession,
    anchors: BootstrapAnchors,
    biennium: str,
    persons_only: bool = False,
) -> NormalizedBatch:
    """Parse a sponsors payload and emit the member cluster (Person/identifier/party/seat).

    ``session`` is the runner's session (same transaction); the get-or-create helpers
    resolve Person/Role ids so the Assignments carry real FKs. Assignments scope to the
    biennium (``valid_from`` = Jan 1 of the odd start year). Persons/assignments carry no
    jurisdiction of their own — a seat's LD lives on the seat Role — so no jurisdiction
    parameter is threaded here.

    ``persons_only`` (the #77 historical harvest, Phase A) emits **only** Person +
    identifier, skipping party/seat Assignments — those are merged spans built from the full
    archive in Phase B (#78), not per-biennium here. Persons dedup across biennia by the
    stable WSL ``Id`` (#81), so a member seen in many biennia collapses to one Person."""
    if payload.parsed is not None:
        members = payload.parsed
    else:
        members = json.loads(payload.body.decode("utf-8"))

    start_year, _ = parse_biennium(biennium)
    valid_from = date(start_year, 1, 1)
    collector = EntityCollector()

    for member in members:
        if not is_person(member):
            # Expected per run (name-blanked departed/superseded tenure stubs) — debug.
            logger.debug(
                "wsl_sponsor_skip_non_person",
                extra={"member_id": member.get("Id"), "agency": member.get("Agency")},
            )
            continue

        person = await get_or_create_person(session, member)
        collector.add(person)
        collector.add(build_person_identifier(person, member))

        if persons_only:
            continue  # Phase A: no per-biennium Assignments — spans are Phase B (#78)
        await _emit_party(collector, session, member, person, anchors, biennium, valid_from)
        await _emit_chamber(collector, session, member, person, anchors, biennium, valid_from)

    return NormalizedBatch(entities=collector.entities)


async def _emit_party(
    collector: EntityCollector,
    session: AsyncSession,
    member: dict,
    person,
    anchors: BootstrapAnchors,
    biennium: str,
    valid_from: date,
) -> None:
    """Emit the party Assignment for a major-party member (independent/blank → none)."""
    slug = canonicalize_party(member.get("Party"))
    if slug is None or slug not in anchors.party_ids:
        return  # independent / blank / no Party Org → no party Assignment (power-map#270)
    role = await get_or_create_role(
        session,
        source_id=party_role_source_id(slug),
        organization_id=anchors.party_ids[slug],
        name=_MEMBER_ROLE_NAME,
        role_type=_MEMBER_ROLE_TYPE,
    )
    collector.add(role)
    collector.add(
        build_assignment(
            source_id=assignment_source_id(person.source_id, "party", biennium),
            person_id=person.id,
            role_id=role.id,
            valid_from=valid_from,
        )
    )


async def _emit_chamber(
    collector: EntityCollector,
    session: AsyncSession,
    member: dict,
    person,
    anchors: BootstrapAnchors,
    biennium: str,
    valid_from: date,
) -> None:
    """Senate → seat Role + seat Assignment; House → nothing (deferred whole to #69)."""
    agency = member.get("Agency")
    if agency == "Senate":
        ld_number = district_number(member.get("District"))
        if ld_number is None:
            logger.warning(
                "wsl_sponsor_senate_seat_no_district",
                extra={"member_id": member.get("Id"), "district": member.get("District")},
            )
            return
        jurisdiction = await resolve_ld_jurisdiction(session, ld_number)
        if jurisdiction is None:
            logger.warning(
                "wsl_sponsor_senate_seat_unresolved_ld",
                extra={"member_id": member.get("Id"), "district": member.get("District")},
            )
            return
        seat_role = await get_or_create_role(
            session,
            source_id=senate_seat_role_source_id(ld_number),
            organization_id=anchors.senate_id,
            name=_SENATE_SEAT_ROLE_NAME,
            role_type=_SENATE_SEAT_ROLE_TYPE,
            jurisdiction_id=jurisdiction.id,
            qualifier=None,
        )
        collector.add(seat_role)
        collector.add(
            build_assignment(
                source_id=assignment_source_id(person.source_id, "chamber-senate", biennium),
                person_id=person.id,
                role_id=seat_role.id,
                valid_from=valid_from,
            )
        )
    elif agency == "House":
        # House chamber Assignment is #69's alone (needs Position; created fresh there).
        # Fires for every House member every run — debug, not INFO.
        logger.debug("wsl_house_chamber_deferred_to_69", extra={"member_id": member.get("Id")})
