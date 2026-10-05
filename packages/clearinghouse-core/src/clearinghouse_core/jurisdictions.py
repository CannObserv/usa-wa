"""The WA jurisdiction table — the FK target a :class:`~clearinghouse_core.sources.Source`
names (``sources.jurisdiction_id``, published as ``SourceOut.jurisdiction_id``).

Two tables:

- :class:`JurisdictionType` — type lookup (16 rows seeded by migration: ``country``,
  ``state``, ``county``, ``city``, ``legislative_district``, etc.).
- :class:`Jurisdiction` — the entity row, keyed by ``slug``. ``usa_wa_common.jurisdictions``
  declares the WA vocabulary and ``python -m usa_wa_common.seed_jurisdictions`` is the
  table's writer (#310).

It began as a local mirror of Power Map's jurisdiction extension (#22), with a containment
graph (``jurisdiction_relationships`` + its type lookup) and ``pm_*_id`` anchors back to
PM's rows. #412 PR F dropped that half: nothing read the graph, PM pulls published
datasets instead of syncing (#314), and the canonical tables whose ``jurisdiction_id``
FKs it once served are gone. The bitemporal columns stay, as the seeder writes them.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
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


class Jurisdiction(Base, TimestampMixin):
    """A bounded political / administrative area (country, state, county, district, ...).

    Natural key is ``slug`` (e.g., ``'usa-wa'``, ``'usa-wa-ld-21'``,
    ``'usa-wa-county-king'``); see the slug convention in the design spec §1.

    FK target for ``sources.jurisdiction_id``. The type vocabulary lives in
    :class:`JurisdictionType`.

    Bitemporal columns (they once mirrored PM's clock):

    - ``valid_from`` / ``valid_until`` — when the jurisdiction is legally active
      in the real world. Null ``valid_until`` = currently active.
    - ``recorded_at`` / ``superseded_at`` — when the row was added / superseded.
      Null ``superseded_at`` = current row.

    ``created_at`` / ``updated_at`` from :class:`TimestampMixin` carry the local
    write times (a separate axis).
    """

    __tablename__ = "jurisdictions"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_jurisdictions_slug"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
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
