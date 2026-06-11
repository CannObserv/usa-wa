"""Reusable test doubles for the sync engine.

Shipped in ``src`` (not a test conftest) so both this package's tests and a
sibling's tests can import stable symbols — ``from clearinghouse_sync_powermap``
``.testing import FakeDescriptor`` — without the conftest-name collisions that
arise when multiple packages each define a root ``conftest``.

Importing this module registers :class:`FakeEntity` (schema ``sync_test``) on
``Base.metadata``. The package ``__init__`` does NOT import it, so it never
reaches production metadata or alembic autogen — only test runs that import it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin
from clearinghouse_sync_powermap.client import ChangePage, EntityPage, ObservationResult
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid
from clearinghouse_sync_powermap.models import DISPOSITION_NEW

TEST_SCHEMA = "sync_test"


def parse_ts(value: str) -> datetime:
    """Parse a PM ISO-8601 timestamp (``...Z``) into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FakeEntity(Base, TimestampMixin):
    """Minimal cache table standing in for a real synced entity."""

    __tablename__ = "fake_entities"
    __table_args__ = {"schema": TEST_SCHEMA}

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_ULID)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    pm_fake_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)


class FakeDescriptor(EntityDescriptor):
    """Descriptor over :class:`FakeEntity` with reconcile-style reads + writes."""

    entity_type = "fake"
    model = FakeEntity
    anchor_column = "pm_fake_id"
    natural_key = ("source", "source_id")
    authority = "local"
    read_path = "/api/v1/fakes"
    observe_path = "/api/v1/fakes/observations"
    read_source = "reconcile"
    write_enabled = True

    async def to_observation(self, session: Any, row: Any) -> dict:
        return {"source": row.source, "source_id": row.source_id, "name": row.name}

    async def local_match(self, session: Any, record: dict) -> Any | None:
        return (
            await session.execute(
                select(FakeEntity).where(
                    FakeEntity.source == record["source"],
                    FakeEntity.source_id == record["source_id"],
                )
            )
        ).scalar_one_or_none()

    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        row = existing if existing is not None else await self.local_match(session, record)
        if row is None:
            row = FakeEntity(source=record["source"], source_id=record["source_id"])
            session.add(row)
        row.name = record["name"]
        if record.get("id") is not None:
            row.pm_fake_id = as_ulid(record["id"])
        await session.flush()
        return row

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, FakeEntity):
            return obj.updated_at
        ts = obj.get("updated_at")
        return parse_ts(ts) if ts else None


class FakeClient:
    """In-memory PM client. Tests preset responses; read back ``posted``."""

    def __init__(
        self,
        *,
        changes_pages: list[ChangePage] | None = None,
        entity_pages: list[EntityPage] | None = None,
        entities: dict[Any, dict] | None = None,
        observation_result: ObservationResult | Any = None,
        search_pages: list[EntityPage] | None = None,
    ) -> None:
        self._changes_pages = list(changes_pages or [])
        self._entity_pages = list(entity_pages or [])
        self._entities = entities or {}
        self._observation_result = observation_result
        self._search_pages = list(search_pages or [])
        self.posted: list[tuple[str, dict]] = []
        self.searched: list[dict] = []

    async def get_changes(self, since: str | None, limit: int = 100) -> ChangePage:
        if self._changes_pages:
            return self._changes_pages.pop(0)
        return ChangePage(items=[], cursor=since)

    async def list_entities(self, read_path: str, params: dict | None = None) -> EntityPage:
        if self._entity_pages:
            return self._entity_pages.pop(0)
        return EntityPage(records=[], cursor=None)

    async def get_entity(self, read_path: str, pm_id: Any) -> dict | None:
        return self._entities.get(pm_id) or self._entities.get(str(pm_id))

    async def search_entities(
        self,
        search_path: str,
        *,
        q: str | None = None,
        identifier_type: str | None = None,
        identifier_value: str | None = None,
        jurisdiction: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> EntityPage:
        self.searched.append(
            {
                "path": search_path,
                "q": q,
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
                "jurisdiction": jurisdiction,
                "offset": offset,
            }
        )
        if self._search_pages:
            return self._search_pages.pop(0)
        return EntityPage(records=[], cursor=None)

    async def post_observation(self, observe_path: str, payload: dict) -> ObservationResult:
        self.posted.append((observe_path, payload))
        result = self._observation_result
        if callable(result):
            return result(payload)
        if result is None:
            return ObservationResult(disposition=DISPOSITION_NEW, pm_id=_ULID(), raw={})
        return result
