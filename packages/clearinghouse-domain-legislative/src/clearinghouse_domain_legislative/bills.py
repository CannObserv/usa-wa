"""Bill cluster — legislation in motion.

All tables live in the ``canonical`` Postgres schema and carry ``jurisdiction_id``
(text slug, e.g. ``"usa-wa"``) plus ``(source, source_id)`` for natural-key
upsert via :class:`clearinghouse_core.runner.AdapterRunner`.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class Bill(Base, TimestampMixin):
    """A piece of legislation in its bill (proposed) form."""

    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "source", "source_id", name="uq_bills_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    biennium: Mapped[str] = mapped_column(String(16), nullable=False)  # e.g., "2025-26"
    chamber: Mapped[str] = mapped_column(String(16), nullable=False)  # house|senate|unicameral
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    bill_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # HB|SB|HJR|...
    title: Mapped[str] = mapped_column(Text, nullable=False)
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    introduced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    primary_source_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fetch_event_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)


class Legislator(Base, TimestampMixin):
    """An elected representative serving in a legislature."""

    __tablename__ = "legislators"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_legislators_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    name_full: Mapped[str] = mapped_column(String(256), nullable=False)
    name_last: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name_first: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chamber: Mapped[str] = mapped_column(String(16), nullable=False)
    district: Mapped[str | None] = mapped_column(String(32), nullable=True)
    party: Mapped[str | None] = mapped_column(String(32), nullable=True)
    biennium: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Populated in P2 by the power-map adapter; null until then.
    powermap_person_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)


class BillSponsorship(Base, TimestampMixin):
    """A legislator's sponsorship of a bill — prime, co, or otherwise."""

    __tablename__ = "bill_sponsorships"
    __table_args__ = (
        UniqueConstraint(
            "bill_id", "legislator_id", "role", name="uq_bill_sponsorships_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legislator_id: Mapped[_ULID] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.legislators.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # prime|co
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BillAction(Base, TimestampMixin):
    """Append-only lifecycle log entry for a bill."""

    __tablename__ = "bill_actions"
    __table_args__ = (
        UniqueConstraint(
            "bill_id", "source", "source_action_id", name="uq_bill_actions_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_action_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    chamber: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class BillVersion(Base, TimestampMixin):
    """Version metadata for a bill — substitute, engrossed, etc.

    Full text deferred to P3; MVP stores only metadata.
    """

    __tablename__ = "bill_versions"
    __table_args__ = (
        UniqueConstraint("bill_id", "source", "source_id", name="uq_bill_versions_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(default=False)


class Committee(Base, TimestampMixin):
    """A legislative committee. Skeletal in MVP; expanded in P3."""

    __tablename__ = "committees"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_committees_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    biennium: Mapped[str | None] = mapped_column(String(16), nullable=True)
    chamber: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)


class Hearing(Base, TimestampMixin):
    """A scheduled or past committee hearing. Skeletal in MVP; expanded in P3."""

    __tablename__ = "hearings"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "source", "source_id", name="uq_hearings_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    committee_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.committees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
