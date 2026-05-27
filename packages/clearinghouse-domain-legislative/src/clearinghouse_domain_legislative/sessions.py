"""LegislativeSession — bounded period during which a legislature meets.

Replaces the P0 skeleton's `Bill.biennium` text column. Slug follows the
OpenStates convention extended for our jurisdiction encoding:
`<jurisdiction_id>-<year>[-<session_suffix>]` (e.g., `usa-wa-2025`,
`usa-wa-2025-special-1`, `usa-fed-119`).
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class LegislativeSession(Base, TimestampMixin):
    """A regular or special session of a legislature."""

    __tablename__ = "legislative_sessions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_legislative_sessions_natural_key",
        ),
        UniqueConstraint("jurisdiction_id", "slug", name="uq_legislative_sessions_slug"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    # classification vocab (v1.2, 2026-05-28): regular | special | other
    # Dropped `extraordinary` (no semantic difference from `special`/`other`)
    # and `sine_die` (which is an adjournment state, not a session type — see
    # adjourned_sine_die_at below).

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    adjourned_sine_die_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the session adjourned sine die — literally without plans to return.

    In WA practice ``sine die`` is the act of ending a session; populated when known,
    null while the session is active or if adjournment status is unknown.
    """

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    biennium_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
