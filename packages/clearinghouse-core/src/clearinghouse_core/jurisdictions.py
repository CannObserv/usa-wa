"""Jurisdiction cache mirror — local-side copy of Power Map's Jurisdiction extension.

Power Map is the system of record for Jurisdictions (and their containment
graph). usa-wa caches the WA-relevant subset locally so canonical-table FKs
(``jurisdiction_id`` across ~30 tables, ``Role.jurisdiction_id``) can be enforced
at the database layer without a network round-trip per write.

Four tables, mirroring PM's shape (see
``docs/specs/2026-05-31-jurisdictional-ia-design.md`` §2):

- :class:`JurisdictionType` — type lookup (16 rows seeded by migration to match
  PM's ``jurisdiction_types``: ``country``, ``state``, ``county``, ``city``,
  ``legislative_district``, etc.).
- :class:`JurisdictionRelationshipType` — relationship-type lookup (11 codes;
  carries ``is_symmetric`` for symmetric relations like ``is_coterminous_with``
  and ``category`` for query filtering).
- :class:`Jurisdiction` — the entity row. ``type_id`` FK replaces the previous
  ``JurisdictionLevel`` StrEnum (4 values → 16 values via lookup). Bitemporal
  columns ``valid_from`` / ``valid_until`` / ``recorded_at`` / ``superseded_at``
  mirror PM's clock; ``created_at`` / ``updated_at`` from :class:`TimestampMixin`
  carry usa-wa's local-cache write times (separate axis).
- :class:`JurisdictionRelationship` — bitemporal junction over
  ``(subject, object, relationship_type)``.

All tables carry an optional ``pm_*_id`` nullable column pointing back to the
PM-side row, populated by the sidecar on sync (``AUTO_ATTACHED`` /
``NEW`` dispositions). Null = pending sidecar push.

Identifier caching (``clearinghouse_core.jurisdiction_identifiers``) was
dropped from MVP — PM's design review chose to extend the existing polymorphic
``identifiers`` table on the PM side; for usa-wa MVP, identifiers are resolved
via PM API when needed (deferred to the sidecar follow-up plan).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, CreatedAtMixin, TimestampMixin

SCHEMA = "clearinghouse_core"


def _new_ulid() -> _ULID:
    """Default factory for ULID PK columns. Captures the row's creation time."""
    return _ULID()


class JurisdictionType(Base, CreatedAtMixin):
    """Type lookup for :class:`Jurisdiction` (mirrors PM's ``jurisdiction_types``).

    Seeded by migration with the 16 PM-side values: ``country``, ``state``,
    ``county``, ``city``, ``legislative_district``, ``legislative_district_upper``,
    ``legislative_district_lower``, ``congressional_district``, ``judicial_district``,
    ``school_district``, ``water_district``, ``tribal_nation``, ``federal_enclave``,
    ``census_block``, ``census_tract``, ``other``.
    """

    __tablename__ = "jurisdiction_types"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_jurisdiction_types_slug"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)


class JurisdictionRelationshipType(Base, CreatedAtMixin):
    """Type lookup for :class:`JurisdictionRelationship`.

    Eleven codes seeded by migration (PM dropped
    ``exercises_concurrent_jurisdiction`` from MVP per design review):
    ``is_fully_contained_by``, ``partially_overlaps``, ``is_coterminous_with``,
    ``has_regulatory_authority_over``, ``has_extraterritorial_jurisdiction_over``,
    ``member_of``, ``reports_to``, ``contracts_services_from``, ``supersedes``,
    ``succeeded_by``, ``evolved_from``.

    ``category`` is one of ``spatial`` / ``governance`` / ``functional`` /
    ``temporal`` — for query filtering.

    ``is_symmetric`` is True for ``partially_overlaps`` and
    ``is_coterminous_with``; False for directed relations.
    """

    __tablename__ = "jurisdiction_relationship_types"
    __table_args__ = (
        UniqueConstraint("code", name="uq_jurisdiction_relationship_types_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    is_symmetric: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Optional long-form description; PM populates from PM-side rows. Null in
    MVP usa-wa seeds — adapters may fill it on observation pushes."""


class Jurisdiction(Base, TimestampMixin):
    """A bounded political / administrative area (country, state, county, district, ...).

    Natural key is ``slug`` (e.g., ``'usa-wa'``, ``'usa-wa-ld-21'``,
    ``'usa-wa-county-king'``); see the slug convention in the design spec §1.

    FK target for the ``jurisdiction_id`` column across the canonical schema and
    for :class:`canonical.Role.jurisdiction_id`. Replaces the prior minimal
    ``Jurisdiction`` model (which had a 4-value ``JurisdictionLevel`` StrEnum);
    the type vocabulary moved to :class:`JurisdictionType`.

    Bitemporal columns mirror PM's clock:

    - ``valid_from`` / ``valid_until`` — when the jurisdiction is legally active
      in the real world. Null ``valid_until`` = currently active.
    - ``recorded_at`` / ``superseded_at`` — when the row was added / superseded
      in PM. Null ``superseded_at`` = current row.

    ``created_at`` / ``updated_at`` from :class:`TimestampMixin` carry the local
    cache's write times (separate axis from PM's clock).
    """

    __tablename__ = "jurisdictions"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_jurisdictions_slug"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    pm_jurisdiction_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.jurisdiction_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    jurisdiction_type: Mapped[JurisdictionType] = relationship()


class JurisdictionRelationship(Base, TimestampMixin):
    """Bitemporal directed edge between two jurisdictions.

    Carries the Component/Tags graph that replaces the missing hierarchy in the
    old jurisdiction representation. Example: ``usa-wa-ld-21
    IS_FULLY_CONTAINED_BY usa-wa`` (subject = LD-21, object = WA).

    Natural-key UNIQUE on
    ``(subject_jurisdiction_id, object_jurisdiction_id, relationship_type_id, valid_from)``
    so re-issuing the same relationship at a new ``valid_from`` is allowed
    (graph evolves over time).

    **Nullable ``valid_from`` semantics.** PostgreSQL treats NULL as distinct in
    UNIQUE constraints, so the natural-key constraint above does not prevent
    duplicate ``(subject, object, type)`` triples whose ``valid_from`` is NULL.
    The partial index ``uq_jurisdiction_relationships_natural_key_null_from``
    closes that gap by enforcing uniqueness on the triple WHERE ``valid_from IS
    NULL``. Together they ensure exactly one current row per triple regardless
    of whether the real-world start is known. The sidecar follow-up plan will
    define the convention for evolving an edge's validity window (e.g.,
    supersede-and-recreate vs. a sentinel ``valid_from`` value).

    ``rel_metadata`` holds out-of-band fields like weight percentages (for
    overlapping districts), basis (statute reference), legal URL, etc.
    """

    __tablename__ = "jurisdiction_relationships"
    __table_args__ = (
        UniqueConstraint(
            "subject_jurisdiction_id",
            "object_jurisdiction_id",
            "relationship_type_id",
            "valid_from",
            name="uq_jurisdiction_relationships_natural_key",
        ),
        Index(
            "uq_jurisdiction_relationships_natural_key_null_from",
            "subject_jurisdiction_id",
            "object_jurisdiction_id",
            "relationship_type_id",
            unique=True,
            postgresql_where=text("valid_from IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    pm_relationship_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
    subject_jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    object_jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    relationship_type_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.jurisdiction_relationship_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rel_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subject_jurisdiction: Mapped[Jurisdiction] = relationship(
        foreign_keys=[subject_jurisdiction_id]
    )
    object_jurisdiction: Mapped[Jurisdiction] = relationship(foreign_keys=[object_jurisdiction_id])
    relationship_type: Mapped[JurisdictionRelationshipType] = relationship()
