"""Vote cluster — VoteEvent, VoteCount, PersonVote.

Flexible enough for committee + floor votes on bills, amendments, and motions.
PersonVote is materialized in P1a (per resolved P0.5 OQ3).
"""

from datetime import datetime

from sqlalchemy import (
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


class VoteEvent(Base, TimestampMixin):
    """One vote happening — floor or committee, on a bill / amendment / motion."""

    __tablename__ = "vote_events"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_vote_events_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # subject_type vocab: bill | amendment | motion

    subject_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False)
    # Polymorphic — no DB FK on subject_id (mirrors the Citation pattern).

    bill_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    amendment_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.amendments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    motion_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    context_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # context_type vocab: floor | committee

    context_organization_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    chamber: Mapped[str | None] = mapped_column(String(16), nullable=True)

    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # category vocab: passage | cloture | recommit | tabling | motion_to_proceed
    #               | nomination | treaty | conviction | procedural | other

    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    # outcome vocab: passed | failed | tabled | withdrawn | inconclusive | other


class VoteCount(Base, TimestampMixin):
    """Aggregate counts per VoteEvent — one row per outcome category."""

    __tablename__ = "vote_counts"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_vote_counts_natural_key"
        ),
        UniqueConstraint("vote_event_id", "count_type", name="uq_vote_counts_event_type"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    vote_event_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.vote_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    count_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # count_type vocab: yea | nay | excused | absent | present_not_voting | paired | other

    value: Mapped[int] = mapped_column(Integer, nullable=False)


class PersonVote(Base, TimestampMixin):
    """Per-legislator vote within a VoteEvent. Materialized in P1a per OQ3."""

    __tablename__ = "person_votes"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_person_votes_natural_key"
        ),
        CheckConstraint(
            "person_id IS NOT NULL OR voter_name_raw IS NOT NULL",
            name="ck_person_votes_person_or_name",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    vote_event_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.vote_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.persons.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    voter_name_raw: Mapped[str | None] = mapped_column(String(256), nullable=True)
    vote: Mapped[str] = mapped_column(String(16), nullable=False)
    # vote vocab: yea | nay | abstain | excused | absent | present_not_voting | paired
