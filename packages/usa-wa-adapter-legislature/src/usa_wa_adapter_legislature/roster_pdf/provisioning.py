"""Roster-PDF Source provisioning (#225) — get-or-create ``usa_wa_legislature_roster``.

Its **own** Source row, distinct from the WSL SOAP ``usa_wa_legislature`` source, per the
multi-source target pattern: same jurisdiction and target, different publisher and archive. The
SOS filings/results pair is the precedent.

``kind="document"`` rather than ``rest``/``soap``: this feed is a published PDF edition, not an
API, and the distinction is what makes the quarterly cadence legible next to the daily wires.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import RetentionPolicy, Source
from clearinghouse_core.source_coverage import seed_source_coverage
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_COVERAGE, ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.transport import DEFAULT_ROSTER_URL


async def get_or_create_roster_source(session: AsyncSession, jurisdiction: Jurisdiction) -> Source:
    """Get-or-create the ``usa_wa_legislature_roster`` :class:`Source` (idempotent).

    Seeds the declared coverage claim on **both** paths, like every sibling provisioner: a row
    predating the coverage table would otherwise never acquire one.
    """
    existing = (
        await session.execute(select(Source).where(Source.slug == ROSTER_SOURCE_SLUG))
    ).scalar_one_or_none()
    if existing is not None:
        await seed_source_coverage(session, existing, ROSTER_COVERAGE)
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA Legislature — Members of the Legislature (roster PDF)",
        slug=ROSTER_SOURCE_SLUG,
        kind="document",
        base_url=DEFAULT_ROSTER_URL,
        reliability=1.0,
        # The document changes ~biennially; nothing about it is a short-lived cache.
        cache_ttl_days=90,
        # The archived PDF is the provenance record every pre-1991 fact will cite (#54).
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    await seed_source_coverage(session, row, ROSTER_COVERAGE)
    return row
