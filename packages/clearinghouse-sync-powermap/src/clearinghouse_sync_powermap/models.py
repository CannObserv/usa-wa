"""Durable sync-state models (Postgres schema ``sync``).

Three tables back the engine's at-least-once delivery, incremental-read state,
and enrich re-propagation. They are deployment-agnostic — the ``entity_type``
discriminator is a free string resolved against the sibling's descriptor
registry, so no concrete entity names are baked in here.

- :class:`OutboxEntry` — the local→PM delivery ledger. One open (``PENDING``)
  row per source row at a time (partial-unique index); the worker re-reads the
  source row at send time, so no payload is stored. Two terminal backlogs persist
  for the operator: ``REJECTED`` (PM refused the payload — fix the data) and
  ``UNAVAILABLE`` (transport-failure cap exhausted, or a permanent auth/scope
  block such as a 403 — re-drivable once PM recovers or the credential is fixed,
  see ``SyncEngine.redrive_unavailable``).
- :class:`SyncState` — per-stream cursor + last-reconcile stamp. One row per
  logical stream (e.g. ``changes_feed``, or per-entity reconcile keys).
- :class:`EnrichFingerprint` — the last enrich payload hash delivered (or settled)
  per source row. The engine re-enriches an already-anchored row when the carry
  payload it holds drifts from this stamp (#34 detection gap), so a payload-shape
  correction reaches the existing cohort without a manual backfill.
- :class:`AnchorReanchor` — an append-only ledger of every in-place ``pm_*_id``
  overwrite (usa-wa#108). When a delivery resolves to a *different* PM id than the
  row's existing anchor (PM dedups assignments on ``(person, role, start_date)``,
  so a start-date correction mints a fresh assignment and orphans the old one), the
  old id is otherwise destroyed by the overwrite and unrecoverable. This row is the
  only durable record of the orphaned PM id — journald is not a queryable retained
  ledger, and the orphan-reconcile cleanup (blocked on power-map#311) reads it.
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
#: Transport-failure cap exhausted (PM unreachable for too long), or a permanent
#: auth/scope block (e.g. a 403 — the credential is mis-scoped) — terminal but
#: re-drivable once PM recovers or the credential is fixed. Distinct from REJECTED
#: so the backlog separates "data bug" from "PM was down / key was wrong".
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
    #: For ENRICH entries: the carry-payload hash computed at enqueue, copied to
    #: :class:`EnrichFingerprint` once the entry reaches a terminal PM verdict so a
    #: re-enrich is not posted again for an unchanged payload (#34). Null for
    #: CREATE/UPDATE (their re-enqueue is governed by the sweep, not a fingerprint).
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


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


class EnrichFingerprint(Base, TimestampMixin):
    """The last enrich carry-payload hash settled for one source row (#34).

    The engine stamps this when an ENRICH entry reaches a terminal PM verdict
    (delivered or rejected). On the next anchored-cohort reconcile the engine
    rebuilds the row's enrich payload, hashes it, and re-enqueues an ENRICH only
    when the hash differs from the stored stamp — so a carry-field shape fix or a
    newly-added carry field propagates to the already-anchored cohort, while an
    unchanged payload never re-posts (no write-back loop). The stamp is local
    (what *we* last sent), never a diff against PM's curated record, so PM
    curating our evidence away does not re-trigger.
    """

    __tablename__ = "powermap_enrich_fingerprint"
    __table_args__ = (
        UniqueConstraint("entity_type", "local_id", name="uq_powermap_enrich_fingerprint_row"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    local_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AnchorReanchor(Base, TimestampMixin):
    """An append-only record of one in-place PM-anchor overwrite (usa-wa#108).

    Written by :meth:`SyncEngine._deliver` whenever a delivery's ``pm_id`` differs
    from the anchor the row already carries. The stamp overwrites ``pm_*_id`` in
    place, so ``old_pm_id`` — the now-orphaned upstream assignment — survives *only*
    here. The paired WARNING log alerts; this table is the queryable, retained
    record the orphan-reconcile cleanup consumes (power-map#311). Never updated or
    deleted: one row per overwrite event, ``created_at`` (TimestampMixin) is the
    observation time.
    """

    __tablename__ = "powermap_anchor_reanchor"
    __table_args__ = (
        Index("ix_powermap_anchor_reanchor_old", "old_pm_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    local_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False)
    #: The source row's natural key when it exposes one (canonical tables carry
    #: ``source_id``) — nullable for triage convenience, not identity.
    source_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    old_pm_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False)
    new_pm_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False)
    disposition: Mapped[str | None] = mapped_column(String(32), nullable=True)
