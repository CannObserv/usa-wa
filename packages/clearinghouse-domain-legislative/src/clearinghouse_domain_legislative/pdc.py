"""PDC-shaped cluster — lobbying disclosure + campaign finance.

PDC's notion of "Filer" disappears in v1: filers map onto either Person
(individual lobbyists, individual contributors) or Organization (lobby firms,
PACs, candidate committees). The adapter is responsible for the mapping.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class LobbyingActivity(Base, TimestampMixin):
    """A reported lobbying-activity filing. Subject is a Person or Organization."""

    __tablename__ = "lobbying_activities"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_lobbying_activities_natural_key",
        ),
        CheckConstraint(
            "person_id IS NOT NULL OR organization_id IS NOT NULL",
            name="ck_lobbying_activities_person_or_org",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    person_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.persons.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    employer_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    compensation: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    expenses: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)


class LobbyingPosition(Base, TimestampMixin):
    """A position taken on a bill within a lobbying activity."""

    __tablename__ = "lobbying_positions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_lobbying_positions_natural_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    lobbying_activity_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.lobbying_activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bill_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    bill_reference_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position: Mapped[str] = mapped_column(String(16), nullable=False)
    # position vocab: support | oppose | neutral


class Contribution(Base, TimestampMixin):
    """Campaign contribution. Recipient is an Organization (candidate committee, PAC, etc.)."""

    __tablename__ = "contributions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_contributions_natural_key"
        ),
        CheckConstraint(
            "NOT (contributor_person_id IS NOT NULL AND contributor_organization_id IS NOT NULL)",
            name="ck_contributions_at_most_one_contributor",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    recipient_organization_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    contributor_person_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contributor_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contributor_name_raw: Mapped[str | None] = mapped_column(String(512), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    contributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
