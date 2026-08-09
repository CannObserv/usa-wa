"""Shared runner provisioning — resolve the usa-wa Jurisdiction + the WSL Source row.

Every WSL-facing runner path (the daily refresh, the historical harvests, the seed
ingest, the archive-derived reconcilers) needs the same two rows before it can drive an
:class:`~clearinghouse_core.runner.AdapterRunner`: the ``usa-wa`` Jurisdiction (must be
pre-seeded) and the ``usa_wa_legislature`` SOAP Source (get-or-create, idempotent). These
lived as underscore-privates in :mod:`refresh` and were imported across half a dozen
modules — promoted here to a shared public surface (CR #77).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import RetentionPolicy, Source
from clearinghouse_core.source_coverage import seed_source_coverage
from usa_wa_adapter_legislature.coverage import WSL_COVERAGE
from usa_wa_adapter_legislature.transport import WSL_BASE_URL


async def resolve_jurisdiction(session: AsyncSession) -> Jurisdiction:
    """Return the pre-seeded ``usa-wa`` Jurisdiction, or raise if the IA bootstrap
    hasn't run (it must exist before any WSL runner path)."""
    row = (
        await session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(
            "Jurisdiction 'usa-wa' is not seeded — run the jurisdictional IA "
            "bootstrap before invoking the WSL refresh."
        )
    return row


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
        slug="usa_wa_legislature",
        kind="soap",
        base_url=WSL_BASE_URL,
        reliability=1.0,
        cache_ttl_days=1,
        # Provenance-critical: the archived SOAP wire (#54) is a long-lived tamper-evident
        # record, not an operational cache — exempt from any future RawPayload GC.
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    await seed_source_coverage(session, row, WSL_COVERAGE)
    return row
