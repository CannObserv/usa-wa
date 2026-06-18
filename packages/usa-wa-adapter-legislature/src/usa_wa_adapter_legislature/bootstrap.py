"""Bootstrap — idempotent DB seed of the WSL anchor rows.

Materializes the legislature Org, the House + Senate chamber Orgs, the
biennium-classified parent session, and the two regular sessions of that
biennium. Returns a :class:`BootstrapAnchors` carrying the IDs so callers
(the adapter + runner) can wire FK references without re-querying.

Idempotent: each row is upserted on its natural key
(``(source, source_id)``) via ``INSERT ... ON CONFLICT DO NOTHING``, and the
ID is read back. Re-running yields the same anchor IDs and writes no new rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.sessions import LegislativeSession
from usa_wa_adapter_legislature.synthesis import (
    biennium_session,
    chamber_orgs,
    legislature_org,
    regular_sessions,
)


@dataclass(frozen=True)
class BootstrapAnchors:
    """IDs of the rows materialized by :func:`bootstrap_synthetic_anchors`."""

    legislature_id: _ULID
    house_id: _ULID
    senate_id: _ULID
    biennium_session_id: _ULID
    regular_session_ids: dict[int, _ULID] = field(default_factory=dict)


async def _upsert_org(session: AsyncSession, row: dict) -> _ULID:
    """Insert an Organization row by natural key; return the existing-or-new id."""
    stmt = (
        pg_insert(Organization)
        .values(**row)
        .on_conflict_do_nothing(index_elements=["source", "source_id"])
    )
    await session.execute(stmt)
    fetched = (
        await session.execute(
            select(Organization.id).where(
                Organization.source == row["source"],
                Organization.source_id == row["source_id"],
            )
        )
    ).scalar_one()
    return fetched


async def _upsert_session(session: AsyncSession, row: dict) -> _ULID:
    """Insert a LegislativeSession by natural key; return the existing-or-new id."""
    stmt = (
        pg_insert(LegislativeSession)
        .values(**row)
        .on_conflict_do_nothing(index_elements=["source", "source_id"])
    )
    await session.execute(stmt)
    fetched = (
        await session.execute(
            select(LegislativeSession.id).where(
                LegislativeSession.source == row["source"],
                LegislativeSession.source_id == row["source_id"],
            )
        )
    ).scalar_one()
    return fetched


async def bootstrap_synthetic_anchors(
    session: AsyncSession,
    *,
    biennium: str,
    jurisdiction_id: _ULID,
) -> BootstrapAnchors:
    """Materialize the WSL anchor rows and return their IDs."""
    leg_row = legislature_org(jurisdiction_id)
    legislature_id = await _upsert_org(session, leg_row)

    chambers = chamber_orgs(legislature_id, jurisdiction_id)
    house_id = await _upsert_org(session, chambers[0])
    senate_id = await _upsert_org(session, chambers[1])

    biennium_row = biennium_session(legislature_id, biennium)
    biennium_session_id = await _upsert_session(session, biennium_row)

    regular_rows = regular_sessions(biennium_session_id, legislature_id, biennium)
    regular_ids: dict[int, _ULID] = {}
    for row in regular_rows:
        # ``source_id`` is ``'session:YYYY'``; the year is the dispatch key.
        year = int(row["source_id"].removeprefix("session:"))
        regular_ids[year] = await _upsert_session(session, row)

    await session.flush()
    return BootstrapAnchors(
        legislature_id=legislature_id,
        house_id=house_id,
        senate_id=senate_id,
        biennium_session_id=biennium_session_id,
        regular_session_ids=regular_ids,
    )
