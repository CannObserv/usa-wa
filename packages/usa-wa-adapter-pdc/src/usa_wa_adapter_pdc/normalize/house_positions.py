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

**Mid-biennium replacement inference (#74).** A member who *won* a House seat in the base
election but then moved to the Senate mid-biennium is absent from the House roster (their
WSL House row is a name-blanked stub), so their PDC winner row matches no one and is
deferred. The replacement who now holds the vacated seat is a sitting House member with a
known district but no PDC winner row (appointed). A second reconciliation pass recovers
this by **within-LD elimination**: if an LD has exactly one deferred winner and exactly one
unmatched roster member, and the deferred winner **reappears as that LD's sitting Senator**
(the confirming signal that the vacancy is a genuine chamber-move, not a name-match miss),
the unmatched member is assigned the deferred position. Such a seat is **inferred**, not
PDC-declared — it carries no `person_wa_pdc` identifier and a reduced-confidence
:class:`FactCitation`, and logs `pdc_house_seat_inferred`. Ambiguous cases (both LD reps
moved the same biennium → two deferrals) fall through to `pdc_house_unresolved`.

The mover's own PDC winner identity is theirs even though they left the House, so it is
**cross-linked** onto their current (Senate) Person as a `person_wa_pdc` identifier — the
same cross-link a directly-seated winner gets — independent of whether the replacement's
seat could be inferred.

**Session-aware** (like the WSL member normalizers): it SELECTs the existing Person and
get-or-creates the shared seat Role, flushing for the real ids the leaf rows' FKs need
(the runner cannot resolve an intra-batch FK). The WSL House + Senate rosters are built
from a `GetSponsors` pull (House members' districts aren't stored locally post-decoupling);
the caller supplies them via :func:`build_house_roster` / :func:`build_senate_roster`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.adapter import FactCitation, FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Assignment, PersonIdentifier
from usa_wa_adapter_legislature.normalize.members import (
    EntityCollector,
    canonicalize_party,
    district_number,
    get_or_create_role,
    resolve_ld_jurisdiction,
)
from usa_wa_adapter_legislature.synthesis import parse_biennium
from usa_wa_adapter_pdc.normalize.persons import resolve_wsl_person
from usa_wa_adapter_pdc.normalize.positions import (
    PDC_PERSON_ID_SCHEME,
    PDC_SOURCE,
    canonical_position,
    fold_token,
    house_seat_assignment_source_id,
    house_seat_role_source_id,
    pdc_person_identifier_source_id,
    surname_match_set,
)

logger = get_logger(__name__)

#: PDC-provenance rows (identifier + assignment) carry ``PDC_SOURCE``; the structural seat
#: Role stays ``usa_wa_legislature`` (via ``get_or_create_role``), symmetric with the Senate
#: seat Role and matching PM's structural seat.
_HOUSE_SEAT_ROLE_TYPE = "state_representative"
_HOUSE_SEAT_ROLE_NAME = "State Representative"

#: Confidence stamped on the FactCitation for an *inferred* replacement seat (#74) — the
#: position is deduced by elimination, not observed in PDC, so its provenance is weaker
#: than the directly-sourced seats (which ride the runner's default full-confidence cite).
_INFERRED_CONFIDENCE = 0.5


@dataclass(frozen=True)
class HouseRosterEntry:
    """One WSL House member for the within-LD match: the stable member id, the folded
    surname tested against a winner's name tokens, and the party for a tiebreak."""

    member_id: str
    folded_last: str
    party_slug: str | None


@dataclass(frozen=True)
class SenateEntry:
    """One WSL Senator for the #74 confirming signal — the stable member id (to cross-link
    a mover's PDC id onto their current Person) + the folded surname (to match a deferred
    House winner who moved to this LD's Senate seat)."""

    member_id: str
    folded_last: str


@dataclass(frozen=True)
class _Deferred:
    """A PDC winner that matched no House roster member — a mid-biennium replacement
    inference candidate (#74). Carries the PDC ``person_id`` so a confirmed mover's identity
    can be cross-linked onto their current (Senate) Person."""

    qualifier: str
    filer_name: str
    pdc_person_id: str


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


def build_senate_roster(sponsor_members: list[dict[str, Any]]) -> dict[int, list[SenateEntry]]:
    """``{LD: [SenateEntry]}`` — the confirming signal for the #74 replacement inference. A
    deferred House winner who reappears as their LD's sitting Senator is a genuine
    mid-biennium House→Senate mover, which *explains* the vacated House seat; the entry's
    member id lets us cross-link the mover's PDC identity onto their current Person."""
    out: dict[int, list[SenateEntry]] = {}
    for member in sponsor_members:
        if member.get("Agency") != "Senate":
            continue
        last = (member.get("LastName") or "").strip()
        ld = district_number(member.get("District"))
        if not last or ld is None:
            continue
        out.setdefault(ld, []).append(
            SenateEntry(member_id=str(member["Id"]), folded_last=fold_token(last))
        )
    return out


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


def _find_confirming_senator(
    filer_name: str, ld: int, senate_roster: dict[int, list[SenateEntry]]
) -> SenateEntry | None:
    """The LD's Senator whose folded surname matches a deferred House winner — the genuine
    mid-biennium House→Senate mover that explains the vacant House seat (#74). Without this
    signal an unmatched winner could be a name-match miss, so we don't infer. Returns the
    single matching Senator (so their id can carry the mover's PDC cross-link), or ``None``
    when there is no unique match."""
    keys = surname_match_set(filer_name)
    matches = [s for s in senate_roster.get(ld, []) if s.folded_last in keys]
    return matches[0] if len(matches) == 1 else None


async def normalize_house_positions(
    payload: FetchedPayload,
    *,
    house_roster: dict[int, list[HouseRosterEntry]],
    anchors: Any,
    session: AsyncSession,
    biennium: str,
    senate_roster: dict[int, list[SenateEntry]] | None = None,
) -> NormalizedBatch:
    """Emit `person_wa_pdc` identifiers + House seat Assignments for the PDC winner cohort.

    ``anchors`` is the WSL :class:`~usa_wa_adapter_legislature.bootstrap.BootstrapAnchors`
    (the House org id is the seat Role's organization); ``biennium`` scopes the Assignment
    (``valid_from`` = Jan 1 of the odd start year). ``senate_roster`` (``{LD: [SenateEntry]}``,
    from :func:`build_senate_roster`) enables the #74 mid-biennium replacement inference +
    mover cross-link; omitted → no inference (a deferred winner just logs
    `pdc_house_unresolved`).

    **Inferred-seat provenance (#74).** The runner still writes its default full-confidence
    whole-entity Citation for an inferred Assignment; the *inference* is signalled only at
    field level — a reduced-confidence :class:`FactCitation` on the assignment's ``role_id``
    plus a `pdc_house_seat_inferred` log — not in the whole-entity confidence."""
    winners = payload.parsed or []
    senate_roster = senate_roster or {}
    start_year, _ = parse_biennium(biennium)
    valid_from = date(start_year, 1, 1)
    collector = EntityCollector()
    citations: list[FactCitation] = []
    seen_members: set[str] = set()
    #: LD → resolved Jurisdiction, or ``None`` sentinel for an unsynced LD (cached so an
    #: unsynced LD is resolved + logged once, not once per winner).
    resolved_ld: dict[int, Any] = {}
    deferred: dict[int, list[_Deferred]] = {}
    direct_seated = inferred_seated = movers_linked = unresolved = 0
    incomplete = unresolved_ld = 0

    # Phase 1 — direct match of each winner to a House roster member.
    for row in winners:
        pdc_id = str(row.get("person_id") or "").strip()
        qualifier = canonical_position(row.get("position"))
        ld = district_number(row.get("legislative_district"))
        if not pdc_id or qualifier is None or ld is None:
            incomplete += 1
            logger.warning(
                "pdc_house_row_incomplete",
                extra={"person_id": pdc_id, "position": row.get("position"), "ld": ld},
            )
            continue

        if ld not in resolved_ld:
            resolved_ld[ld] = await resolve_ld_jurisdiction(session, ld)
            if resolved_ld[ld] is None:
                logger.warning("pdc_house_unresolved_ld", extra={"ld": ld})
        jurisdiction = resolved_ld[ld]
        if jurisdiction is None:
            unresolved_ld += 1
            continue

        match = _match_member(
            house_roster,
            ld,
            surname_match_set(row.get("filer_name") or ""),
            canonicalize_party(row.get("party_code")),
        )
        if match is None:
            # No roster member — a mid-biennium replacement inference candidate (#74).
            deferred.setdefault(ld, []).append(
                _Deferred(
                    qualifier=qualifier,
                    filer_name=row.get("filer_name") or "",
                    pdc_person_id=pdc_id,
                )
            )
            continue
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
            continue

        emitted = await _emit_seat_rows(
            collector,
            citations,
            session,
            member_id=match.member_id,
            ld=ld,
            jurisdiction=jurisdiction,
            qualifier=qualifier,
            anchors=anchors,
            biennium=biennium,
            valid_from=valid_from,
            pdc_person_id=pdc_id,
            inferred=False,
        )
        if emitted:
            seen_members.add(match.member_id)
            direct_seated += 1

    # Phase 2 — reconcile mid-biennium replacements by within-LD elimination (#74).
    for ld, deferrals in deferred.items():
        unmatched = [m for m in house_roster.get(ld, []) if m.member_id not in seen_members]

        # Cross-link each confirmed mover's PDC identity onto their current (Senate) Person —
        # their PDC winner row is theirs even though they no longer hold the House seat.
        movers = [
            (d, senator)
            for d in deferrals
            if (senator := _find_confirming_senator(d.filer_name, ld, senate_roster)) is not None
        ]
        for deferral, senator in movers:
            await _link_pdc_identifier(
                collector, session, senator.member_id, deferral.pdc_person_id
            )
            movers_linked += 1

        # Seat inference: exactly one deferred position + one unmatched member, and that
        # deferral is a confirmed mover (so the vacancy is explained). Attempting the
        # reconcile suppresses the generic unresolved log — a person-absent replacement is
        # surfaced by `pdc_house_person_absent`, not a second line.
        attempted = len(deferrals) == 1 and len(unmatched) == 1 and len(movers) == 1
        seated = False
        if attempted:
            seated = await _emit_seat_rows(
                collector,
                citations,
                session,
                member_id=unmatched[0].member_id,
                ld=ld,
                jurisdiction=resolved_ld[ld],
                qualifier=deferrals[0].qualifier,
                anchors=anchors,
                biennium=biennium,
                valid_from=valid_from,
                pdc_person_id=None,
                inferred=True,
            )
            if seated:
                seen_members.add(unmatched[0].member_id)
                inferred_seated += 1
        if not attempted:
            for deferral in deferrals:
                unresolved += 1
                logger.info(
                    "pdc_house_unresolved",
                    extra={
                        "ld": ld,
                        "position": deferral.qualifier,
                        "filer_name": deferral.filer_name,
                    },
                )

    logger.info(
        "pdc_house_summary",
        extra={
            "winners": len(winners),
            "direct_seated": direct_seated,
            "inferred_seated": inferred_seated,
            "movers_linked": movers_linked,
            "unresolved": unresolved,
            "unresolved_ld": unresolved_ld,
            "incomplete": incomplete,
        },
    )
    return NormalizedBatch(entities=collector.entities, citations=citations)


async def _link_pdc_identifier(
    collector: EntityCollector, session: AsyncSession, member_id: str, pdc_person_id: str
) -> None:
    """Attach a `person_wa_pdc` child identifier (value = ``pdc_person_id``) to the WSL
    :class:`Person` ``member_id`` — the mover cross-link (#74): a House→Senate mover's PDC
    winner identity is theirs even though they no longer hold the House seat, so it rides
    their current Person the same way a directly-seated winner's does. No-op if the Person
    isn't ingested yet."""
    person = await resolve_wsl_person(session, member_id)
    if person is None:
        logger.warning("pdc_mover_person_absent", extra={"member_id": member_id})
        return
    collector.add(
        PersonIdentifier(
            source=PDC_SOURCE,
            source_id=pdc_person_identifier_source_id(pdc_person_id),
            person_id=person.id,
            scheme=PDC_PERSON_ID_SCHEME,
            value=pdc_person_id,
        )
    )


async def _emit_seat_rows(
    collector: EntityCollector,
    citations: list[FactCitation],
    session: AsyncSession,
    *,
    member_id: str,
    ld: int,
    jurisdiction: Any,
    qualifier: str,
    anchors: Any,
    biennium: str,
    valid_from: date,
    pdc_person_id: str | None,
    inferred: bool,
) -> bool:
    """Emit the seat Role + (for a directly-matched winner) `person_wa_pdc` identifier + a
    chamber seat Assignment for ``member_id``. Returns ``False`` (no rows) when the WSL
    Person isn't ingested yet. An ``inferred`` seat (#74) carries no PDC identifier
    (``pdc_person_id`` is ``None``) and a reduced-confidence :class:`FactCitation`."""
    person = await resolve_wsl_person(session, member_id)
    if person is None:
        logger.warning(
            "pdc_house_person_absent",
            extra={"member_id": member_id, "ld": ld, "position": qualifier},
        )
        return False

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
    if pdc_person_id:
        collector.add(
            PersonIdentifier(
                source=PDC_SOURCE,
                source_id=pdc_person_identifier_source_id(pdc_person_id),
                person_id=person.id,
                scheme=PDC_PERSON_ID_SCHEME,
                value=pdc_person_id,
            )
        )
    assignment = Assignment(
        source=PDC_SOURCE,
        source_id=house_seat_assignment_source_id(member_id, biennium),
        person_id=person.id,
        role_id=seat_role.id,
        valid_from=valid_from,
        is_active=True,
    )
    collector.add(assignment)
    if inferred:
        logger.info(
            "pdc_house_seat_inferred",
            extra={"member_id": member_id, "ld": ld, "position": qualifier},
        )
        # The position is deduced by elimination, not PDC-observed — record the weaker
        # provenance on the role binding (the seat's defining fact).
        citations.append(
            FactCitation(entity=assignment, field_path="role_id", confidence=_INFERRED_CONFIDENCE)
        )
    return True
