"""Canonical slice of ``/api/v1`` (#184) — persons, organizations, roles, spans.

The product surface. Every question in #184's motivation ("which spans are open
with no end?", "what is the evidence chain for LD-5 Position 1 in 2019?") is a
request here rather than script #48.

**There is no spans resource.** A tenure span *is* an ``Assignment``
(``docs/ONTOLOGY.md`` § 2), so ``/assignments`` is the span route: it carries the
parsed span key and takes a ``span_kind`` filter. Adding ``/spans`` as a separate
resource would publish a distinction the data model deliberately does not have.

**Liveness.** List routes filter the two lifecycle tombstones through
``queries.live_only``, applied once per model the query joins through — a live
Role hanging off an archived Organization is dropped only if the org hop is
filtered too. ``include_hidden=true`` is the explicit audit escape hatch.
Detail routes never filter: a caller who names an id gets that row with its
tombstones visible, because "archived" is an answer and a 404 is not.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.provenance import Citation, FetchEvent, Source
from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    PersonIdentifier,
    Role,
)
from clearinghouse_domain_legislative.queries import live_only
from clearinghouse_domain_legislative.span_emit import ASSIGNMENT_CITATION_TYPE
from usa_wa_api.api.deps import get_db_session
from usa_wa_api.api.v1.pagination import (
    DEFAULT_LIMIT,
    CursorQuery,
    LimitQuery,
    Page,
    parse_ulid_cursor,
    take_page,
)
from usa_wa_api.api.v1.schemas import (
    SPAN_KEY_PARTS,
    AssignmentDetail,
    AssignmentSummary,
    CitationOut,
    OrganizationOut,
    PersonDetail,
    PersonIdentifierOut,
    PersonSummary,
    RoleOut,
    ULIDPath,
    parse_ulid_path,
    parse_ulid_query,
)

router = APIRouter(tags=["canonical"])

EMBEDDED_CITATION_LIMIT = DEFAULT_LIMIT
"""How many citations ride along on an assignment detail response. An embedded
collection needs a bound or the detail route becomes the unpaginated list route
this API does not have; the full chain is paginated at
``/provenance/assignment/{id}``."""

IncludeHiddenQuery = Query(
    default=False,
    description=(
        "Include archived and deleted rows. Off by default — both tombstones hide a "
        "row from live reads."
    ),
)


def _keyset(stmt: Select, column, *, cursor: str | None, limit: int) -> Select:
    """Apply the ascending ULID keyset predicate, ordering and over-fetch."""
    after = parse_ulid_cursor(cursor)
    if after is not None:
        stmt = stmt.where(column > after)
    return stmt.order_by(column.asc()).limit(limit + 1)


async def _page(session: AsyncSession, stmt: Select, model, *, limit: int) -> Page:
    rows = list((await session.execute(stmt)).scalars().all())
    page, next_cursor = take_page(rows, limit=limit, key_of=lambda row: str(row.id))
    return Page(
        items=[model.model_validate(row) for row in page],
        limit=limit,
        next_cursor=next_cursor,
    )


async def _get_or_404(session: AsyncSession, model, row_id: _ULID, label: str):
    row = await session.scalar(select(model).where(model.id == row_id))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no {label} with id {row_id}"
        )
    return row


# --------------------------------------------------------------------------- #
# Persons
# --------------------------------------------------------------------------- #


@router.get("/persons", response_model=Page[PersonSummary])
async def list_persons(
    session: AsyncSession = Depends(get_db_session),
    source: str | None = Query(default=None, description="Filter by originating source slug."),
    name_contains: str | None = Query(
        default=None, min_length=2, description="Case-insensitive substring of `name_full`."
    ),
    include_hidden: bool = IncludeHiddenQuery,
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[PersonSummary]:
    """People, oldest id first. The cursor is a person id."""
    stmt = live_only(select(Person), Person, include_hidden=include_hidden)
    if source is not None:
        stmt = stmt.where(Person.source == source)
    if name_contains is not None:
        stmt = stmt.where(Person.name_full.ilike(f"%{name_contains}%"))
    return await _page(
        session, _keyset(stmt, Person.id, cursor=cursor, limit=limit), PersonSummary, limit=limit
    )


@router.get("/persons/{person_id}", response_model=PersonDetail)
async def get_person(
    person_id: ULIDPath,
    session: AsyncSession = Depends(get_db_session),
) -> PersonDetail:
    """One person with their external-identifier graph.

    Returns archived and deleted rows too — the tombstones are on the response, and
    a caller holding an id is usually asking *because* the row went quiet.
    """
    person = await _get_or_404(session, Person, parse_ulid_path(person_id), "person")
    identifiers = (
        (
            await session.execute(
                select(PersonIdentifier)
                .where(PersonIdentifier.person_id == person.id)
                .order_by(PersonIdentifier.scheme.asc())
            )
        )
        .scalars()
        .all()
    )
    detail = PersonDetail.model_validate(person)
    detail.identifiers = [PersonIdentifierOut.model_validate(row) for row in identifiers]
    return detail


# --------------------------------------------------------------------------- #
# Organizations
# --------------------------------------------------------------------------- #


@router.get("/organizations", response_model=Page[OrganizationOut])
async def list_organizations(
    session: AsyncSession = Depends(get_db_session),
    org_type: str | None = Query(default=None, description="`chamber` | `committee` | `party` | …"),
    jurisdiction_id: str | None = Query(default=None, description="ULID of the binding root."),
    active: bool | None = Query(
        default=None,
        description=(
            "PM's operational live-vs-dissolved flag. A **third** axis, unrelated to the "
            "tombstones: a dissolved committee is inactive, not archived."
        ),
    ),
    include_hidden: bool = IncludeHiddenQuery,
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[OrganizationOut]:
    """Organizations, oldest id first. The cursor is an organization id."""
    stmt = live_only(select(Organization), Organization, include_hidden=include_hidden)
    if org_type is not None:
        stmt = stmt.where(Organization.org_type == org_type)
    if jurisdiction_id is not None:
        stmt = stmt.where(
            Organization.jurisdiction_id
            == parse_ulid_query(jurisdiction_id, field="jurisdiction_id")
        )
    if active is not None:
        stmt = stmt.where(Organization.active.is_(active))
    return await _page(
        session,
        _keyset(stmt, Organization.id, cursor=cursor, limit=limit),
        OrganizationOut,
        limit=limit,
    )


@router.get("/organizations/{organization_id}", response_model=OrganizationOut)
async def get_organization(
    organization_id: ULIDPath,
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationOut:
    """One organization, tombstones included."""
    row = await _get_or_404(session, Organization, parse_ulid_path(organization_id), "organization")
    return OrganizationOut.model_validate(row)


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #


@router.get("/roles", response_model=Page[RoleOut])
async def list_roles(
    session: AsyncSession = Depends(get_db_session),
    organization_id: str | None = Query(default=None, description="ULID of the owning org."),
    role_type: str | None = Query(default=None, description="`elected_member` | `staff` | …"),
    jurisdiction_id: str | None = Query(
        default=None, description="ULID of the seat's district. Seat roles only."
    ),
    include_hidden: bool = IncludeHiddenQuery,
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[RoleOut]:
    """Roles, oldest id first. Joins the owning organization so an archived org
    hides its roles too. The cursor is a role id."""
    stmt = live_only(
        select(Role).join(Organization, Role.organization_id == Organization.id),
        Role,
        Organization,
        include_hidden=include_hidden,
    )
    if organization_id is not None:
        stmt = stmt.where(
            Role.organization_id == parse_ulid_query(organization_id, field="organization_id")
        )
    if role_type is not None:
        stmt = stmt.where(Role.role_type == role_type)
    if jurisdiction_id is not None:
        stmt = stmt.where(
            Role.jurisdiction_id == parse_ulid_query(jurisdiction_id, field="jurisdiction_id")
        )
    return await _page(
        session, _keyset(stmt, Role.id, cursor=cursor, limit=limit), RoleOut, limit=limit
    )


@router.get("/roles/{role_id}", response_model=RoleOut)
async def get_role(
    role_id: ULIDPath,
    session: AsyncSession = Depends(get_db_session),
) -> RoleOut:
    """One role, tombstones included."""
    return RoleOut.model_validate(
        await _get_or_404(session, Role, parse_ulid_path(role_id), "role")
    )


# --------------------------------------------------------------------------- #
# Assignments — i.e. tenure spans
# --------------------------------------------------------------------------- #


@router.get("/assignments", response_model=Page[AssignmentSummary])
async def list_assignments(
    session: AsyncSession = Depends(get_db_session),
    person_id: str | None = Query(default=None, description="ULID of the holder."),
    role_id: str | None = Query(default=None, description="ULID of the role held."),
    is_active: bool | None = Query(default=None, description="Open spans only when true."),
    span_kind: str | None = Query(
        default=None,
        description=(
            "`chamber-senate` | `chamber-house` | `committee` | `party`. Matches the "
            "kind position of the 4-part span `source_id`; rows with any other "
            "`source_id` shape never match."
        ),
    ),
    as_of: date | None = Query(
        default=None, description="Only spans covering this date (`valid_from` ≤ d ≤ `valid_to`)."
    ),
    include_hidden: bool = IncludeHiddenQuery,
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[AssignmentSummary]:
    """Assignments — equivalently, tenure spans — oldest id first.

    Joins role and organization so an archived org hides the tenures held under it;
    pass ``include_hidden=true`` to see them. The cursor is an assignment id.
    """
    stmt = live_only(
        select(Assignment)
        .join(Role, Assignment.role_id == Role.id)
        .join(Organization, Role.organization_id == Organization.id),
        Assignment,
        Role,
        Organization,
        include_hidden=include_hidden,
    )
    if person_id is not None:
        stmt = stmt.where(Assignment.person_id == parse_ulid_query(person_id, field="person_id"))
    if role_id is not None:
        stmt = stmt.where(Assignment.role_id == parse_ulid_query(role_id, field="role_id"))
    if is_active is not None:
        stmt = stmt.where(Assignment.is_active.is_(is_active))
    if span_kind is not None:
        # Guard on the part count as well as the value: a legacy `source_id` with a
        # different shape can still have *something* in position 2, and matching it
        # would report a span kind the row does not carry.
        stmt = stmt.where(
            func.array_length(func.string_to_array(Assignment.source_id, ":"), 1) == SPAN_KEY_PARTS
        ).where(func.split_part(Assignment.source_id, ":", 2) == span_kind)
    if as_of is not None:
        stmt = stmt.where(Assignment.valid_from <= as_of).where(
            (Assignment.valid_to.is_(None)) | (Assignment.valid_to >= as_of)
        )
    return await _page(
        session,
        _keyset(stmt, Assignment.id, cursor=cursor, limit=limit),
        AssignmentSummary,
        limit=limit,
    )


@router.get("/assignments/{assignment_id}", response_model=AssignmentDetail)
async def get_assignment(
    assignment_id: ULIDPath,
    session: AsyncSession = Depends(get_db_session),
) -> AssignmentDetail:
    """One tenure span with its provenance chain — *who held this seat when, and
    how do we know* in a single request.

    Citations are capped at ``EMBEDDED_CITATION_LIMIT`` and ordered newest first;
    a span with a longer chain is paginated at ``/provenance/assignment/{id}``.
    """
    assignment = await _get_or_404(
        session, Assignment, parse_ulid_path(assignment_id), "assignment"
    )
    rows = (
        await session.execute(
            select(Citation, FetchEvent, Source)
            .join(FetchEvent, Citation.fetch_event_id == FetchEvent.id)
            .join(Source, FetchEvent.source_id == Source.id)
            .where(Citation.entity_type == ASSIGNMENT_CITATION_TYPE)
            .where(Citation.entity_id == assignment.id)
            .order_by(Citation.id.desc())
            .limit(EMBEDDED_CITATION_LIMIT)
        )
    ).all()
    detail = AssignmentDetail.model_validate(assignment)
    detail.citations = [
        CitationOut.from_row(citation, event, source) for citation, event, source in rows
    ]
    return detail
