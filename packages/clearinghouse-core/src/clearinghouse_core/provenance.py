"""Provenance spine — jurisdiction-agnostic models that every canonical entity ties back to.

Every fact in the clearinghouse traces to a :class:`FetchEvent` (which produced
the bytes that yielded the fact), a :class:`Source` (the configured data feed),
a :class:`~clearinghouse_core.jurisdictions.Jurisdiction` (the political body
the data describes — defined in :mod:`clearinghouse_core.jurisdictions`), and
zero-or-more :class:`Citation` rows (field-level provenance for facts that need
it). The polymorphic :class:`Note` table attaches editorial / staff-summary /
clarification notes to any canonical entity, with optional author attribution.

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.models import Base, CreatedAtMixin, TimestampMixin

SCHEMA = "clearinghouse_core"


def _new_ulid() -> _ULID:
    """Default factory for ULID PK columns. Captures the row's creation time."""
    return _ULID()


class FetchStatus(StrEnum):
    ok = "ok"
    err = "err"
    skipped = "skipped"


class RetentionPolicy(StrEnum):
    """How long a Source's :class:`RawPayload` bodies should be kept (#54).

    ``operational_cache`` (default) — bodies are an operational cache, eligible
    for GC past the source's ``cache_ttl_days`` (the GC itself is not yet built;
    today every payload is de-facto retained). ``archival`` — provenance-critical
    source whose bodies are a long-lived tamper-evident record; a future GC must
    never delete them. Stored as a String (FetchStatus precedent), not a native
    PG enum, so adding a value later is a data change, not a DDL migration."""

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
    of the (not-yet-built) RawPayload GC. See :class:`RetentionPolicy`."""
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
    """sha256 over *exactly* the bytes in the paired :class:`RawPayload.body`,
    pre-any-app-compression, no normalization — the integrity baseline (#54). The
    :class:`~clearinghouse_core.runner.AdapterRunner` always populates this
    (adapter-supplied digest wins, else derived ``sha256(body)``), so rows written
    since #54 are never NULL. The column stays nullable only for the legacy tail
    fetched before #54; NULL means "unbaselined," NOT a mismatch — an integrity
    sweep must skip-and-count those separately and must never treat NULL (or an
    all-zeros sentinel) as a verified hash."""
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


class DocumentIdentifier(Base, TimestampMixin):
    """Polymorphic identifier mapping for bill texts, amendments, and similar.

    Added v1.3 (2026-05-29) after OCD review #2 follow-up surfaced that WA bill
    texts and amendments carry rich, parseable identifiers issued by the Code
    Reviser and committee staff — e.g., ``H-0043.1`` (Code Reviser bill text ID),
    ``S-5276.3/26`` (Code Reviser striking amendment), ``1066 AMH CPB CLOD 295``
    (committee amendment with bill / chamber / committee / drafter / sequence).
    These don't fit on the overall ``Bill`` entity (which stays stably "HB 1941"
    or "SB 5069") — they identify *texts* and *amendments* below the Bill level.

    Polymorphic same-pattern as :class:`Citation` and :class:`Note`. ``entity_type``
    is the table name discriminator (``"bill_version"`` or ``"amendment"`` for
    legislative use; the table is reusable across domains). ``entity_id`` is the
    referenced row's ULID; no DB FK so the table spans domains.

    Scheme slugs are jurisdiction-prefixed (``usa_wa_code_reviser``,
    ``usa_wa_committee_amendment``, ``usa_wa_lifecycle_tag``) so future
    jurisdictions add their own without collision.

    ``parsed_components`` is a JSONB column populated by P1b enrichment when
    parsers exist (e.g., decomposing ``1066 AMH CPB CLOD 295`` into
    ``{bill_number, chamber, committee_abbr, drafter_initials, sequence}``).
    Raw ``value`` is the authoritative form; ``parsed_components`` is derivative.
    """

    __tablename__ = "document_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_document_identifiers_natural_key",
        ),
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "scheme",
            name="uq_document_identifiers_entity_scheme",
        ),
        UniqueConstraint(
            "jurisdiction_id",
            "entity_type",
            "scheme",
            "value",
            name="uq_document_identifiers_jurisdiction_entity_scheme_value",
        ),
        # v1.3 (2026-05-30): the uniqueness constraint includes entity_type
        # because a single identifier (e.g., WA Code Reviser "H-0734.1/25")
        # legitimately attaches to BOTH the Amendment row AND the resulting
        # BillVersion row when a substitute/striking amendment becomes a new
        # bill text. The previous (jurisdiction_id, scheme, value) UNIQUE
        # blocked that pattern.
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    """Table-name discriminator: ``bill_version`` | ``amendment`` (extensible)."""

    entity_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False, index=True)
    """Polymorphic, no DB FK. References the row in the table named by ``entity_type``."""

    scheme: Mapped[str] = mapped_column(String(64), nullable=False)
    """Jurisdiction-prefixed identifier scheme slug, e.g., ``usa_wa_code_reviser``."""

    value: Mapped[str] = mapped_column(String(256), nullable=False)
    """The identifier as published by the issuing authority."""

    parsed_components: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """Derived structured decomposition of ``value`` populated by P1b enrichment."""

    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
