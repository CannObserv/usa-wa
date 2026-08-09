"""Shared runner provisioning for the SOS adapter — get-or-create the SOS Source row.

The votewa sibling of :mod:`usa_wa_adapter_pdc.provisioning`: every SOS-facing runner path (the
historical harvest, #100) needs the ``usa_wa_sos`` REST :class:`Source` before it can drive an
:class:`~clearinghouse_core.runner.AdapterRunner`. The ``usa-wa`` Jurisdiction resolve stays
generic — reuse :func:`usa_wa_adapter_legislature.provisioning.resolve_jurisdiction`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import RetentionPolicy, Source
from clearinghouse_core.source_coverage import seed_source_coverage
from usa_wa_adapter_sos.coverage import SOS_FILINGS_COVERAGE, SOS_RESULTS_COVERAGE
from usa_wa_adapter_sos.filings.transport import SOS_BASE_URL
from usa_wa_adapter_sos.results.transport import RESULTS_BASE_URL

#: The filings source slug — matches :attr:`SOSAdapter.source_slug` and its ``Source`` row.
SOS_SOURCE_SLUG = "usa_wa_sos"

#: The results source slug — matches :attr:`ResultsAdapter.source_slug` and its ``Source`` row.
RESULTS_SOURCE_SLUG = "usa_wa_sos_results"


async def get_or_create_source(session: AsyncSession, jurisdiction: Jurisdiction) -> Source:
    """Get-or-create the ``usa_wa_sos`` REST :class:`Source` (idempotent).

    Seeds this feed's declared coverage claims (#180) on both paths — including the ``absent``
    2020-onward claim, so the Power BI retirement is a queryable fact rather than prose. See
    :func:`usa_wa_adapter_legislature.provisioning.get_or_create_source` for why both paths.
    """
    existing = (
        await session.execute(select(Source).where(Source.slug == SOS_SOURCE_SLUG))
    ).scalar_one_or_none()
    if existing is not None:
        await seed_source_coverage(session, existing, SOS_FILINGS_COVERAGE)
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA Secretary of State (votewa)",
        slug=SOS_SOURCE_SLUG,
        kind="rest",
        base_url=SOS_BASE_URL,
        reliability=1.0,
        cache_ttl_days=1,
        # The archived filing CSV (#54) is a long-lived provenance record, not an operational
        # cache — exempt from any future RawPayload GC.
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    await seed_source_coverage(session, row, SOS_FILINGS_COVERAGE)
    return row


async def get_or_create_results_source(session: AsyncSession, jurisdiction: Jurisdiction) -> Source:
    """Get-or-create the ``usa_wa_sos_results`` REST :class:`Source` (idempotent) — the results
    source's own provenance root, distinct from the filings ``usa_wa_sos`` Source (#101).

    Seeds this feed's declared coverage claims (#180) on both paths.
    """
    existing = (
        await session.execute(select(Source).where(Source.slug == RESULTS_SOURCE_SLUG))
    ).scalar_one_or_none()
    if existing is not None:
        await seed_source_coverage(session, existing, SOS_RESULTS_COVERAGE)
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA Secretary of State (election results)",
        slug=RESULTS_SOURCE_SLUG,
        kind="rest",
        base_url=RESULTS_BASE_URL,
        reliability=1.0,
        cache_ttl_days=1,
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    await seed_source_coverage(session, row, SOS_RESULTS_COVERAGE)
    return row
