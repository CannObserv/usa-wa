"""Operations slice of ``/api/v1`` (#184) — the telemetry, made readable.

The run ledger (#178) and the source-coverage table (#180) both landed as tables
with no consumer, which is the failure mode #184 names: *building the ledger
without the surface just moves the invisibility*. These four routes are that
consumer.

Every route is a ``GET``. The API runs as the **app** role and the provenance
tables carry ``REVOKE UPDATE`` (#54), so a mutating route here would not fail in
review — it would fail at the database, in production.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.provenance import Citation, FetchEvent, Source, citable_entity_types
from clearinghouse_core.runs import JobRun
from clearinghouse_core.source_coverage import SourceCoverage
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
    CitationOut,
    CoverageSpan,
    JobHealth,
    SourceCoverageOut,
    SourceOut,
    ULIDPath,
    parse_ulid_path,
)

router = APIRouter(tags=["operations"])

#: The discriminators actually written today, for the 422 *message* only. The accepted
#: set is the full ORM registry (any mapped class may be cited); naming all 52 in an
#: error would bury the five a caller almost certainly meant.
_CITED_ENTITY_TYPES = frozenset(
    {"person", "personidentifier", "organization", "role", "assignment"}
)


@router.get("/health/jobs", response_model=Page[JobHealth])
async def list_job_health(
    session: AsyncSession = Depends(get_db_session),
    job_slug: str | None = Query(default=None, description="Restrict to one job slug."),
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[JobHealth]:
    """Latest run per job slug, from the run ledger (#178).

    One row per slug — the most recent ``started_at`` — because "when did this job
    last run and how did it end" is the operational question; the full history is
    a different route and nobody has asked for it yet.

    **An empty list is a normal answer, not a failure.** #178 shipped with one
    adopter (the integrity sweep); the sweep across the remaining CLIs is #179b.
    A slug that has never run has no row and therefore does not appear — which is
    itself the finding, and the reason the ledger records slugs rather than
    deriving them from a registry that would claim a run that never happened.

    Ordered by ``job_slug`` ascending; the cursor is a job slug.
    """
    stmt = select(JobRun).distinct(JobRun.job_slug)
    if job_slug is not None:
        stmt = stmt.where(JobRun.job_slug == job_slug)
    if cursor is not None:
        stmt = stmt.where(JobRun.job_slug > cursor)
    stmt = stmt.order_by(JobRun.job_slug.asc(), JobRun.started_at.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    page, next_cursor = take_page(rows, limit=limit, key_of=lambda row: row.job_slug)
    now = datetime.now(UTC)
    return Page(
        items=[JobHealth.from_row(row, now=now) for row in page],
        limit=limit,
        next_cursor=next_cursor,
    )


@router.get("/sources", response_model=Page[SourceOut])
async def list_sources(
    session: AsyncSession = Depends(get_db_session),
    kind: str | None = Query(default=None, description="Filter by transport family."),
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[SourceOut]:
    """Every configured data feed. Ordered by id ascending (creation order)."""
    stmt = select(Source)
    if kind is not None:
        stmt = stmt.where(Source.kind == kind)
    after = parse_ulid_cursor(cursor)
    if after is not None:
        stmt = stmt.where(Source.id > after)
    stmt = stmt.order_by(Source.id.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    page, next_cursor = take_page(rows, limit=limit, key_of=lambda row: str(row.id))
    return Page(
        items=[SourceOut.model_validate(row) for row in page],
        limit=limit,
        next_cursor=next_cursor,
    )


async def _source_by_slug(session: AsyncSession, slug: str) -> Source:
    source = await session.scalar(select(Source).where(Source.slug == slug))
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no source with slug {slug!r}"
        )
    return source


@router.get("/sources/{slug}", response_model=SourceOut)
async def get_source(
    slug: str,
    session: AsyncSession = Depends(get_db_session),
) -> SourceOut:
    """One configured feed by its stable slug."""
    return SourceOut.model_validate(await _source_by_slug(session, slug))


@router.get("/sources/{slug}/coverage", response_model=SourceCoverageOut)
async def get_source_coverage(
    slug: str,
    session: AsyncSession = Depends(get_db_session),
) -> SourceCoverageOut:
    """What this source covers, per dimension, and how each bound was established (#180).

    **The empty case is answered, not 404'd.** An unknown slug is a 404; a *known*
    source with no coverage rows is a 200 carrying ``coverage_recorded: false``.
    Collapsing the two would recreate exactly the silence #180 exists to remove:
    "we have never audited this feed" and "this feed does not exist" are different
    facts, and so is "this feed covers nothing". The table is additive and rows
    seed from ``get_or_create_source``, so it is empty in production until the next
    harvest run — the unaudited answer is the *common* one today and has to be a
    first-class response rather than an error.

    ``status`` is reported verbatim per span, and the ``absent`` subset is repeated
    as ``known_gaps``: a known gap is the load-bearing fact here, and a response
    that flattened the three statuses would lose the only thing the table was built
    for.

    Not paginated. The rows are a full reconcile of a hand-written declaration —
    one per ``(dimension, range_start)`` — so the set is bounded by the audit, not
    by the data volume.
    """
    source = await _source_by_slug(session, slug)
    rows = list(
        (
            await session.execute(
                select(SourceCoverage)
                .where(SourceCoverage.source_id == source.id)
                .order_by(SourceCoverage.dimension.asc(), SourceCoverage.range_start.asc())
            )
        )
        .scalars()
        .all()
    )
    return SourceCoverageOut(
        source_slug=source.slug,
        source_id=source.id,
        coverage_recorded=bool(rows),
        items=[CoverageSpan.model_validate(row) for row in rows],
    )


@router.get("/provenance/{entity_type}/{entity_id}", response_model=Page[CitationOut])
async def list_provenance(
    entity_type: str,
    entity_id: ULIDPath,
    session: AsyncSession = Depends(get_db_session),
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[CitationOut]:
    """The citation chain for one canonical row — "how do we know this?".

    ``entity_type`` is the polymorphic discriminator the writers use: the lowercase
    mapped-class name. The accepted set is *derived from the ORM registry*
    (:func:`~clearinghouse_core.provenance.citable_entity_types`) exactly the way the
    writer derives it, so it cannot drift — and so it includes the ones a hand-written
    list forgets. ``personidentifier`` is a third of production's citations and was
    absent from every enumeration of this vocabulary in the codebase (CR #196 f41).

    An unknown ``entity_type`` is a **422**: it is a closed set this system controls, so
    a typo (``persons`` for ``person``) should be told rather than silently answered with
    an empty page. An unknown ``entity_id`` is **not** — there is no FK on it by design,
    one citation table spans every domain, so this route genuinely cannot tell "no
    provenance recorded" from "no such row" and returns an empty page rather than
    inventing a distinction it has no way to check.

    Ordered **newest first** (id descending): a re-pull mints a fresh ``FetchEvent``
    and citation for the same resource daily, so the head of the chain is the
    current attestation and is what a reader wants first.
    """
    known = citable_entity_types()
    if entity_type not in known:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"unknown entity_type {entity_type!r}; expected one of "
                f"{', '.join(sorted(known & _CITED_ENTITY_TYPES))}"
            ),
        )
    stmt = (
        select(Citation, FetchEvent, Source)
        .join(FetchEvent, Citation.fetch_event_id == FetchEvent.id)
        .join(Source, FetchEvent.source_id == Source.id)
        .where(Citation.entity_type == entity_type)
        .where(Citation.entity_id == parse_ulid_path(entity_id))
    )
    before = parse_ulid_cursor(cursor)
    if before is not None:
        stmt = stmt.where(Citation.id < before)
    stmt = stmt.order_by(Citation.id.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).all())
    page, next_cursor = take_page(rows, limit=limit, key_of=lambda row: str(row[0].id))
    return Page(
        items=[CitationOut.from_row(citation, event, source) for citation, event, source in page],
        limit=limit,
        next_cursor=next_cursor,
    )
