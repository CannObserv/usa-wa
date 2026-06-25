"""Role descriptor — named slots within an Organization, observed by (org, title).

Unlike organizations, PM matches a role observation on its **structural key**
``(organization_id, title)`` — exactly the pair we send — so an observation
auto-attaches to PM's backfilled roles natively; there is no identifier-less-
backfill gap to bridge with a name cascade. What roles *do* require is **ordering**:
the observation carries the org's *PM* id, so the org must be anchored first. The
:meth:`dependencies_ready` gate defers delivery (no crash, no duplicate) until it
is, and the engine retries on later cycles.

Title-variance caveat: PM's ``(org_id, title)`` match is exact, so a title that
differs from PM's curated form (e.g. "Vice Chair" vs "Vice-Chair") would create a
new role rather than attach. Role titles are a short controlled vocabulary, so the
risk is low; an org-scoped normalized-title cascade (``list_roles?organization_id``
is available) is the refinement if duplicates appear.

Read strategy mirrors the org descriptor: ``feed`` but update-only — feed changes
to an already-anchored role are applied (adopt PM's title); roles we never produced
are skipped, not mirrored (``local_match`` keys on the anchor).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization, Role
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid, parse_pm_timestamp

logger = get_logger(__name__)


class RoleDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_domain_legislative.identity.Role`` to PM."""

    entity_type = "role"
    model = Role
    anchor_column = "pm_role_id"
    retired_column = "retired_at"  # merge-orphan tombstone (#31); no id re-match yet → log-and-skip
    natural_key = ("source", "source_id")
    authority = "pm"
    read_path = "/api/v1/roles"
    observe_path = "/api/v1/roles/observations"
    read_source = "feed"
    # Cohort-only producer: feed is the primary read; the bounded anchored-cohort
    # backstop re-fetches only OUR anchored rows to recover dropped feed events (#13).
    reconcile_mode = "anchored_cohort"
    write_enabled = True

    async def dependencies_ready(self, session: Any, row: Any) -> bool:
        """The role's org must be anchored — its PM id is the observation's key."""
        return await self._org_pm_id(session, row) is not None

    async def _org_pm_id(self, session: Any, row: Any) -> Any | None:
        org = await session.get(Organization, row.organization_id)
        return org.pm_organization_id if org is not None else None

    async def to_observation(self, session: Any, row: Any) -> dict:
        # dependencies_ready guarantees the org is anchored before delivery.
        org_pm_id = await self._org_pm_id(session, row)
        return {"organization_id": str(org_pm_id), "title": row.name}

    async def local_match(self, session: Any, record: dict) -> Any | None:
        """Map a PM role to its local row by **anchor** (``pm_role_id``).

        PM roles carry no usa-wa natural key; the durable link is the anchor.
        ``None`` for a role we never produced → :meth:`upsert_from_pm` skips it."""
        pm_id = record.get("id")
        if pm_id is None:
            return None
        return (
            await session.execute(select(Role).where(Role.pm_role_id == as_ulid(pm_id)))
        ).scalar_one_or_none()

    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        """Apply a PM role onto the local cache — **update-only** (see org descriptor)."""
        row = existing if existing is not None else await self.local_match(session, record)
        if row is None:
            return None
        title = record.get("title")
        if title:
            row.name = title  # adopt PM's curated title
        if record.get("id") is not None:
            row.pm_role_id = as_ulid(record["id"])
        self.mirror_archival(row, record)  # PM archival → retirement tombstone (#41)
        await session.flush()
        return row

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Role):
            return obj.updated_at
        return parse_pm_timestamp(obj.get("updated_at"))
