"""Source provisioning for the PDC adapter — get-or-create the PDC Source row.

The PDC sibling of :mod:`usa_wa_adapter_legislature.provisioning`, called nightly by
``usa_wa_pipeline.coverage_seed`` for the ``usa_wa_pdc`` REST :class:`Source` and its
coverage claims. Promoted from ``refresh``'s underscore-private to a shared public surface
(CR #77) so the #79 harvest could reuse it; both runner paths left with the canonical tier in
#412 PR F. The ``usa-wa`` Jurisdiction resolve stays generic —
:func:`usa_wa_common.jurisdiction.resolve_jurisdiction`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.source_coverage import seed_source_coverage
from clearinghouse_core.sources import RetentionPolicy, Source
from usa_wa_adapter_pdc.coverage import PDC_COVERAGE, PDC_SOURCE_SLUG
from usa_wa_adapter_pdc.transport import PDC_BASE_URL


async def get_or_create_source(session: AsyncSession, jurisdiction: Jurisdiction) -> Source:
    """Get-or-create the ``usa_wa_pdc`` REST :class:`Source` (idempotent).

    Seeds the source's declared coverage claims (#180) on both paths — see
    :func:`usa_wa_adapter_legislature.provisioning.get_or_create_source` for why.
    """
    existing = (
        await session.execute(select(Source).where(Source.slug == PDC_SOURCE_SLUG))
    ).scalar_one_or_none()
    if existing is not None:
        await seed_source_coverage(session, existing, PDC_COVERAGE)
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA Public Disclosure Commission",
        slug=PDC_SOURCE_SLUG,
        kind="rest",
        base_url=PDC_BASE_URL,
        reliability=1.0,
        cache_ttl_days=1,
        # The archived SODA JSON (#54) is a long-lived provenance record, not an
        # operational cache — exempt from any future GC.
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    await seed_source_coverage(session, row, PDC_COVERAGE)
    return row
