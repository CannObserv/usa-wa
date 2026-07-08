"""Shared DB helper for the PDC normalizers — resolve the existing WSL :class:`Person`.

PDC is not a Person source (#69/#75): every PDC winner is matched to a :class:`Person`
already created by the WSL member ingest (P1b), keyed on the stable WSL member id. Both
the House-position and Senate-identity normalizers SELECT that Person to hang their
`person_wa_pdc` identifier (and, for the House, the seat Assignment) off its real id.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_domain_legislative.identity import Person

#: The source slug the WSL member ingest stamps on its :class:`Person` rows.
WSL_SOURCE = "usa_wa_legislature"


async def resolve_wsl_person(session: AsyncSession, member_id: str) -> Person | None:
    """SELECT the WSL :class:`Person` by ``(source, member id)`` (``None`` if not yet
    ingested — its WSL refresh hasn't run)."""
    return (
        await session.execute(
            select(Person).where(Person.source == WSL_SOURCE, Person.source_id == member_id)
        )
    ).scalar_one_or_none()
