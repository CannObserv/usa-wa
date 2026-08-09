"""The ``usa-wa`` Jurisdiction lookup (#189).

Every runner path in this deployment — WSL, PDC, both SOS sources, every fact builder —
needs the same pre-seeded Jurisdiction row before it can drive an
:class:`~clearinghouse_core.runner.AdapterRunner`. It is the deployment's jurisdiction, not
any adapter's, but it was defined in `usa_wa_adapter_legislature.provisioning` (alongside the
get-or-create of the **WSL SOAP Source**, which genuinely is that adapter's) and imported from
there by the PDC harvest and both SOS harvests — pure *sourcing* modules reaching into a peer
adapter for a row that has nothing to do with SOAP.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.jurisdictions import Jurisdiction

#: The slug the jurisdictional IA bootstrap seeds for this deployment.
JURISDICTION_SLUG = "usa-wa"


async def resolve_jurisdiction(session: AsyncSession) -> Jurisdiction:
    """Return the pre-seeded ``usa-wa`` Jurisdiction, or raise if the IA bootstrap
    hasn't run (it must exist before any runner path)."""
    row = (
        await session.execute(select(Jurisdiction).where(Jurisdiction.slug == JURISDICTION_SLUG))
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(
            f"Jurisdiction {JURISDICTION_SLUG!r} is not seeded — run the jurisdictional IA "
            "bootstrap before invoking any adapter run."
        )
    return row
