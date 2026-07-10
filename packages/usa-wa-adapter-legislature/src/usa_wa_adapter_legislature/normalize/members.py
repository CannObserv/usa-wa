"""Shared member-cluster helpers for the WSL sponsor + committee-member normalizers.

``SponsorService.GetSponsors`` and ``CommitteeService.GetActiveCommitteeMembers`` return
the same ``Member`` shape (``Id, Name, LongName, Agency, Party, District, FirstName,
LastName`` — plus ``Phone``/``Email``). Both normalizers build the same canonical rows —
:class:`Person`, :class:`PersonIdentifier`, :class:`Role`, :class:`Assignment` — so the
identity-resolution logic lives here once.

**Why these helpers touch the session.** The :class:`AdapterRunner` upserts each entity
independently by natural key and reads its id back, so it cannot resolve an intra-batch
FK — an ``Assignment`` needs a *real* ``person_id`` / ``role_id`` before it is written.
:func:`get_or_create_person` / :func:`get_or_create_role` therefore SELECT-or-INSERT
against the session (flushing a new row to obtain its id) so the ``Assignment`` the
normalizer builds carries the persisted ids. The runner then re-upserts each returned
entity idempotently (ON CONFLICT on the natural key) and writes its provenance Citation.
Leaf rows (``PersonIdentifier`` / ``Assignment``) are keyed deterministically, so they
are built with resolved FK ids and left for the runner to upsert — no get-or-create.

Party encoding differs by endpoint (step 0 finding): the sponsor endpoint sends
``"R"``/``"D"``, the committee endpoint ``"Republican"``/``"Democrat"``.
:func:`canonicalize_party` folds both to the bare slug (``republican`` / ``democratic``);
an independent / blank / unknown value yields ``None`` → no party Assignment.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import (
    Assignment,
    Person,
    PersonIdentifier,
    Role,
)

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"


class EntityCollector:
    """Accumulate canonical entities for a :class:`NormalizedBatch`, deduped by
    ``(type, source_id)`` so a shared Role (a party's Member role) or a member seen
    twice (a mid-biennium chamber mover) appears once — one row, one Citation."""

    def __init__(self) -> None:
        self._entities: list[Any] = []
        self._seen: set[tuple[str, str]] = set()

    def add(self, entity: Any) -> None:
        key = (type(entity).__name__, entity.source_id)
        if key in self._seen:
            return
        self._seen.add(key)
        self._entities.append(entity)

    @property
    def entities(self) -> list[Any]:
        return self._entities


#: Local ``PersonIdentifier.scheme`` for the WSL member id (spec 2026-06-18). The PM
#: identifier_type the person descriptor emits from ``Person.source_id`` is the sibling
#: ``person_wa_legislature_member_id``; this local child row is the queryable N-scheme
#: graph (bill sponsorships in P1c join on it).
MEMBER_ID_SCHEME = "wa_legislature_member_id"

#: ``Party`` (either encoding) → canonical party slug. Anything not here (independent,
#: blank, a non-person stub) → ``None`` → no party Assignment (power-map#270).
_PARTY_CANON = {
    "r": "republican",
    "republican": "republican",
    "d": "democratic",
    "democrat": "democratic",
    "democratic": "democratic",
}


def is_person(member: dict[str, Any]) -> bool:
    """True when a ``Member`` row is a named legislator (both first + last present).

    Filters the name-blanked stubs ``GetSponsors`` returns for a superseded / departed
    (member, chamber-tenure) — a real ``Id`` but no name/district/party (step 0 finding)."""
    return bool((member.get("FirstName") or "").strip() and (member.get("LastName") or "").strip())


def canonicalize_party(raw: str | None) -> str | None:
    """Fold a WSL ``Party`` value (either endpoint encoding) to a canonical slug.

    ``"R"``/``"Republican"`` → ``republican``; ``"D"``/``"Democrat"``/``"Democratic"`` →
    ``democratic``. Independent / blank / unknown → ``None`` (no party Assignment)."""
    if not raw:
        return None
    return _PARTY_CANON.get(raw.strip().lower())


def member_source_id(member: dict[str, Any]) -> str:
    """The canonical ``Person.source_id`` for a member — the stable WSL ``Id`` as a string
    (cross-endpoint / cross-biennium / cross-chamber stable, step 0)."""
    return str(member["Id"])


def district_number(district: str | None) -> int | None:
    """Parse a WSL ``District`` (e.g. ``"33"``, ``" 5 "``) to its LD number, or ``None``
    for a blank/malformed value (no district → no seat). The single parse site — both the
    LD slug and the Senate seat's ``source_id`` derive from this."""
    if district is None:
        return None
    text = district.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def ld_slug(district: str | None) -> str | None:
    """WSL ``District`` → the local LD jurisdiction slug ``usa-wa-ld-<n>`` (unpadded,
    matching the synced PM jurisdictions), or ``None`` for a blank/malformed district."""
    number = district_number(district)
    return f"usa-wa-ld-{number}" if number is not None else None


def party_role_source_id(slug: str) -> str:
    """Deterministic ``source_id`` for a party's ``Member`` Role (one per party Org)."""
    return f"party-role:{slug}"


def committee_member_role_source_id(committee_source_id: str) -> str:
    """Deterministic ``source_id`` for a committee's ``Member`` Role (one per committee)."""
    return f"committee-member-role:{committee_source_id}"


def senate_seat_role_source_id(ld_number: int) -> str:
    """Deterministic ``source_id`` for a Senate seat Role (one per LD)."""
    return f"seat:senate:ld-{ld_number}"


def assignment_source_id(member_id: str, dimension: str, biennium: str) -> str:
    """Deterministic ``Assignment.source_id`` — role-independent (the role is a *value*
    of the assignment, not part of the key, so a role correction needs no new row).

    ``dimension`` ∈ {``chamber-senate``, ``party``, ``committee:<committee_source_id>``}."""
    return f"{member_id}:{dimension}:{biennium}"


def build_person(member: dict[str, Any]) -> Person:
    """Construct a :class:`Person` from a member row (name recomposed from first+last)."""
    first = (member.get("FirstName") or "").strip()
    last = (member.get("LastName") or "").strip()
    full = f"{first} {last}".strip()
    long_name = (member.get("LongName") or "").strip()
    return Person(
        source=_SOURCE,
        source_id=member_source_id(member),
        name_full=full,
        name_first=first or None,
        name_last=last or None,
        # LongName is the honorific display form ("Senator Rivers"); keep it as the
        # used-name when it differs. PM curates the display name on match (person
        # descriptor adopts PM's ``display_name``), so this is a low-stakes local hint.
        name_used=long_name if long_name and long_name != full else None,
    )


def build_person_identifier(person: Person, member: dict[str, Any]) -> PersonIdentifier:
    """Construct the WSL-member-id :class:`PersonIdentifier` for a person (requires
    ``person.id`` resolved). Deterministic keys, so the runner upserts it idempotently."""
    value = member_source_id(member)
    return PersonIdentifier(
        source=_SOURCE,
        source_id=f"{value}:{MEMBER_ID_SCHEME}",
        person_id=person.id,
        scheme=MEMBER_ID_SCHEME,
        value=value,
    )


async def get_or_create_person(session: AsyncSession, member: dict[str, Any]) -> Person:
    """SELECT the Person by ``(source, source_id)``; INSERT + flush a new one if absent.

    Flushing a new row obtains its ``id`` so the caller can wire ``Assignment.person_id``
    (the runner cannot resolve that intra-batch FK). Idempotent within a run: a member
    appearing twice (a mid-biennium chamber mover's two named rows) resolves to the same
    Person on the second call."""
    source_id = member_source_id(member)
    existing = (
        await session.execute(
            select(Person).where(Person.source == _SOURCE, Person.source_id == source_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    person = build_person(member)
    session.add(person)
    await session.flush()
    return person


async def get_or_create_role(
    session: AsyncSession,
    *,
    source_id: str,
    organization_id: _ULID,
    name: str,
    role_type: str,
    jurisdiction_id: _ULID | None = None,
    qualifier: str | None = None,
) -> Role:
    """SELECT the Role by ``(source, source_id)``; INSERT + flush a new one if absent.

    ``source_id`` is deterministic per structural identity (a party's/committee's Member
    role, or a Senate seat's LD), so it aligns 1:1 with PM's seat/title match keys — the
    SELECT finds the existing row before an INSERT could collide with the partial unique
    seat/title index. Flushing yields the ``id`` for ``Assignment.role_id`` wiring."""
    existing = (
        await session.execute(
            select(Role).where(Role.source == _SOURCE, Role.source_id == source_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    role = Role(
        source=_SOURCE,
        source_id=source_id,
        organization_id=organization_id,
        name=name,
        role_type=role_type,
        jurisdiction_id=jurisdiction_id,
        qualifier=qualifier,
    )
    session.add(role)
    await session.flush()
    return role


async def resolve_ld_jurisdiction(session: AsyncSession, ld_number: int) -> Jurisdiction | None:
    """Resolve an LD number to its local LD :class:`Jurisdiction` (or ``None`` if that LD
    isn't synced locally — a seat then can't be keyed and is skipped, the Person/party
    still land). Takes the pre-parsed number (see :func:`district_number`) so the caller
    parses the WSL ``District`` once."""
    return (
        await session.execute(
            select(Jurisdiction).where(Jurisdiction.slug == f"usa-wa-ld-{ld_number}")
        )
    ).scalar_one_or_none()


def build_assignment(
    *,
    source_id: str,
    person_id: _ULID,
    role_id: _ULID,
    valid_from: Any,
) -> Assignment:
    """Construct an active :class:`Assignment` (leaf row — resolved FK ids, no
    get-or-create; the runner upserts it idempotently on ``(source, source_id)``)."""
    return Assignment(
        source=_SOURCE,
        source_id=source_id,
        person_id=person_id,
        role_id=role_id,
        valid_from=valid_from,
        is_active=True,
    )


async def normalize_member_persons(
    payload: FetchedPayload,
    *,
    session: AsyncSession,
) -> NormalizedBatch:
    """Member rows → the Person cluster (:class:`Person` + ``wa_legislature_member_id``
    :class:`PersonIdentifier`), and nothing else.

    Shared by every WSL roster whose payload is a flat ``Member`` list: ``GetSponsors``
    (#78-2c) and the historical ``GetCommitteeMembers`` (#82). Tenure — party, chamber
    seat, committee membership — is **not** emitted per-biennium; it is archive-derived
    merged spans built by the span engine. Name-blanked stubs are skipped; Persons dedup
    across biennia and endpoints by the stable WSL ``Id`` (#81)."""
    members = payload.parsed if payload.parsed is not None else json.loads(payload.body.decode())

    collector = EntityCollector()
    for member in members:
        if not is_person(member):
            # Expected per run (name-blanked departed/superseded tenure stubs) — debug.
            logger.debug(
                "wsl_member_skip_non_person",
                extra={"member_id": member.get("Id"), "agency": member.get("Agency")},
            )
            continue
        person = await get_or_create_person(session, member)
        collector.add(person)
        collector.add(build_person_identifier(person, member))

    return NormalizedBatch(entities=collector.entities)
