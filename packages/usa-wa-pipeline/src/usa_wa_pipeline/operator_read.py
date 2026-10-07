"""Read seams for the curated operator inputs: events (#309), succession links (#447).

Operator succession events are the one span input with no raw-store origin:
they are human decisions (an appointee seated on a date the wire never
carries, a mid-biennium departure), curated in Postgres
(``registry.operator_events`` since #412, beside the adjudications) by
``usa_wa_adapter_legislature.operators``. The span models read them as a
**curated input** — exactly as the registry crosswalk is read — so the
transform stays stateless while the judgment stays durable.

The committee succession links (``registry.committee_succession_events``, #124) are
the same kind of input — operator-attested, no raw-store origin — and the
``org_lineage`` model reads them the same way (#447).

Sync wrappers, because dbt Python models are synchronous; the hermetic marker
is the same opt-in the crosswalk seam uses, so a build with no database says
so explicitly instead of silently producing spans with no operator boundaries.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.config import get_database_url
from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from clearinghouse_domain_legislative.operator_events import OperatorEvent, current_clause

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
    """Every current (neither superseded nor retracted, #468) operator event, oldest
    first — and, within one date, in curation order.

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
            .where(current_clause())
            .order_by(OperatorEvent.effective_date, OperatorEvent.id)
        )
    ).all()
    return [EventRow(*row) for row in rows]


@dataclass(frozen=True)
class SuccessionRow:
    """One current committee succession link, as ``org_lineage`` publishes it (#447).

    The WSL committee ids are the raw ends; ``conformed.lineage`` resolves each to
    its entity through the crosswalk. The row's own ULID and ``source_id`` stay
    behind: a year-only correction re-mints both, and the published identity is
    the ``(subject, slug, linked)`` edge, refined in place (#127).
    """

    subject_source_id: str
    linked_source_id: str
    slug: str
    effective_year: int | None
    evidence_url: str
    notes: str | None


async def succession_link_rows(session: AsyncSession) -> list[SuccessionRow]:
    """Every current (non-superseded) succession link, in edge order.

    Superseded rows drop out here, so a supersede retracts the old edge from the
    next published version. The ULID tiebreak keeps the read total even when two
    current rows share an edge (the ``org_lineage_key`` test refuses that build,
    but the hand-review table it leaves must still reproduce).
    """
    rows = (
        await session.execute(
            select(
                CommitteeSuccessionEvent.subject_source_id,
                CommitteeSuccessionEvent.linked_source_id,
                CommitteeSuccessionEvent.slug,
                CommitteeSuccessionEvent.effective_year,
                CommitteeSuccessionEvent.evidence_url,
                CommitteeSuccessionEvent.notes,
            )
            .where(CommitteeSuccessionEvent.superseded_by_id.is_(None))
            .order_by(
                CommitteeSuccessionEvent.subject_source_id,
                CommitteeSuccessionEvent.slug,
                CommitteeSuccessionEvent.linked_source_id,
                CommitteeSuccessionEvent.id,
            )
        )
    ).all()
    return [SuccessionRow(*row) for row in rows]


def _read_sync(reader: Callable[[AsyncSession], Awaitable[list[Any]]]) -> list[Any]:
    """Run one async read on its own engine off ``DATABASE_URL`` — or nothing,
    under the hermetic marker only."""
    if os.environ.get(HERMETIC_ENV) == "1":
        return []
    database_url = get_database_url()

    async def _read() -> list[Any]:
        engine = create_async_engine(database_url)
        try:
            async with AsyncSession(engine) as session:
                return await reader(session)
        finally:
            await engine.dispose()

    return list(asyncio.run(_read()))


def operator_events() -> list[Any]:
    """Sync wrapper for dbt Python models: own engine off ``DATABASE_URL``."""
    return _read_sync(operator_event_rows)


def succession_links() -> list[SuccessionRow]:
    """Sync wrapper for the ``org_lineage`` model (#447)."""
    return _read_sync(succession_link_rows)
