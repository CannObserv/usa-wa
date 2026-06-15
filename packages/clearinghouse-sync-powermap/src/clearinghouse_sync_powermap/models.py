"""Durable sync-state models (Postgres schema ``sync``).

Two tables back the engine's at-least-once delivery + incremental-read state.
They are deployment-agnostic — the ``entity_type`` discriminator is a free
string resolved against the sibling's descriptor registry, so no concrete
entity names are baked in here.

- :class:`OutboxEntry` — the local→PM delivery ledger. One open (``PENDING``)
  row per source row at a time (partial-unique index); the worker re-reads the
  source row at send time, so no payload is stored. ``REJECTED`` rows persist as
  the operator backlog.
- :class:`SyncState` — per-stream cursor + last-reconcile stamp. One row per
  logical stream (e.g. ``changes_feed``, or per-entity reconcile keys).
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "sync"

#: Outbox operation kinds.
OP_CREATE = "CREATE"
OP_UPDATE = "UPDATE"
#: Enrich-on-match (power-map#198): attach our identifiers/names to an already-
#: matched, identifier-less PM entity, keyed on its PM-native ``pm_*_id`` type.
OP_ENRICH = "ENRICH"
_OPS = (OP_CREATE, OP_UPDATE, OP_ENRICH)

#: Outbox delivery states.
STATUS_PENDING = "PENDING"
STATUS_DELIVERED = "DELIVERED"
#: PM explicitly rejected the observation (bad/duplicate payload) — terminal; a
#: blind retry just repeats the rejection. Operator must fix the source data.
STATUS_REJECTED = "REJECTED"
#: Transport-failure cap exhausted (PM unreachable for too long) — terminal but
#: re-drivable: the same payload will likely succeed once PM recovers. Distinct
#: from REJECTED so the backlog separates "data bug" from "PM was down".
STATUS_UNAVAILABLE = "UNAVAILABLE"
_STATUSES = (STATUS_PENDING, STATUS_DELIVERED, STATUS_REJECTED, STATUS_UNAVAILABLE)

#: PM observation dispositions — values match PM's deployed ``Disposition`` StrEnum
#: (``src/core/observation.py``): lowercase, hyphenated. Verified 2026-06-06.
#: ``queued-for-review`` was discarded in PM design review.
DISPOSITION_AUTO_ATTACHED = "auto-attached"
DISPOSITION_NEW = "new"
DISPOSITION_REJECTED = "rejected"


def _new_ulid() -> _ULID:
    return _ULID()


class OutboxEntry(Base, TimestampMixin):
    """A pending/settled local→PM write for one source row."""

    __tablename__ = "powermap_outbox"
    __table_args__ = (
        CheckConstraint(f"op IN {_OPS}", name="ck_powermap_outbox_op"),
        CheckConstraint(f"status IN {_STATUSES}", name="ck_powermap_outbox_status"),
        # At most one open delivery per source row — re-enqueue is a no-op.
        Index(
            "uq_powermap_outbox_open",
            "entity_type",
            "local_id",
            unique=True,
            postgresql_where=text(f"status = '{STATUS_PENDING}'"),
        ),
        Index("ix_powermap_outbox_due", "status", "next_attempt_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    local_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_disposition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncState(Base, TimestampMixin):
    """Cursor + last-reconcile stamp for one logical read stream."""

    __tablename__ = "powermap_sync_state"
    __table_args__ = (
        UniqueConstraint("stream", name="uq_powermap_sync_state_stream"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    stream: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_reconcile_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
