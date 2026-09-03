"""Read seam for the curated operator events (#309).

Operator succession events are the one span input with no raw-store origin:
they are human decisions (an appointee seated on a date the wire never
carries, a mid-biennium departure), curated in Postgres by
``usa_wa_adapter_legislature.operators``. The span models read them as a
**curated input** — exactly as the registry crosswalk is read — so the
transform stays stateless while the judgment stays durable.

Sync wrapper, because dbt Python models are synchronous; the hermetic marker
is the same opt-in the crosswalk seam uses, so a build with no database says
so explicitly instead of silently producing spans with no operator boundaries.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.config import get_database_url
from clearinghouse_domain_legislative.operator_events import OperatorEvent

#: Set by the commit gate / dbt tests only — see registry_read.crosswalk_frame.
HERMETIC_ENV = "USA_WA_PIPELINE_HERMETIC"


@dataclass(frozen=True)
class EventRow:
    """The attribute surface ``operator_overlay.from_rows`` reads. A plain
    dataclass rather than the ORM row so the transform never holds a session."""

    member_id: str
    kind: str
    effective_date: date
    seat_kind: str | None
    seat_discriminator: str | None


async def operator_event_rows(session: AsyncSession) -> list[EventRow]:
    """Every current (non-superseded) operator event, oldest first — and, within
    one date, in curation order.

    The ULID tiebreak is load-bearing (CR 61). ``apply_operator_events`` sorts
    **stably** on ``(is_departed, effective_date)``, so the input order settles
    same-date ties, and its per-span seating dedup makes which of two same-date
    events lands first outcome-affecting. Postgres promises no order for equal
    sort keys, and production carries seven (member, date) pairs holding two
    current events each — enough to re-date spans between two runs over
    identical inputs, which a content-hashed versioned dataset cannot tolerate.
    """
    rows = (
        await session.execute(
            select(
                OperatorEvent.member_id,
                OperatorEvent.kind,
                OperatorEvent.effective_date,
                OperatorEvent.seat_kind,
                OperatorEvent.seat_discriminator,
            )
            .where(OperatorEvent.superseded_by_id.is_(None))
            .order_by(OperatorEvent.effective_date, OperatorEvent.id)
        )
    ).all()
    return [EventRow(*row) for row in rows]


def operator_events() -> list[Any]:
    """Sync wrapper for dbt Python models: own engine off ``DATABASE_URL``."""
    if os.environ.get(HERMETIC_ENV) == "1":
        return []
    database_url = get_database_url()

    async def _read() -> list[EventRow]:
        engine = create_async_engine(database_url)
        try:
            async with AsyncSession(engine) as session:
                return await operator_event_rows(session)
        finally:
            await engine.dispose()

    return list(asyncio.run(_read()))
