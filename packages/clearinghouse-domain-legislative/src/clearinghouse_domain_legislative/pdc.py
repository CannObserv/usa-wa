"""PDC-shaped cluster — lobbying disclosure and campaign finance.

Models are generic enough to fit similar regimes in other states with renames
(Oregon ORESTAR, California Cal-Access, federal LDA/FEC). All tables live in
the ``canonical`` Postgres schema.

Identity links to ``Legislator`` (via ``powermap_person_id`` on both) are
populated in P2 when the power-map adapter lands. MVP queries stay
PDC-internal — no cross-source identity resolution in P1c.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class Filer(Base, TimestampMixin):
    """An entity registered with the disclosure regime — lobbyist, candidate committee, PAC, etc."""

    __tablename__ = "filers"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "source", "source_id", name="uq_filers_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    name: Mapped[str] = mapped_column(String(512), nullable=False)
    filer_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Populated in P2 by the power-map adapter; null until then.
    powermap_org_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)
    powermap_person_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)


class LobbyingActivity(Base, TimestampMixin):
    """A reported lobbying-activity filing — compensation, expenses, employer, period."""

    __tablename__ = "lobbying_activities"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_lobbying_activities_natural_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    filer_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.filers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    employer_filer_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.filers.id", ondelete="SET NULL"),
        nullable=True,
    )
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    compensation: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    expenses: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)


class LobbyingPosition(Base, TimestampMixin):
    """A lobbying activity's stated position on a specific bill.

    ``bill_id`` is resolved during normalization by matching the source's
    chamber+number+biennium reference against the Bills table. When the
    resolver can't find a match, ``bill_id`` stays null and the row is queued
    for later backfill.
    """

    __tablename__ = "lobbying_positions"
    __table_args__ = (
        UniqueConstraint(
            "lobbying_activity_id", "bill_id", name="uq_lobbying_positions_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
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
    position: Mapped[str] = mapped_column(String(16), nullable=False)  # support|oppose|neutral


class Contribution(Base, TimestampMixin):
    """A campaign contribution — money flowing from a contributor to a recipient committee/filer."""

    __tablename__ = "contributions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_contributions_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    recipient_filer_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.filers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    contributor_filer_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.filers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contributor_name_raw: Mapped[str | None] = mapped_column(String(512), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    contributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
