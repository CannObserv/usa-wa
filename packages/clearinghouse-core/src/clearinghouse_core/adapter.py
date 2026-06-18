"""Adapter contract — the source-specific surface every per-source package implements.

An adapter is a *pure transformer*: it knows how to talk to one external data
source, parse its responses, and produce canonical entities. Caching, retries,
provenance writing, and scheduling are not its concern — they live in
:class:`clearinghouse_core.runner.AdapterRunner`.

See ``docs/specs/2026-05-25-usa-wa-mvp-design.md`` § Adapter contract.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from clearinghouse_core.models import Base


@dataclass(frozen=True)
class ResourceRef:
    """A pointer to one fetchable thing in a source.

    Yielded by :meth:`BaseAdapter.discover`. The runner turns each ref into a
    :meth:`BaseAdapter.fetch_one` call.
    """

    resource_id: str
    """The source's stable identifier (e.g., ``"HB-1234-2025-26"``)."""

    version_key: str | None = None
    """Optional source-supplied version (etag, lastmod, version number). The
    runner uses this to skip re-fetching unchanged resources when supported."""


@dataclass(frozen=True)
class FetchedPayload:
    """In-flight result of :meth:`BaseAdapter.fetch_one`. Persisted by the runner."""

    url: str
    fetched_at: datetime
    content_type: str
    body: bytes
    http_status: int | None = None
    content_hash: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    resource_version_key: str | None = None


@dataclass
class NormalizedBatch:
    """Output of :meth:`BaseAdapter.normalize`.

    For MVP, an adapter returns canonical entity instances; the runner writes one
    default citation per entity tying it back to the fetch event. Adapters that
    want field-level citations can construct them and append to ``citations``
    directly — the runner persists those as-is.
    """

    entities: list[Base] = field(default_factory=list)
    """Canonical entities (SQLAlchemy model instances) to upsert."""

    citations: list["FactCitation"] = field(default_factory=list)
    """Optional field-level citations supplementing the default whole-entity citation."""


@dataclass(frozen=True)
class FactCitation:
    """An adapter-supplied citation for a specific field of an entity.

    The referenced ``entity`` must also appear in :attr:`NormalizedBatch.entities`.
    The runner upserts each entity and populates its ``.id`` with the persisted
    ULID before writing citations; a FactCitation pointing at an entity outside
    that list would carry whatever local ULID the adapter assigned (or ``None``)
    and the Citation row would dangle or raise a NOT NULL violation.
    """

    entity: Base
    field_path: str
    confidence: float = 1.0


class BaseAdapter(ABC):
    """Per-source pure transformer.

    Subclasses must declare ``source_slug``, ``schema_name``, and
    ``jurisdiction_slug`` as class variables. The runner uses these to look up
    the :class:`Source` row and tag persisted provenance.
    """

    source_slug: ClassVar[str]
    """Matches ``Source.slug`` and the Postgres schema namespace.
    Example: ``"usa_wa_legislature"``."""

    schema_name: ClassVar[str]
    """Postgres schema for this adapter's source-specific tables.
    Conventionally equal to ``source_slug``."""

    jurisdiction_slug: ClassVar[str]
    """Matches ``Jurisdiction.slug``. Example: ``"usa-wa"``."""

    @abstractmethod
    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Fetch the bytes for one resource. May raise on network/parse errors."""

    @abstractmethod
    def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        """Yield refs for resources that may have changed since ``since``."""

    @abstractmethod
    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        """Parse a fetched payload into canonical entities + optional citations."""
