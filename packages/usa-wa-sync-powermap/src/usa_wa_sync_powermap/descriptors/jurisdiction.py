"""Jurisdiction descriptor — local cache mirror of PM-authoritative jurisdictions.

PM is the system of record; usa-wa caches the WA-relevant subset. Reads come off
the changes feed (jurisdictions are on it since PM #179); writes go out as
observations keyed on the ``jur_slug`` identifier (PM #183) so they AUTO_ATTACH
to the bootstrap-imported rows instead of minting duplicates.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid


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
    # Write path fully AUTO_ATTACHes once PM #183 ships the jur_slug identifier
    # type; until then observations return `rejected` (surfaced on the outbox).
    write_enabled = True

    async def to_observation(self, session: Any, row: Any) -> dict:
        jt = await session.get(JurisdictionType, row.type_id)
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
        type_slug = (record.get("type") or {}).get("slug")
        if not type_slug:
            return None
        jt = (
            await session.execute(
                select(JurisdictionType).where(JurisdictionType.slug == type_slug)
            )
        ).scalar_one_or_none()
        return jt.id if jt is not None else None

    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        row = existing if existing is not None else await self.local_match(session, record)
        type_id = await self._type_id_for(session, record)
        if row is None:
            # type_id is NOT NULL; a new row needs a resolvable type.
            row = Jurisdiction(slug=record["slug"], type_id=type_id)
            session.add(row)
        elif type_id is not None:
            row.type_id = type_id
        row.name = record["name"]
        row.valid_from = _parse_ts(record.get("valid_from"))
        row.valid_until = _parse_ts(record.get("valid_until"))
        row.recorded_at = _parse_ts(record.get("recorded_at")) or datetime.now(UTC)
        row.superseded_at = _parse_ts(record.get("superseded_at"))
        if record.get("id") is not None:
            row.pm_jurisdiction_id = as_ulid(record["id"])
        await session.flush()
        return row

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Jurisdiction):
            return obj.updated_at
        ts = obj.get("updated_at")
        return _parse_ts(ts) if ts else None
