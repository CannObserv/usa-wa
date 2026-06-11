"""Jurisdiction descriptor — local cache mirror of PM-authoritative jurisdictions.

PM is the system of record; usa-wa caches jurisdictions locally. Reads come off
the changes feed (jurisdictions are on it since PM #179); writes go out as
observations keyed on the ``jur_slug`` identifier (PM #183) so they AUTO_ATTACH
to the bootstrap-imported rows instead of minting duplicates.

Scope note: the feed/reconcile reads are currently unfiltered, so this mirrors
*every* PM jurisdiction, not just the WA subset. Bounding that to a subscription
is tracked in CannObserv/usa-wa#10 (needs PM-side per-key feed filtering).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid

logger = get_logger(__name__)


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class JurisdictionDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_core.jurisdictions.Jurisdiction`` to PM."""

    entity_type = "jurisdiction"
    model = Jurisdiction
    anchor_column = "pm_jurisdiction_id"
    natural_key = ("slug",)
    authority = "pm"  # PM is system-of-record for jurisdictions
    read_path = "/api/v1/jurisdictions"
    observe_path = "/api/v1/jurisdictions/observations"
    read_source = "feed"
    # Full-mirror, PM-authoritative, bounded list → the full-list reconcile backstop
    # is correct and wanted (catches anything the feed dropped). See #13.
    reconcile_enabled = True
    # jur_slug identifier type is live (PM #183), so observations AUTO_ATTACH to
    # the bootstrap-imported rows instead of minting duplicates.
    write_enabled = True

    async def to_observation(self, session: Any, row: Any) -> dict:
        jt = await session.get(JurisdictionType, row.type_id)
        if jt is None:
            # type_id is a NOT NULL FK; a miss means an orphaned reference. Emit a
            # null slug (PM will reject → surfaced on the outbox) but log loudly
            # rather than raising, which would poison the drain cycle.
            logger.warning(
                "jurisdiction_type_unresolved",
                extra={"slug": row.slug, "type_id": str(row.type_id)},
            )
        return {
            "identifier_type": "jur_slug",
            "identifier_value": row.slug,
            "jurisdiction_slug": row.slug,
            "jurisdiction_name": row.name,
            "jurisdiction_type_slug": jt.slug if jt is not None else None,
            "jurisdiction_valid_from": _iso(row.valid_from),
            "jurisdiction_valid_until": _iso(row.valid_until),
        }

    async def local_match(self, session: Any, record: dict) -> Any | None:
        return (
            await session.execute(select(Jurisdiction).where(Jurisdiction.slug == record["slug"]))
        ).scalar_one_or_none()

    async def _type_id_for(self, session: Any, record: dict) -> Any | None:
        """Resolve the local jurisdiction-type id, minting the type on first sight.

        PM exposes no jurisdiction-types list endpoint, but every jurisdiction
        record embeds its full type object. So an unknown slug is "synced" from
        the embedded ``{slug, display_name}`` rather than wedging the cycle on a
        NOT NULL violation (the local id is independent of PM's).
        """
        type_obj = record.get("type") or {}
        type_slug = type_obj.get("slug")
        if not type_slug:
            return None
        jt = (
            await session.execute(
                select(JurisdictionType).where(JurisdictionType.slug == type_slug)
            )
        ).scalar_one_or_none()
        if jt is None:
            jt = JurisdictionType(
                slug=type_slug,
                display_name=type_obj.get("display_name") or type_slug,
            )
            session.add(jt)
            await session.flush()
            logger.info("jurisdiction_type_minted", extra={"slug": type_slug})
        return jt.id

    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        row = existing if existing is not None else await self.local_match(session, record)
        type_id = await self._type_id_for(session, record)
        is_new = row is None
        if is_new:
            if type_id is None:
                # No resolvable type even after the mint attempt — skip rather than
                # insert an invalid NULL-type row that would poison the cycle.
                logger.warning("jurisdiction_skipped_no_type", extra={"slug": record.get("slug")})
                return None
            row = Jurisdiction(slug=record["slug"], type_id=type_id)
            session.add(row)
        elif type_id is not None:
            row.type_id = type_id
        row.name = record["name"]
        row.valid_from = _parse_ts(record.get("valid_from"))
        row.valid_until = _parse_ts(record.get("valid_until"))
        recorded = _parse_ts(record.get("recorded_at"))
        if is_new:
            # recorded_at is NOT NULL; stamp now() only when PM omits it on insert.
            row.recorded_at = recorded or datetime.now(UTC)
        elif recorded is not None:
            # On update, keep the prior value when PM omits it (no churn).
            row.recorded_at = recorded
        row.superseded_at = _parse_ts(record.get("superseded_at"))
        if record.get("id") is not None:
            row.pm_jurisdiction_id = as_ulid(record["id"])
        # NOTE: PM's updated_at is mirrored onto the row by the engine
        # (SyncEngine._adopt_remote_clock after upsert) so LWW sees parity — it
        # is not set here, to keep that invariant in one place for all descriptors.
        await session.flush()
        return row

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Jurisdiction):
            return obj.updated_at
        ts = obj.get("updated_at")
        return _parse_ts(ts) if ts else None
