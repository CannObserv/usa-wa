"""Read seams over the registry for the conformed tier (#309).

The conformed models are stateless joins against the registry (spec § Target
architecture); dbt Python models are synchronous, so :func:`crosswalk_frame`
wraps the async read with its own engine + ``asyncio.run``. The crosswalk row
shape is the published contract: ``entity_id`` (ULID base32), the raw
``natural_key`` plus its split ``key_namespace``/``key_value`` halves,
``registered_by``, and the entity's ``merged_into`` tombstone (the only
re-point signal PM gets — spec § walkthrough).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.config import get_database_url
from clearinghouse_core.registry import RegistryEntity, RegistryKey


async def crosswalk_rows(session: AsyncSession, kind: str) -> list[dict[str, Any]]:
    """Every key of ``kind``, flattened with its entity's merge tombstone."""
    rows = (
        await session.execute(
            select(
                RegistryKey.natural_key,
                RegistryKey.entity_id,
                RegistryKey.registered_by,
                RegistryEntity.merged_into,
            )
            .join(RegistryEntity, RegistryEntity.id == RegistryKey.entity_id)
            .where(RegistryKey.kind == kind)
            .order_by(RegistryKey.natural_key)
        )
    ).all()
    out = []
    for natural_key, entity_id, registered_by, merged_into in rows:
        namespace, _, value = natural_key.partition(":")
        out.append(
            {
                "entity_id": str(entity_id),
                "natural_key": natural_key,
                "key_namespace": namespace,
                "key_value": value,
                "registered_by": registered_by,
                "merged_into": None if merged_into is None else str(merged_into),
            }
        )
    return out


def crosswalk_frame(kind: str) -> list[dict[str, Any]]:
    """Sync wrapper for dbt Python models: own engine off ``DATABASE_URL``.

    The empty fallback is OPT-IN (CR 2): only ``USA_WA_PIPELINE_HERMETIC=1``
    (set by the commit gate and the dbt tests) may build with no database —
    a production run missing ``DATABASE_URL`` must fail the build loudly, not
    materialize empty crosswalks whose schema tests pass vacuously and hand
    the publish gate a 100%-shrink surprise two stages later.
    """
    if os.environ.get("USA_WA_PIPELINE_HERMETIC") == "1":
        return []
    database_url = get_database_url()

    async def _read() -> list[dict[str, Any]]:
        engine = create_async_engine(database_url)
        try:
            async with AsyncSession(engine) as session:
                return await crosswalk_rows(session, kind)
        finally:
            await engine.dispose()

    return asyncio.run(_read())


CROSSWALK_COLUMNS = [
    "entity_id",
    "natural_key",
    "key_namespace",
    "key_value",
    "registered_by",
    "merged_into",
]
