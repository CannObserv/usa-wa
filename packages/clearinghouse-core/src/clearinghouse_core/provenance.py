"""Provenance spine — jurisdiction-agnostic models that every canonical entity ties back to.

Every fact in the clearinghouse traces to a :class:`FetchEvent` (which produced
the bytes that yielded the fact), a :class:`Source` (the configured data feed),
a :class:`Jurisdiction` (the political body the data describes), and zero-or-more
:class:`Citation` rows (field-level provenance for facts that need it). The
polymorphic :class:`Note` table attaches editorial / staff-summary / clarification
notes to any canonical entity, with optional author attribution.

All tables live in the ``clearinghouse_core`` Postgres schema.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, CreatedAtMixin, TimestampMixin

SCHEMA = "clearinghouse_core"


def _new_ulid() -> _ULID:
    """Default factory for ULID PK columns. Captures the row's creation time."""
    return _ULID()


class JurisdictionLevel(StrEnum):
    state = "state"
    federal = "federal"
    municipal = "municipal"
    country = "country"


class FetchStatus(StrEnum):
    ok = "ok"
    err = "err"
    skipped = "skipped"


class Jurisdiction(Base, TimestampMixin):
    """A political body whose data the clearinghouse ingests.

    Natural key is ``slug`` (e.g., ``'usa-wa'``). Used as the ``jurisdiction_id``
    text value on every canonical entity in domain packages.
    """

    __tablename__ = "jurisdictions"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_jurisdictions_slug"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[JurisdictionLevel] = mapped_column(String(16), nullable=False)


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
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    jurisdiction: Mapped[Jurisdiction] = relationship()


class FetchEvent(Base, CreatedAtMixin):
    """One fetch operation against a Source.

    Append-only. Metadata persists forever; the body bytes in :class:`RawPayload`
    age out per the source's ``cache_ttl_days``.

    ``resource_id`` is the source's stable identifier for a particular thing being
    fetched (e.g., ``"HB-1234-2025-26"``). The :class:`AdapterRunner` uses
    ``(source_id, resource_id)`` to find recent cached fetches.
    """

    __tablename__ = "fetch_events"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    source_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    resource_version_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[FetchStatus] = mapped_column(String(16), nullable=False)


class RawPayload(Base, CreatedAtMixin):
    """Cached fetched bytes for a FetchEvent.

    GC'd after the source's ``cache_ttl_days``. The parent FetchEvent persists,
    so callers can still find the original URL and route to Archiver for
    long-term content.
    """

    __tablename__ = "raw_payloads"
    __table_args__ = (
        UniqueConstraint("fetch_event_id", name="uq_raw_payloads_fetch_event"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    fetch_event_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.fetch_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Citation(Base, CreatedAtMixin):
    """Polymorphic provenance link.

    Each canonical fact (entity-level or field-level) carries one or more rows
    pointing back to the :class:`FetchEvent` that asserted it. ``entity_type``
    is a string discriminator (e.g., ``"bill"``, ``"legislator"``); ``entity_id``
    is the ULID of the referenced row. No DB-level FK enforcement on
    ``entity_id`` — by design, so a single Citation table can span domains.
    """

    __tablename__ = "citations"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False, index=True)
    fetch_event_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.fetch_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    field_path: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    asserted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Note(Base, TimestampMixin):
    """Polymorphic editorial / staff / provenance note attached to any canonical entity.

    Added v1.2 (2026-05-28) after the OCD transformation review surfaced multiple
    note-attachment use cases (amendment staff-summary, bill-version editorial
    notes, person biographical clarifications). Rather than a per-entity ``note``
    column, one table covers them all polymorphically — same pattern as
    :class:`Citation`.

    ``entity_type`` is a string discriminator (e.g., ``"bill"``, ``"bill_version"``,
    ``"amendment"``, ``"person"``, ``"organization"``); ``entity_id`` is the ULID of
    the referenced row. No DB-level FK on ``entity_id`` — by design, so a single
    notes table spans domains.

    Authorship attribution is optional. Common author shapes:

    - ``author_organization_id`` set to a chamber's Committee Services Office for
      non-partisan staff summaries of amendments.
    - ``author_person_id`` set to the analyst who wrote the note.
    - Both null when the note is editorial (sourced from the adapter itself).
    """

    __tablename__ = "notes"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False, index=True)
    note_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    """Vocabulary (free-text but documented): ``staff_summary`` (non-partisan
    staff-prepared effects description, e.g. for an Amendment), ``editorial``
    (adapter-derived), ``clarification`` (human curator clarification),
    ``provenance`` (an explanation of where a fact came from), ``other``."""

    text: Mapped[str] = mapped_column(Text, nullable=False)

    author_person_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)
    """Polymorphic, no DB FK. References ``canonical.persons.id`` when set."""

    author_organization_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)
    """Polymorphic, no DB FK. References ``canonical.organizations.id`` when set."""

    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
