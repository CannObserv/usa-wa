"""SQLAlchemy declarative base and shared mixins.

Provenance models (Jurisdiction, Source, FetchEvent, RawPayload, Citation)
and the ULID column type land here in step 4 of the P0 plan. Domain-layer
entities live in `clearinghouse-domain-legislative` (and future siblings).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base. Every clearinghouse package's models subclass this."""


class TimestampMixin:
    """Mixin adding created_at and updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )
