"""Bill cluster — bills, sponsorships, actions, versions, amendments, subjects,
relationships, and events.

All tables live in the ``canonical`` Postgres schema.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class Bill(Base, TimestampMixin):
    """A piece of legislation in proposed form."""

    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "source", "source_id", name="uq_bills_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    legislative_session_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.legislative_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    originating_chamber: Mapped[str] = mapped_column(String(16), nullable=False)
    # originating_chamber vocab: house | senate | unicameral

    current_chamber: Mapped[str | None] = mapped_column(String(16), nullable=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    bill_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # bill_type vocab: HB | SB | HJR | SJR | HCR | SCR | HJM | SJM | HR | S | etc.

    title: Mapped[str] = mapped_column(Text, nullable=False)
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    current_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_status_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # current_status_class vocab: introduced | in_committee | passed_first_chamber
    #                           | passed_second_chamber | vetoed | signed
    #                           | enacted | failed | withdrawn

    current_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    introduced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enacted_as: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class BillSponsorship(Base, TimestampMixin):
    """Polymorphic sponsorship — Person or Organization. WA only emits Person."""

    __tablename__ = "bill_sponsorships"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_sponsorships_natural_key",
        ),
        CheckConstraint(
            "(person_id IS NOT NULL AND organization_id IS NULL)"
            " OR (person_id IS NULL AND organization_id IS NOT NULL)"
            " OR (person_id IS NULL AND organization_id IS NULL AND sponsor_name_raw IS NOT NULL)",
            name="ck_bill_sponsorships_polymorphic",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[_ULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.persons.id", ondelete="RESTRICT"), nullable=True
    )
    organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"), nullable=True
    )
    sponsor_name_raw: Mapped[str | None] = mapped_column(String(256), nullable=True)

    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # role vocab: primary | co | joint | generic

    sponsor_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillAction(Base, TimestampMixin):
    """Append-only lifecycle log entry for a bill."""

    __tablename__ = "bill_actions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_actions_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    chamber: Mapped[str | None] = mapped_column(String(16), nullable=True)
    acting_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # OCD-aligned: introduction | reading-1 | reading-2 | reading-3 | passage
    #            | amendment-passage | committee-passage | executive-signature | etc.

    description: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_major: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BillActionClassification(Base, TimestampMixin):
    """1:N OCD-style multi-classification for a BillAction. New in v1."""

    __tablename__ = "bill_action_classifications"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_action_classifications_natural_key",
        ),
        UniqueConstraint(
            "bill_action_id",
            "classification",
            name="uq_bill_action_classifications_action_class",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_action_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_actions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    classification: Mapped[str] = mapped_column(String(64), nullable=False)


class BillVersion(Base, TimestampMixin):
    """Version metadata only in MVP. Full text deferred to P3."""

    __tablename__ = "bill_versions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_versions_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # version_type vocab: introduced | substitute | engrossed | first_engrossed
    #                   | enrolled | act | conference_substitute | etc.

    version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Amendment(Base, TimestampMixin):
    """Proposed change to a bill. Voted on, so the Vote cluster references it."""

    __tablename__ = "amendments"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_amendments_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    amendment_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sponsor_person_id: Mapped[_ULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.persons.id", ondelete="SET NULL"), nullable=True
    )
    sponsor_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # status vocab: offered | adopted | rejected | withdrawn | pending | tabled

    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillSubject(Base, TimestampMixin):
    """Policy area / topic tag for a bill. New in v1."""

    __tablename__ = "bill_subjects"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_subjects_natural_key"
        ),
        UniqueConstraint("bill_id", "subject", name="uq_bill_subjects_bill_subject"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BillRelationship(Base, TimestampMixin):
    """Bill-to-bill relationship — companion, replaces, etc. New in v1."""

    __tablename__ = "bill_relationships"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_relationships_natural_key",
        ),
        UniqueConstraint(
            "from_bill_id",
            "to_bill_id",
            "relationship_type",
            name="uq_bill_relationships_pair_type",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    from_bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # relationship_type vocab: companion | replaces | replaced_by | related_to
    #                        | prior_session_carryover | derived_from | other

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BillEvent(Base, TimestampMixin):
    """Scheduled event on a bill — public hearings, work sessions, calendar slots. New in v1."""

    __tablename__ = "bill_events"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_events_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # event_type vocab: public_hearing | executive_session | work_session
    #                 | committee_meeting | floor_calendar | other

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    # status vocab: scheduled | completed | cancelled | continued | rescheduled

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
