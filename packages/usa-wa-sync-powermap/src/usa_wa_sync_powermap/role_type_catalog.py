"""Role-type catalog sync (power-map#268, usa-wa#68).

Fetches PM's ``role_types`` catalog (``GET /api/v1/role-types``) and upserts the local
:class:`clearinghouse_domain_legislative.role_types.RoleType` mirror, keyed on ``slug``.
The :class:`~usa_wa_sync_powermap.descriptors.role.RoleDescriptor` reads that mirror to
decide a Role observation's shape (seat vs title) at runtime — this is what lets us
retire the hardcoded seat-slug map and track PM's catalog as it grows.

PM is read-only source of truth; usa-wa never writes role_types back. Idempotent:
an existing slug is updated in place (display_name / expects_jurisdiction /
requires_qualifier / anchor), never duplicated. A slug PM no longer lists is **demoted**
(both ``expects_jurisdiction`` and ``requires_qualifier`` reset to ``False``, power-map#273)
rather than deleted, so a retired or reclassified type stops driving seat-mode observations
and stops enforcing a qualifier PM no longer declares — a flag flip is not inert. The row
itself is kept (there is no FK from ``roles.role_type``; the slug is historical) and
re-promoted if PM lists it again.

PM 0.7.0 renamed the catalog field ``is_seat`` → ``expects_jurisdiction`` (power-map#271).
The read tolerates the legacy key as a fallback for raw-dict / legacy-payload callers;
note this does NOT rescue a client pinned to the old PM schema (the generated
``RoleType.from_dict`` would raise on the now-required ``expects_jurisdiction`` before this
sync sees the row), only callers that hand us untyped dicts.
"""

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.role_types import RoleType
from clearinghouse_sync_powermap.descriptors import as_ulid

logger = get_logger(__name__)


class _CatalogClient(Protocol):
    """The one PM operation this sync needs (a subset of ``PowerMapClient``)."""

    async def list_role_types(self) -> list[dict]: ...


async def sync_role_type_catalog(session: AsyncSession, client: _CatalogClient) -> int:
    """Upsert PM's role_types catalog into the local mirror; return the row count seen.

    Keyed on ``slug`` (PM's stable match value). A row present locally is updated in
    place; a new slug is inserted; a slug PM no longer lists is demoted to
    ``expects_jurisdiction=False`` so the descriptor stops treating a retired type as a
    seat."""
    rows: list[dict[str, Any]] = await client.list_role_types()
    existing = {r.slug: r for r in (await session.execute(select(RoleType))).scalars().all()}
    seen: set[str] = set()
    for row in rows:
        slug = row.get("slug")
        if not slug:
            logger.warning("role_type_catalog_row_missing_slug", extra={"row": row})
            continue
        seen.add(slug)
        pm_id = row.get("id")
        target = existing.get(slug)
        if target is None:
            target = RoleType(slug=slug)
            session.add(target)
        target.display_name = row.get("display_name") or slug
        # power-map#271 renamed this field; tolerate the legacy key for raw-dict callers.
        expects_jurisdiction = row.get("expects_jurisdiction")
        if expects_jurisdiction is None:
            expects_jurisdiction = row.get("is_seat")
        target.expects_jurisdiction = bool(expects_jurisdiction)
        # power-map#273: PM's enforced qualifier constraint. Absent (older PM / raw-dict
        # caller) → False (unconstrained), the safe default.
        target.requires_qualifier = bool(row.get("requires_qualifier"))
        if pm_id is not None:
            target.pm_role_type_id = as_ulid(pm_id)
    # Reconcile rows PM no longer lists: demote (never delete — no FK, historical slug)
    # so a retired/reclassified type stops driving seat-mode observations and stops
    # enforcing a qualifier requirement PM no longer declares.
    for slug, target in existing.items():
        if slug not in seen and (target.expects_jurisdiction or target.requires_qualifier):
            logger.info("role_type_catalog_demoted_absent_slug", extra={"slug": slug})
            target.expects_jurisdiction = False
            target.requires_qualifier = False
    await session.flush()
    return len(rows)
