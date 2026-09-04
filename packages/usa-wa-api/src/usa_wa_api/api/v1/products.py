"""Products slice of ``/api/v1`` (#184, flipped to the serving tier at #313).

Persons, organizations, roles and spans — served from ``serving.*``, the
deployment's own projection of the datasets it publishes. This is the point of
the #302 replatform where the API stops being a second reader of the canonical
Postgres tables and becomes **the first consumer of its own datapackage
contract**: every row here was published, hashed, versioned and loaded back.

**There is no spans resource.** A tenure span *is* an assignment
(``docs/ONTOLOGY.md`` § 2), so ``/assignments`` is the span route. Since #313 the
span key's parts are real columns, so ``span_kind`` filters a column instead of
splitting a string — which is what retires #335.

**No lifecycle filtering, and no ``include_hidden``.** The tombstones are gone
with the tables that carried them: a row the pipeline no longer asserts is
simply absent (retraction-as-absence, the #302 publication contract), and a
person the registry merged away is reachable through the crosswalk's
``merged_into`` rather than through an archived row. There is nothing left to
hide, so there is no escape hatch to offer.

**Detail routes still answer for rows a list route would not show.** A caller
holding an id gets that row — the reason they hold the id is usually that it
went quiet.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from usa_wa_api.api.deps import get_db_session
from usa_wa_api.api.v1.pagination import (
    DEFAULT_LIMIT,
    CursorQuery,
    LimitQuery,
    Page,
    decode_cursor,
    encode_cursor,
    take_page,
)
from usa_wa_api.api.v1.schemas import (
    AssignmentDetail,
    AssignmentSummary,
    CitationOut,
    OrganizationOut,
    PersonCrosswalkOut,
    PersonDetail,
    PersonSummary,
    RoleOut,
    ULIDPath,
    split_span_key,
)
from usa_wa_api.serving.schema import (
    Assignment,
    Citation,
    Organization,
    Person,
    PersonCrosswalk,
    RawFetch,
    Role,
)

router = APIRouter(tags=["products"])

EMBEDDED_CITATION_LIMIT = DEFAULT_LIMIT
"""How many citations ride along on an assignment detail response. An embedded
collection needs a bound or the detail route becomes the unpaginated list route
this API does not have; the full chain is paginated at
``/provenance/assignment/{id}``."""

#: The five columns that *are* a span's identity, in cursor order.
_SPAN_KEY_COLUMNS = (
    Assignment.source,
    Assignment.member_id,
    Assignment.span_kind,
    Assignment.span_discriminator,
    Assignment.span_start_biennium,
)


def _keyset(stmt: Select, column, *, cursor: str | None, limit: int) -> Select:
    """Apply the ascending single-column keyset predicate, ordering and over-fetch.

    The cursor is the key value itself. Registry ULIDs sort lexicographically by
    creation time, so for the entity routes this is still the natural order; for
    ``roles`` the key is the structural ``role_key``, which sorts by seat.
    """
    if cursor is not None:
        stmt = stmt.where(column > cursor)
    return stmt.order_by(column.asc()).limit(limit + 1)


async def _page(session: AsyncSession, stmt: Select, model, *, key_of, limit: int) -> Page:
    rows = list((await session.execute(stmt)).scalars().all())
    page, next_cursor = take_page(rows, limit=limit, key_of=key_of)
    return Page(
        items=[model.model_validate(row) for row in page],
        limit=limit,
        next_cursor=next_cursor,
    )


async def _get_or_404(session: AsyncSession, model, column, value: str, label: str):
    row = await session.scalar(select(model).where(column == value))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no {label} with id {value}"
        )
    return row


# --------------------------------------------------------------------------- #
# Persons
# --------------------------------------------------------------------------- #


@router.get("/persons", response_model=Page[PersonSummary])
async def list_persons(
    session: AsyncSession = Depends(get_db_session),
    source: str | None = Query(
        default=None,
        description=(
            "Filter to people the registry knows under this key namespace "
            "(`usa_wa_legislature`, `usa_wa_legislature_roster`, `wa_pdc`). Since #313 a "
            "person is multi-source by construction, so this asks *known to* rather than "
            "*originated from*."
        ),
    ),
    name_contains: str | None = Query(
        default=None, min_length=2, description="Case-insensitive substring of `name_full`."
    ),
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[PersonSummary]:
    """People, oldest entity id first. The cursor is a person `entity_id`."""
    stmt = select(Person)
    if source is not None:
        stmt = stmt.where(
            Person.entity_id.in_(
                select(PersonCrosswalk.entity_id).where(PersonCrosswalk.key_namespace == source)
            )
        )
    if name_contains is not None:
        stmt = stmt.where(Person.name_full.ilike(f"%{name_contains}%"))
    return await _page(
        session,
        _keyset(stmt, Person.entity_id, cursor=cursor, limit=limit),
        PersonSummary,
        key_of=lambda row: row.entity_id,
        limit=limit,
    )


@router.get("/persons/{person_id}", response_model=PersonDetail)
async def get_person(
    person_id: ULIDPath,
    session: AsyncSession = Depends(get_db_session),
) -> PersonDetail:
    """One person with every natural key the registry binds to them."""
    person = await _get_or_404(session, Person, Person.entity_id, person_id, "person")
    keys = (
        (
            await session.execute(
                select(PersonCrosswalk)
                .where(PersonCrosswalk.entity_id == person.entity_id)
                .order_by(PersonCrosswalk.natural_key.asc())
            )
        )
        .scalars()
        .all()
    )
    detail = PersonDetail.model_validate(person)
    detail.identifiers = [PersonCrosswalkOut.model_validate(row) for row in keys]
    return detail


# --------------------------------------------------------------------------- #
# Organizations
# --------------------------------------------------------------------------- #


@router.get("/organizations", response_model=Page[OrganizationOut])
async def list_organizations(
    session: AsyncSession = Depends(get_db_session),
    org_type: str | None = Query(default=None, description="`committee` | `other` | …"),
    agency: str | None = Query(default=None, description="`House` | `Senate` | `Joint` | `Other`."),
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[OrganizationOut]:
    """Organizations, oldest entity id first. The cursor is an `entity_id`."""
    stmt = select(Organization)
    if org_type is not None:
        stmt = stmt.where(Organization.org_type == org_type)
    if agency is not None:
        stmt = stmt.where(Organization.agency == agency)
    return await _page(
        session,
        _keyset(stmt, Organization.entity_id, cursor=cursor, limit=limit),
        OrganizationOut,
        key_of=lambda row: row.entity_id,
        limit=limit,
    )


@router.get("/organizations/{organization_id}", response_model=OrganizationOut)
async def get_organization(
    organization_id: ULIDPath,
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationOut:
    """One organization."""
    row = await _get_or_404(
        session, Organization, Organization.entity_id, organization_id, "organization"
    )
    return OrganizationOut.model_validate(row)


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #


@router.get("/roles", response_model=Page[RoleOut])
async def list_roles(
    session: AsyncSession = Depends(get_db_session),
    organization_id: str | None = Query(
        default=None, description="Registry ULID of the owning organization."
    ),
    role_type: str | None = Query(
        default=None,
        description=(
            "`party_member` | `committee_member` | `state_senator` | `state_representative`."
        ),
    ),
    district: int | None = Query(default=None, description="LD number. Seat roles only."),
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[RoleOut]:
    """Roles, in structural key order. The cursor is a `role_key`.

    Ordered by ``role_key`` rather than by the registry ULID because the key is
    the row's own identity here — a role minted later still sorts beside its
    siblings, which is what makes paging through a committee's seats coherent.
    """
    stmt = select(Role)
    if organization_id is not None:
        stmt = stmt.where(Role.org_entity_id == organization_id)
    if role_type is not None:
        stmt = stmt.where(Role.role_type == role_type)
    if district is not None:
        stmt = stmt.where(Role.district == district)
    return await _page(
        session,
        _keyset(stmt, Role.role_key, cursor=cursor, limit=limit),
        RoleOut,
        key_of=lambda row: row.role_key,
        limit=limit,
    )


@router.get("/roles/{role_id}", response_model=RoleOut)
async def get_role(
    role_id: ULIDPath,
    session: AsyncSession = Depends(get_db_session),
) -> RoleOut:
    """One role by its registry ULID.

    Addressed by ``entity_id`` and not by ``role_key``: the key is derived, so it
    can move when the derivation is corrected, and an id that moves is not an id.
    Both are on the response, so a caller holding one can always find the other.
    """
    return RoleOut.model_validate(await _get_or_404(session, Role, Role.entity_id, role_id, "role"))


# --------------------------------------------------------------------------- #
# Assignments — i.e. tenure spans
# --------------------------------------------------------------------------- #


@router.get("/assignments", response_model=Page[AssignmentSummary])
async def list_assignments(
    session: AsyncSession = Depends(get_db_session),
    person_id: str | None = Query(default=None, description="Registry ULID of the holder."),
    role_id: str | None = Query(default=None, description="Registry ULID of the role held."),
    role_key: str | None = Query(default=None, description="Structural key of the role held."),
    is_active: bool | None = Query(default=None, description="Open spans only when true."),
    span_kind: str | None = Query(
        default=None,
        description=(
            "`chamber-senate` | `chamber-house` | `committee` | `party`. A real column "
            "since #313 — the string-splitting under-report (#335) is gone with it."
        ),
    ),
    as_of: date | None = Query(
        default=None, description="Only spans covering this date (`valid_from` ≤ d ≤ `valid_to`)."
    ),
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[AssignmentSummary]:
    """Assignments — equivalently, tenure spans — in span-key order.

    Keyed on the five columns that are a span's identity, so the cursor is those
    values encoded rather than a row id: there is no row id any more, because a
    span *is* its key.
    """
    stmt = select(Assignment)
    if person_id is not None:
        stmt = stmt.where(Assignment.entity_id == person_id)
    if role_key is not None:
        stmt = stmt.where(Assignment.role_key == role_key)
    if role_id is not None:
        stmt = stmt.where(
            Assignment.role_key.in_(select(Role.role_key).where(Role.entity_id == role_id))
        )
    if is_active is not None:
        stmt = stmt.where(Assignment.is_active.is_(is_active))
    if span_kind is not None:
        stmt = stmt.where(Assignment.span_kind == span_kind)
    if as_of is not None:
        stmt = stmt.where(Assignment.valid_from <= as_of).where(
            (Assignment.valid_to.is_(None)) | (Assignment.valid_to >= as_of)
        )
    after = decode_cursor(cursor, arity=len(_SPAN_KEY_COLUMNS))
    if after is not None:
        # Row-value comparison, not a chain of ORs: `(a, b, …) > (:a, :b, …)` is
        # the whole keyset predicate in one expression, and the one shape that
        # cannot get a boundary wrong when an earlier column ties.
        stmt = stmt.where(tuple_(*_SPAN_KEY_COLUMNS) > tuple_(*(literal(v) for v in after)))
    stmt = stmt.order_by(*(column.asc() for column in _SPAN_KEY_COLUMNS)).limit(limit + 1)
    return await _page(
        session,
        stmt,
        AssignmentSummary,
        key_of=lambda row: encode_cursor(
            [
                row.source,
                row.member_id,
                row.span_kind,
                row.span_discriminator,
                row.span_start_biennium,
            ]
        ),
        limit=limit,
    )


@router.get("/assignments/{assignment_id}", response_model=AssignmentDetail)
async def get_assignment(
    assignment_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> AssignmentDetail:
    """One tenure span with its provenance chain — *who held this seat when, and
    how do we know* in a single request.

    ``assignment_id`` is the 4-part span key, split from the **right** so a
    roster identity's own colon does not shift every field (#259). ``source`` is
    not part of it: the two families key in disjoint identity spaces (numeric WSL
    ids, ``<fold>:<year>`` roster ones), which a dbt uniqueness test pins.

    Citations are capped at ``EMBEDDED_CITATION_LIMIT``; a span with a longer
    chain is paginated at ``/provenance/assignment/{id}``.
    """
    member, kind, discriminator, start = split_span_key(assignment_id)
    matches = list(
        (
            await session.execute(
                select(Assignment)
                .where(Assignment.member_id == member)
                .where(Assignment.span_kind == kind)
                .where(Assignment.span_discriminator == discriminator)
                .where(Assignment.span_start_biennium == start)
                .order_by(Assignment.source.asc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no assignment with id {assignment_id}"
        )
    if len(matches) > 1:
        # Two families claiming one span key breaks the identity-space split the
        # whole two-source design rests on. A 500 is right: nothing the caller
        # sent is wrong, and answering with either row would be a coin flip.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"assignment id {assignment_id} matches spans in more than one source; "
                "the identity spaces are meant to be disjoint"
            ),
        )
    assignment = matches[0]
    rows = (
        await session.execute(
            select(Citation, RawFetch)
            .outerjoin(
                RawFetch,
                (Citation.source == RawFetch.source)
                & (Citation.resource_id == RawFetch.resource_id),
            )
            .where(Citation.entity_type == "assignment")
            .where(Citation.entity_id == assignment_id)
            .order_by(Citation.source.asc(), Citation.resource_id.asc())
            .limit(EMBEDDED_CITATION_LIMIT)
        )
    ).all()
    detail = AssignmentDetail.model_validate(assignment)
    detail.citations = [CitationOut.from_row(citation, fetch) for citation, fetch in rows]
    return detail
