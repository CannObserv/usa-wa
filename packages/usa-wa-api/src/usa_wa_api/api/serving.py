"""``GET /health/serving`` (#313) — is the API's own projection current?

The sibling of ``/health/datasets``: that one answers "did the pipeline
publish", this one answers "did this deployment load what was published". They
are different failures — a healthy catalog with a stale serving schema is
exactly the silent case worth a probe, because every ``/api/v1`` answer would
still be a 200.

Unauthenticated and unversioned, alongside ``/health`` and ``/ready``: it is a
deployment contract, not part of the product surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from usa_wa_api.api.deps import get_db_session
from usa_wa_api.serving.load import catalog_entries, datasets_root
from usa_wa_api.serving.schema import SERVING_TABLES

router = APIRouter(tags=["health"])


@router.get("/health/serving")
async def health_serving(session: AsyncSession = Depends(get_db_session)) -> dict:
    """Per-dataset loaded row counts against what the catalog currently lists.

    ``loaded: false`` when the schema does not exist yet — a fresh box before
    the first load, which is a normal state and not an error (the #180 posture
    the dataset probe already takes).
    """
    published = {name: entry["rows"] for name, entry in catalog_entries(datasets_root()).items()}
    try:
        loaded = {
            name: await session.scalar(select(func.count()).select_from(table))
            for name, table in SERVING_TABLES.items()
        }
    except SQLAlchemyError:
        return {"loaded": False, "datasets": []}
    return {
        "loaded": True,
        "datasets": [
            {
                "name": name,
                "rows": rows,
                "published_rows": published.get(name),
                # The one comparison worth making: a served table that no longer
                # matches the published row count means the load did not take.
                "current": published.get(name) == rows,
            }
            for name, rows in sorted(loaded.items())
        ],
    }
