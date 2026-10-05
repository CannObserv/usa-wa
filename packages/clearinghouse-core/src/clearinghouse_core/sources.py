"""The configured data feeds — :class:`Source`, one row per external feed.

What survives of the Postgres provenance spine (``provenance.py`` until #412 PR F). The
spine's other tables — ``fetch_events``, ``raw_payloads``, ``citations``, ``notes`` and
``document_identifiers`` — dropped with the canonical tier: fetched bytes live in the #304
raw store, and every published entity's citations ride in the published ``citations``
dataset. A source row stays because ``/api/v1/sources`` lists the feeds and their #180
coverage claims (:mod:`clearinghouse_core.source_coverage`) hang off it.

The table lives in the ``clearinghouse_core`` Postgres schema.
"""

from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "clearinghouse_core"


def _new_ulid() -> _ULID:
    """Default factory for ULID PK columns. Captures the row's creation time."""
    return _ULID()


class RetentionPolicy(StrEnum):
    """How long a Source's fetched bodies should be kept (#54).

    ``operational_cache`` (default) — bodies are an operational cache, eligible
    for GC past the source's ``cache_ttl_days``. ``archival`` — provenance-critical
    source whose bodies are a long-lived tamper-evident record; a GC must never
    delete them. No GC exists: the Postgres ``RawPayload`` cache it was declared
    for dropped in #412 PR F, and the raw store deletes nothing. Stored as a String,
    not a native PG enum, so adding a value later is a data change, not a DDL
    migration."""

    operational_cache = "operational_cache"
    archival = "archival"


class Source(Base, TimestampMixin):
    """A configured data source feeding the clearinghouse.

    One row per (jurisdiction, external feed) pair — e.g., the WA Legislature
    SOAP service, the WA PDC HTTP API, the RCW corpus.
    """

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_sources_slug"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # soap/http/csv/scrape
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reliability: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    cache_ttl_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    retention_policy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=RetentionPolicy.operational_cache.value,
        server_default=RetentionPolicy.operational_cache.value,
    )
    """Payload-retention contract for this source (#54). Defaults to
    ``operational_cache``; provenance-critical feeds set ``archival`` to opt out
    of any future GC. See :class:`RetentionPolicy`."""
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    jurisdiction: Mapped[Jurisdiction] = relationship()
