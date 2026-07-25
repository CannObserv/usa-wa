"""Operator-attested committee succession events (usa-wa#124) — the judgment layer.

WA re-keys standing committees across eras (new WSL ``Id`` ~each decade). The
*objective* lifecycle facts — each ``Id``'s ``active`` flag + founded/dissolved window —
are auto-derived from the roster archive. What is **not** derivable is which era-``Id``
continued, split from, or merged with which: the re-orgs are irregular and there is no
upstream link. Operators know these (news/journals) and attest them here, feeding the
event producer that emits PM ``succeeded_by`` / ``split_from`` / ``merged_with`` linked
entity events (power-map#321).

A :class:`CommitteeSuccessionEvent` is **link-shaped**, matching PM's linked-entity event
directly: it is recorded on a *subject* org (``subject_source_id``, PM ``org_id``) and
points at a *linked* org (``linked_source_id``, PM ``linked_entity``), typed by ``slug``:

- ``succeeded_by`` — subject = predecessor, linked = successor (the rename-re-key
  continuation; the event lives on the predecessor per the power-map#321 direction).
- ``split_from`` — subject = child, linked = parent (the child came from the parent).
- ``merged_with`` — subject = one predecessor, linked = the survivor/other.

Each event carries exactly one linked entity (PM's constraint), so a multi-way re-org is
attested pairwise. ``effective_year`` is the optional boundary year (``succeeded_by`` is
year-optional in PM).

Backed by the shared ``usa_wa_operator`` provenance ``Source`` (as #107): every write
appends a hashed ``FetchEvent`` + ``RawPayload`` (integrity-sweep covered, #54).
Corrections **append** a new row and stamp the prior one's ``superseded_by_id`` —
provenance is never mutated. A re-link correction (wrong successor) is a supersede whose
producer effect is create-new + retract-old (power-map#322).
"""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

# SCHEMA + _new_ulid are defined locally per the domain-model convention (mirrors
# operator_events.py / bills.py) so the module owns its table placement.
SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


#: Provenance source slug — shared with #107 operator events (the same operator identity).
OPERATOR_SOURCE_SLUG = "usa_wa_operator"

#: Succession relation slugs — mirror PM's org linked-entity event catalog (power-map#321).
SLUG_SUCCEEDED_BY = "succeeded_by"
SLUG_SPLIT_FROM = "split_from"
SLUG_MERGED_WITH = "merged_with"
SLUGS = (SLUG_SUCCEEDED_BY, SLUG_SPLIT_FROM, SLUG_MERGED_WITH)


class CommitteeSuccessionEvent(Base, TimestampMixin):
    """One operator-attested committee-lineage link — the event producer's input unit.

    Natural-keyed on ``(source, source_id)`` where ``source_id`` is deterministic
    (``{slug}:{subject}:{linked}[:{year}]``) so a re-ingest is idempotent and a corrected
    year is a distinct event. ``subject_source_id`` / ``linked_source_id`` are WSL
    committee ``Id``s (``Organization.source_id`` under ``usa_wa_legislature``)."""

    __tablename__ = "committee_succession_events"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_committee_succession_natural_key"),
        CheckConstraint(
            f"slug IN ('{SLUG_SUCCEEDED_BY}', '{SLUG_SPLIT_FROM}', '{SLUG_MERGED_WITH}')",
            name="ck_committee_succession_slug",
        ),
        CheckConstraint(
            "subject_source_id <> linked_source_id",
            name="ck_committee_succession_distinct_ends",
        ),
        Index("ix_committee_succession_subject", "subject_source_id"),
        Index("ix_committee_succession_linked", "linked_source_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default=OPERATOR_SOURCE_SLUG)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)

    #: The org the event is recorded on (PM ``org_id``) — WSL committee ``Id``.
    subject_source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The linked org (PM ``linked_entity``) — WSL committee ``Id``.
    linked_source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Optional boundary year (``succeeded_by`` is year-optional in PM).
    effective_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_url: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    entered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: A correction appends a new row and stamps the prior one here; the producer reads
    #: only rows where this is NULL (the current, non-superseded attestation).
    superseded_by_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.committee_succession_events.id", ondelete="SET NULL"),
        nullable=True,
    )
