"""``GET /health/serving`` (#313) — is the API's own projection current?

The sibling of ``/health/datasets``: that one answers "did the pipeline
publish", this one answers "did this deployment load what was published". They
are different failures — a healthy catalog with a stale serving schema is
exactly the silent case worth a probe, because every ``/api/v1`` answer would
still be a 200.

**Currency is a version comparison, not a row count** (CR 92). An unchanged row
count is the *normal* case for this corpus — the publisher skips a mint when the
content hash is unchanged, so quiet days are typical — which makes counts unable
to tell yesterday's snapshot from today's. ``serving.load_state`` records the
version each table actually holds, and that is what is compared here.

Unauthenticated and unversioned, alongside ``/health`` and ``/ready``: it is a
deployment contract, not part of the product surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from usa_wa_api.api.deps import get_db_session
from usa_wa_api.serving.load import catalog_entries, datasets_root
from usa_wa_api.serving.schema import SCHEMA, SERVING_TABLES, LoadState

router = APIRouter(tags=["health"])


async def _schema_is_built(session: AsyncSession) -> bool:
    """Whether the serving tables exist at all.

    Asked directly rather than inferred from a failed query (CR 94). Catching
    ``SQLAlchemyError`` around the counts conflated three different facts — "not
    loaded yet", "permission denied" and "the database is down" — and reported
    all of them as the first, which is the one shape the #180 posture exists to
    forbid. A broken database now raises rather than answering a comfortable
    lie; ``/ready`` is the probe that speaks to database liveness.
    """
    found = await session.scalar(
        select(func.count())
        .select_from(text("information_schema.tables"))
        .where(text("table_schema = :schema"))
        .params(schema=SCHEMA)
    )
    return bool(found)


@router.get("/health/serving")
async def health_serving(session: AsyncSession = Depends(get_db_session)) -> dict:
    """Per-dataset loaded version against what the catalog currently lists.

    ``loaded: false`` when the schema has not been built yet — a fresh box
    before the first load, which is a normal state and not an error.
    """
    if not await _schema_is_built(session):
        return {"loaded": False, "datasets": []}

    published = catalog_entries(datasets_root())
    state = {row.dataset: row for row in (await session.execute(select(LoadState))).scalars().all()}
    datasets = []
    for name, table in sorted(SERVING_TABLES.items()):
        loaded = state.get(name)
        entry = published.get(name)
        row: dict = {
            "name": name,
            "loaded_version": loaded.version if loaded else None,
            "published_version": entry["latest_version"] if entry else None,
            "rows": await session.scalar(select(func.count()).select_from(table)),
            # A dataset the catalog does not carry is `null`, never False —
            # "the catalog does not say" and "the catalog says otherwise" are
            # different facts.
            "current": (
                None
                if loaded is None or entry is None
                else loaded.version == entry["latest_version"]
            ),
        }
        # Rows the API cannot address: a registry key the registrar had not yet
        # bound when the model that published this row read the crosswalk
        # (CR 95). Transient by construction — the next build closes it — and
        # the PERSISTENT case is already gated by `parity_spans`, which re-reads
        # the registry. What was missing is any view of what actually shipped,
        # which only the loaded rows can show. Reported, not gated: a new seat
        # legitimately arrives this way, so a zero floor would fail the nightly
        # every time a committee is created.
        entity_id = table.columns.get("entity_id")
        if entity_id is not None and entity_id.nullable:
            row["unaddressable_rows"] = await session.scalar(
                select(func.count()).select_from(table).where(entity_id.is_(None))
            )
        datasets.append(row)
    return {"loaded": True, "datasets": datasets}
