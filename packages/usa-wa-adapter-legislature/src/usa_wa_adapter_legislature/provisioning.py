"""WSL Source provisioning — get-or-create the ``usa_wa_legislature`` SOAP Source row.

``usa_wa_pipeline.coverage_seed`` calls it nightly to reconcile the source's declared
coverage (#180). It lived as an underscore-private in ``refresh`` and was imported across
half a dozen modules — every WSL-facing runner path (the daily refresh, the historical
harvests, the seed ingest) needed the row to drive an ``AdapterRunner`` — so it was promoted
here to a shared public surface (CR #77). Those paths left with the canonical tier in
#412 PR F.

The **Jurisdiction** lookup that used to sit beside it moved to
:mod:`usa_wa_common.jurisdiction` at #189: `usa-wa` is the deployment's jurisdiction, not
this adapter's, and the PDC harvest plus both SOS harvests — pure sourcing modules — were
importing a SOAP adapter to reach it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.source_coverage import seed_source_coverage
from clearinghouse_core.sources import RetentionPolicy, Source
from usa_wa_adapter_legislature.coverage import WSL_COVERAGE, WSL_SOURCE_SLUG
from usa_wa_adapter_legislature.transport import WSL_BASE_URL


async def get_or_create_source(session: AsyncSession, jurisdiction: Jurisdiction) -> Source:
    """Get-or-create the ``usa_wa_legislature`` SOAP :class:`Source` (idempotent).

    Also seeds the source's declared coverage claims (#180). On **both** paths, not only on
    create: a deployment whose ``Source`` row predates the coverage table would otherwise never
    acquire one, and the ``docs/ARCHITECTURE.md`` checklist step this backs — *coverage rows must
    exist before an application builds on the feed* — is worth holding by construction rather
    than by remembering to run something. :func:`seed_source_coverage` is a no-op write when the
    rows already match the declaration, so the steady-state cost is one indexed SELECT.
    """
    existing = (
        await session.execute(select(Source).where(Source.slug == "usa_wa_legislature"))
    ).scalar_one_or_none()
    if existing is not None:
        await seed_source_coverage(session, existing, WSL_COVERAGE)
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA State Legislature SOAP",
        slug=WSL_SOURCE_SLUG,
        kind="soap",
        base_url=WSL_BASE_URL,
        reliability=1.0,
        cache_ttl_days=1,
        # Provenance-critical: the archived SOAP wire (#54) is a long-lived tamper-evident
        # record, not an operational cache — exempt from any future GC.
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    await seed_source_coverage(session, row, WSL_COVERAGE)
    return row
