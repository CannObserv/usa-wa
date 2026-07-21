"""Operator succession events (#107) — the operator-attestation overlay store.

Tenure spans are biennium-quantized, so a **mid-biennium** succession (death,
resignation, appointment) after genuine service is invisible to every wire signal:
a departed member stays named + committee-listed in the cumulative wire, so their
span stays ghost-open, and an appointee's span starts at the biennium floor rather
than the appointment date. No wire supplies the intra-biennium dates; operators know
these facts (news-first) and interject them here.

An :class:`OperatorEvent` is **event-shaped** — the operator states what happened
(``departed`` / ``seated`` on a date), and the span builders derive the effects
(close the predecessor's open spans at the date, open the successor's seat at the
date). It is backed by a first-class ``usa_wa_operator`` provenance ``Source``: each
CLI write also appends a ``FetchEvent`` + ``RawPayload`` (the serialized event, hashed
— so the integrity sweep covers operator facts), and the spans the overlay touches
carry a ``Citation`` to the attestation. Corrections **append** a new row and stamp
the prior one's ``superseded_by_id`` — provenance is never mutated (#54).

The overlay reads only non-superseded rows on **every** build (the daily refresh
re-drives the builders), so the wire can never win back a corrected span and a
correction is just a new row.
"""

from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin
from clearinghouse_domain_legislative.identity import SCHEMA, _new_ulid

#: The provenance ``Source.source_slug`` every operator attestation is written under.
OPERATOR_SOURCE_SLUG = "usa_wa_operator"

#: Event kinds. ``departed`` is terminal (closes every open span for the member at the
#: effective date); ``seated`` opens the named seat's span at the effective date.
KIND_DEPARTED = "departed"
KIND_SEATED = "seated"
KINDS = (KIND_DEPARTED, KIND_SEATED)

#: Reason sub-tags per kind (evidence classification, not behaviour — both departed
#: reasons close spans identically; both seated reasons open identically).
DEPARTED_REASONS = ("died", "resigned", "expelled")
SEATED_REASONS = ("appointed", "sworn_in")
REASONS = DEPARTED_REASONS + SEATED_REASONS


def event_source_id(
    member_id: str,
    kind: str,
    effective_date: date,
    *,
    seat_kind: str | None = None,
    seat_discriminator: str | None = None,
) -> str:
    """Deterministic natural-key ``source_id`` for an event — so re-ingesting the same
    attestation is an idempotent upsert, while a corrected date is a *distinct* event
    (superseding the prior one). A ``seated`` event keys on its seat; a ``departed``
    event (which closes everything) does not."""
    parts = [member_id, kind]
    if kind == KIND_SEATED:
        parts += [seat_kind or "-", seat_discriminator or "-"]
    parts.append(effective_date.isoformat())
    return ":".join(parts)


class OperatorEvent(Base, TimestampMixin):
    """One operator-attested succession event — the overlay's input unit (#107).

    ``created_at`` (TimestampMixin) is the entry time; ``entered_by`` the operator.
    A ``departed`` row carries no seat (it closes every open span for ``member_id``);
    a ``seated`` row names one seat via ``(seat_kind, seat_discriminator)`` — the same
    ``kind``/discriminator the span builders key on (``chamber-senate`` + LD,
    ``chamber-house`` + ``ld-{n}-position-{p}``, ``committee`` + the WSL id)."""

    __tablename__ = "operator_events"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_operator_events_natural_key"),
        CheckConstraint(
            f"kind IN ('{KIND_DEPARTED}', '{KIND_SEATED}')",
            name="ck_operator_events_kind",
        ),
        CheckConstraint(
            # A seated event names a seat; a departed event must not.
            f"(kind = '{KIND_SEATED}' AND seat_kind IS NOT NULL AND seat_discriminator IS NOT NULL)"
            f" OR (kind = '{KIND_DEPARTED}' AND seat_kind IS NULL AND seat_discriminator IS NULL)",
            name="ck_operator_events_seat_shape",
        ),
        Index("ix_operator_events_member", "member_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default=OPERATOR_SOURCE_SLUG)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)

    #: WSL member Id — the ``Person.source_id`` under ``usa_wa_legislature``.
    member_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    seat_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seat_discriminator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    evidence_url: Mapped[str] = mapped_column(Text, nullable=False)
    entered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: A correction appends a new row and stamps the prior one here; the overlay reads
    #: only rows where this is NULL (the current, non-superseded attestation).
    superseded_by_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.operator_events.id", ondelete="SET NULL"),
        nullable=True,
    )
