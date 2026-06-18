"""LegislativeSession — bounded period during which a legislature meets.

Replaces the P0 skeleton's `Bill.biennium` text column. The ``slug`` value
follows the OpenStates convention extended for our jurisdiction encoding:
``<jurisdiction.slug>-<year>[-<session_suffix>]`` (e.g., ``usa-wa-2025``,
``usa-wa-2025-special-1``, ``usa-fed-119``). The slug encodes the
*Jurisdiction.slug* text for readability; the session itself belongs to the
**legislature Organization** (``organization_id``), through which its
jurisdiction is derived (decoupling 2026-06-09).
"""

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, UniqueConstraint
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
        UniqueConstraint("source", "source_id", name="uq_legislative_sessions_natural_key"),
        UniqueConstraint("organization_id", "slug", name="uq_legislative_sessions_slug"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    # A session belongs to the legislature Organization; jurisdiction is derived
    # via that org (decoupling 2026-06-09).
    organization_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    # classification vocab: regular | special | biennium | other
    # (biennium added 2026-06-18 for the WSL P1a synthesis — bills can carry
    # across regular/special sessions within a biennium, so the biennium itself
    # is modeled as a parent session, and its child regular/special sessions
    # point to it via parent_legislative_session_id.)
    # (Dropped extraordinary + sine_die in v1.2; dropped per-session timestamps
    # in v1.3 — end_date now carries sine die semantics for adjourned sessions.)

    # Self-FK enabling parent/child session hierarchy (biennium → regular/special).
    # Nullable because the root session in a hierarchy (or a free-standing one) has none.
    parent_legislative_session_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.legislative_sessions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    """The date the session ended.

    For adjourned sessions, this is the sine die date; for scheduled / active
    sessions, the planned end date. ``adjourned_sine_die_at: timestamptz`` was
    dropped in v1.3 (2026-05-30) — functionally redundant with ``end_date`` for
    the WA use case; precise timestamps can be added back if a query needs them.
    """

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    biennium_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
