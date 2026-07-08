"""Shared runner provisioning for the PDC adapter — get-or-create the PDC Source row.

The PDC sibling of :mod:`usa_wa_adapter_legislature.provisioning`: every PDC-facing runner
path (the daily refresh, and the historical harvest #79) needs the ``usa_wa_pdc`` REST
:class:`Source` before it can drive an :class:`~clearinghouse_core.runner.AdapterRunner`.
Promoted from ``refresh``'s underscore-private to a shared public surface (CR #77) so the
#79 harvest can reuse it. The ``usa-wa`` Jurisdiction resolve stays generic — reuse
:func:`usa_wa_adapter_legislature.provisioning.resolve_jurisdiction`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import RetentionPolicy, Source
from usa_wa_adapter_pdc.transport import PDC_BASE_URL


async def get_or_create_source(session: AsyncSession, jurisdiction: Jurisdiction) -> Source:
    """Get-or-create the ``usa_wa_pdc`` REST :class:`Source` (idempotent)."""
    existing = (
        await session.execute(select(Source).where(Source.slug == "usa_wa_pdc"))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA Public Disclosure Commission",
        slug="usa_wa_pdc",
        kind="rest",
        base_url=PDC_BASE_URL,
        reliability=1.0,
        cache_ttl_days=1,
        # The archived SODA JSON (#54) is a long-lived provenance record, not an
        # operational cache — exempt from any future RawPayload GC.
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    return row
