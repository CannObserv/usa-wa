"""SQLAlchemy declarative base and shared mixins.

Provenance entities live in :mod:`clearinghouse_core.provenance` (imported below
so ``Base.metadata`` discovers them as a side-effect of importing this module).

Domain-layer entities live in ``clearinghouse-domain-legislative`` and future
siblings; their packages own that registration.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base. Every clearinghouse package's models subclass this."""


class CreatedAtMixin:
    """Mixin adding only a ``created_at`` column. Use for append-only tables."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class TimestampMixin(CreatedAtMixin):
    """Mixin adding ``created_at`` and ``updated_at`` columns."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


# Side-effect registration: importing this module also imports the jurisdiction
# and provenance tables so ``Base.metadata.create_all`` / autogen sees them.
# Jurisdictions is imported first because provenance.Source FKs into it.
from clearinghouse_core import jurisdictions as _jurisdictions  # noqa: E402,F401
from clearinghouse_core import provenance as _provenance  # noqa: E402,F401
