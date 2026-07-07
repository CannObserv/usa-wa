"""House-position normalizer — PDC winners → ``person_wa_pdc`` + House seat Assignment.

The one thing WSL cannot supply (#69): a House member's ballot **Position** (1 / 2), the
`qualifier` on Power Map's `state_representative` seat Role. PDC's seated-winner cohort
carries it. This normalizer is a **Position resolver**, not a Person source: each PDC
winner is matched to the *existing* WSL :class:`Person` (created by P1b, keyed on the WSL
member id) — within its LD, by folded last name (+ party tiebreak) — and then:

- a `person_wa_pdc` child :class:`PersonIdentifier` is attached to that WSL Person (the
  person descriptor carries it to PM as an `additional_identifier`, cross-linking PDC and
  WSL on one PM person — deterministic, no name-match reliance);
- the House `state_representative` seat :class:`Role` (`qualifier` = Position N) is
  get-or-created (source `usa_wa_legislature`, symmetric with the Senate seat Role P1b
  emits — a seat is legislature structure);
- a chamber seat :class:`Assignment` binds the Person to that seat Role.

**Session-aware** (like the WSL member normalizers): it SELECTs the existing Person and
get-or-creates the shared seat Role, flushing for the real ids the leaf rows' FKs need
(the runner cannot resolve an intra-batch FK). The WSL House roster — the
`(LD, folded-last) → member id` map used for the match — is built from a `GetSponsors`
pull (House members' districts aren't stored locally post-decoupling); the caller supplies
it via :func:`build_house_roster`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier
from usa_wa_adapter_legislature.normalize.members import (
    EntityCollector,
    canonicalize_party,
    district_number,
    get_or_create_role,
    resolve_ld_jurisdiction,
)
from usa_wa_adapter_legislature.synthesis import parse_biennium
from usa_wa_adapter_pdc.normalize.positions import (
    PDC_PERSON_ID_SCHEME,
    canonical_position,
    fold_token,
    house_seat_assignment_source_id,
    house_seat_role_source_id,
    pdc_person_identifier_source_id,
    surname_match_set,
)

logger = get_logger(__name__)

#: PDC-provenance rows (identifier + assignment) carry the PDC source; the structural
#: seat Role stays ``usa_wa_legislature`` (via ``get_or_create_role``), symmetric with the
#: Senate seat Role and matching PM's structural seat.
_SOURCE = "usa_wa_pdc"
_WSL_SOURCE = "usa_wa_legislature"
_HOUSE_SEAT_ROLE_TYPE = "state_representative"
_HOUSE_SEAT_ROLE_NAME = "State Representative"


@dataclass(frozen=True)
class HouseRosterEntry:
    """One WSL House member for the within-LD match: the stable member id, the folded
    surname tested against a winner's name tokens, and the party for a tiebreak."""

    member_id: str
    folded_last: str
    party_slug: str | None


def build_house_roster(sponsor_members: list[dict[str, Any]]) -> dict[int, list[HouseRosterEntry]]:
    """Group WSL ``GetSponsors`` **House** rows by LD number for the winner match.

    Only House rows with a parseable district + last name participate (Senate rows,
    name-blanked stubs, and blank districts are skipped — they can't seat a House member)."""
    roster: dict[int, list[HouseRosterEntry]] = {}
    for member in sponsor_members:
        if member.get("Agency") != "House":
            continue
        last = (member.get("LastName") or "").strip()
        ld = district_number(member.get("District"))
        if not last or ld is None:
            continue
        roster.setdefault(ld, []).append(
            HouseRosterEntry(
                member_id=str(member["Id"]),
                folded_last=fold_token(last),
                party_slug=canonicalize_party(member.get("Party")),
            )
        )
    return roster


def _match_member(
    roster: dict[int, list[HouseRosterEntry]],
    ld: int,
    winner_tokens: set[str],
    winner_party: str | None,
) -> HouseRosterEntry | None:
    """Resolve a PDC winner to a WSL House member in its LD: the member whose folded
    surname is among the winner's name tokens; a shared surname is broken by party. A
    zero-or-ambiguous match returns ``None`` (the winner is left unresolved, not guessed)."""
    candidates = [e for e in roster.get(ld, []) if e.folded_last in winner_tokens]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1 and winner_party is not None:
        by_party = [e for e in candidates if e.party_slug == winner_party]
        if len(by_party) == 1:
            return by_party[0]
    return None


async def normalize_house_positions(
    payload: FetchedPayload,
    *,
    house_roster: dict[int, list[HouseRosterEntry]],
    anchors: Any,
    session: AsyncSession,
    biennium: str,
) -> NormalizedBatch:
    """Emit `person_wa_pdc` identifiers + House seat Assignments for the PDC winner cohort.

    ``anchors`` is the WSL :class:`~usa_wa_adapter_legislature.bootstrap.BootstrapAnchors`
    (the House org id is the seat Role's organization); ``biennium`` scopes the Assignment
    (``valid_from`` = Jan 1 of the odd start year)."""
    winners = payload.parsed or []
    start_year, _ = parse_biennium(biennium)
    valid_from = date(start_year, 1, 1)
    collector = EntityCollector()
    # Guard against one WSL member being resolved by two winners in the same batch (a
    # pathological surname-token overlap): the two Assignments share the member-keyed
    # source_id and would silently dedup, so warn + skip the second rather than mint a
    # stray positioned seat Role with no assignment and leave the real member unseated.
    seen_members: set[str] = set()
    for row in winners:
        await _emit_seat(
            collector, session, row, house_roster, anchors, biennium, valid_from, seen_members
        )
    return NormalizedBatch(entities=collector.entities)


async def _emit_seat(
    collector: EntityCollector,
    session: AsyncSession,
    row: dict[str, Any],
    roster: dict[int, list[HouseRosterEntry]],
    anchors: Any,
    biennium: str,
    valid_from: date,
    seen_members: set[str],
) -> None:
    pdc_id = str(row.get("person_id") or "").strip()
    qualifier = canonical_position(row.get("position"))
    ld = district_number(row.get("legislative_district"))
    if not pdc_id or qualifier is None or ld is None:
        logger.warning(
            "pdc_house_row_incomplete",
            extra={"person_id": pdc_id, "position": row.get("position"), "ld": ld},
        )
        return

    jurisdiction = await resolve_ld_jurisdiction(session, ld)
    if jurisdiction is None:
        logger.warning("pdc_house_unresolved_ld", extra={"person_id": pdc_id, "ld": ld})
        return

    match = _match_member(
        roster,
        ld,
        surname_match_set(row.get("filer_name") or ""),
        canonicalize_party(row.get("party_code")),
    )
    if match is None:
        logger.info(
            "pdc_house_unresolved",
            extra={"ld": ld, "position": qualifier, "filer_name": row.get("filer_name")},
        )
        return

    if match.member_id in seen_members:
        logger.warning(
            "pdc_house_member_double_matched",
            extra={
                "member_id": match.member_id,
                "ld": ld,
                "position": qualifier,
                "filer_name": row.get("filer_name"),
            },
        )
        return

    person = (
        await session.execute(
            select(Person).where(Person.source == _WSL_SOURCE, Person.source_id == match.member_id)
        )
    ).scalar_one_or_none()
    if person is None:
        logger.warning(
            "pdc_house_person_absent",
            extra={"member_id": match.member_id, "ld": ld, "position": qualifier},
        )
        return
    # Mark the member seated only after the Person is confirmed — an absent-Person first
    # winner must not poison a second winner's signal into a spurious double-match warning.
    seen_members.add(match.member_id)

    seat_role = await get_or_create_role(
        session,
        source_id=house_seat_role_source_id(ld, qualifier),
        organization_id=anchors.house_id,
        name=_HOUSE_SEAT_ROLE_NAME,
        role_type=_HOUSE_SEAT_ROLE_TYPE,
        jurisdiction_id=jurisdiction.id,
        qualifier=qualifier,
    )
    collector.add(seat_role)
    collector.add(
        PersonIdentifier(
            source=_SOURCE,
            source_id=pdc_person_identifier_source_id(pdc_id),
            person_id=person.id,
            scheme=PDC_PERSON_ID_SCHEME,
            value=pdc_id,
        )
    )
    collector.add(
        Assignment(
            source=_SOURCE,
            source_id=house_seat_assignment_source_id(match.member_id, biennium),
            person_id=person.id,
            role_id=seat_role.id,
            valid_from=valid_from,
            is_active=True,
        )
    )
