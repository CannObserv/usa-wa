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
from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.provenance import Source
from clearinghouse_core.runs import JobRun
from clearinghouse_core.source_coverage import SourceCoverage
from usa_wa_api.api.deps import get_db_session
from usa_wa_api.api.v1.pagination import (
    DEFAULT_LIMIT,
    CursorQuery,
    LimitQuery,
    Page,
    decode_cursor,
    encode_cursor,
    parse_ulid_cursor,
    take_page,
)
from usa_wa_api.api.v1.schemas import (
    CitationOut,
    CoverageSpan,
    JobHealth,
    SourceCoverageOut,
    SourceOut,
)
from usa_wa_api.serving.schema import Citation, RawFetch

router = APIRouter(tags=["operations"])

#: The entity types the citations artifact carries (#313). A CLOSED set now, not a
#: sample of an open ORM registry: the artifact is built from four named rules, so a
#: type outside this list is a typo rather than a shape nobody has cited yet.
#: ``personidentifier`` is gone — a third of the old chain — because identifiers are
#: the crosswalk now, and a key is not a thing that gets separately attested.
CITED_ENTITY_TYPES = ("assignment", "organization", "person", "role")


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
    entity_id: str,
    session: AsyncSession = Depends(get_db_session),
    limit: LimitQuery = DEFAULT_LIMIT,
    cursor: CursorQuery = None,
) -> Page[CitationOut]:
    """The citation chain for one published entity — "how do we know this?".

    Since #313 this reads the published citations artifact rather than the
    Postgres provenance ledger, and the shape of the answer changed with it: a
    citation names a **raw resource** and the digest of its bytes, not a fetch
    event and a confidence. That is a stronger statement, not a weaker one —
    ``sha256`` identifies exactly what was read.

    ``entity_id`` is a registry ULID for ``person``/``organization``/``role`` and
    the 4-part span key for ``assignment``, so it is **not** ULID-validated: the
    assignment case is legitimately not a ULID.

    An unknown ``entity_type`` is a **422** — it is a closed set this system
    controls (:data:`CITED_ENTITY_TYPES`), so a typo (``persons`` for ``person``)
    should be told rather than silently answered with an empty page. An unknown
    ``entity_id`` is **not**: one artifact spans every kind and carries no foreign
    key, so this route genuinely cannot tell "no provenance recorded" from "no
    such row", and it returns an empty page rather than inventing a distinction
    it cannot check.

    Ordered by ``(source, resource_id)``. The old chain ordered newest-first
    because a daily re-pull minted a fresh citation for the same resource; the
    artifact holds exactly one row per (entity, resource), so there is no
    recency to surface and a stable key order is what pages correctly.
    """
    if entity_type not in CITED_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"unknown entity_type {entity_type!r}; expected one of "
                f"{', '.join(CITED_ENTITY_TYPES)}"
            ),
        )
    stmt = (
        select(Citation, RawFetch)
        # LEFT: a citation with no attestation is an integrity break the nightly
        # probe gates at zero, and dropping it here would hide from a reader the
        # very thing that probe exists to shout about.
        .outerjoin(
            RawFetch,
            (Citation.source == RawFetch.source) & (Citation.resource_id == RawFetch.resource_id),
        )
        .where(Citation.entity_type == entity_type)
        .where(Citation.entity_id == entity_id)
    )
    after = decode_cursor(cursor, arity=2)
    if after is not None:
        stmt = stmt.where(
            tuple_(Citation.source, Citation.resource_id) > tuple_(*(literal(v) for v in after))
        )
    stmt = stmt.order_by(Citation.source.asc(), Citation.resource_id.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).all())
    page, next_cursor = take_page(
        rows, limit=limit, key_of=lambda row: encode_cursor([row[0].source, row[0].resource_id])
    )
    return Page(
        items=[CitationOut.from_row(citation, fetch) for citation, fetch in page],
        limit=limit,
        next_cursor=next_cursor,
    )
