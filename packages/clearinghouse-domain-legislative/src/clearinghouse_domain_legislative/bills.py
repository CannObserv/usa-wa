"""Bill cluster — bills, sponsorships, actions, versions, amendments, subjects,
relationships, and events.

All tables live in the ``canonical`` Postgres schema.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class Bill(Base, TimestampMixin):
    """A piece of legislation in proposed form."""

    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "source", "source_id", name="uq_bills_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    legislative_session_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.legislative_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    originating_chamber: Mapped[str] = mapped_column(String(16), nullable=False)
    # originating_chamber vocab: house | senate | unicameral

    current_chamber: Mapped[str | None] = mapped_column(String(16), nullable=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    bill_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # bill_type vocab: HB | SB | HJR | SJR | HCR | SCR | HJM | SJM | HR | S | etc.

    title: Mapped[str] = mapped_column(Text, nullable=False)
    # short_description and current_text moved to BillVersion in v1.2 (2026-05-28):
    # per-version summary and text are resolution-preserving (an OCD BillAbstract
    # tracks with a specific version of the bill, not the bill as a whole).

    current_version_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_versions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    """FK to the bill's current/latest BillVersion. Convenience denormalization.

    Adapter maintains: on adoption of a new substitute or engrossed version, this
    FK gets updated. Tests + queries that just need "the current text" go through
    this FK rather than scanning ``bill_versions`` for is_current=true.
    """

    current_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_status_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # current_status_class vocab: introduced | in_committee | passed_first_chamber
    #                           | passed_second_chamber | vetoed | signed
    #                           | enacted | failed | withdrawn

    current_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    introduced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enacted_as: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BillSponsorship(Base, TimestampMixin):
    """Polymorphic sponsorship — Person or Organization. WA only emits Person."""

    __tablename__ = "bill_sponsorships"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_sponsorships_natural_key",
        ),
        CheckConstraint(
            "(person_id IS NOT NULL AND organization_id IS NULL)"
            " OR (person_id IS NULL AND organization_id IS NOT NULL)"
            " OR (person_id IS NULL AND organization_id IS NULL AND sponsor_name_raw IS NOT NULL)",
            name="ck_bill_sponsorships_polymorphic",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[_ULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.persons.id", ondelete="RESTRICT"), nullable=True
    )
    organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"), nullable=True
    )
    sponsor_name_raw: Mapped[str | None] = mapped_column(String(256), nullable=True)

    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # role vocab: primary | co | joint | generic

    sponsor_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillAction(Base, TimestampMixin):
    """Append-only lifecycle log entry for a bill."""

    __tablename__ = "bill_actions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_actions_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    chamber: Mapped[str | None] = mapped_column(String(16), nullable=True)
    acting_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # OCD-aligned: introduction | reading-1 | reading-2 | reading-3 | passage
    #            | amendment-passage | committee-passage | executive-signature | etc.

    description: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_major: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BillActionClassification(Base, TimestampMixin):
    """1:N OCD-style multi-classification for a BillAction. New in v1."""

    __tablename__ = "bill_action_classifications"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_action_classifications_natural_key",
        ),
        UniqueConstraint(
            "bill_action_id",
            "classification",
            name="uq_bill_action_classifications_action_class",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_action_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_actions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    classification: Mapped[str] = mapped_column(String(64), nullable=False)


class BillVersion(Base, TimestampMixin):
    """A version of the bill — introduced / substitute / engrossed / enrolled / etc.

    v1.2 (2026-05-28) added ``text``, ``short_description``, and ``amendment_id``:
    full text + per-version summary + provenance to the amendment that produced
    this version (when applicable). See ``bill_version_links`` for alternative
    representations of the same version (HTML, PDF, etc.).
    """

    __tablename__ = "bill_versions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_versions_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # version_type vocab: introduced | substitute | engrossed | first_engrossed
    #                   | enrolled | act | conference_substitute | etc.

    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Per-version summary / abstract (e.g., OCD ``BillAbstract`` for this version)."""

    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Canonical plain-text representation of the bill at this version.

    See ``BillVersionLink`` for alternative representations (HTML, PDF, image-PDF
    with OCR, processed git-friendly text, etc.). Storage/canonicalization rules
    are an open design question — see open questions in the hybrid-IA spec.
    """

    amendment_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.amendments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """When this version was created by adopting an amendment, points to it.

    Null for the introduced version and for engrossed-by-action versions (where
    no amendment produced the version directly). Populated for substitute and
    striking-amendment versions.
    """

    version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BillTitle(Base, TimestampMixin):
    """1:N title rows per Bill. New in v1.1 (2026-05-28).

    Bills carry multiple titles: canonical / short / popular / official / display /
    alternative / long, sometimes chamber-specific, sometimes lifecycle-stage-specific
    ("introduced", "engrossed"), occasionally multilingual. In WA, an amendment can
    change a bill's title — and the procedural significance is load-bearing (an
    amendment whose content falls outside the bill's current title is procedurally
    challengeable for exceeding scope), so we track ``amendment_id`` here.

    ``Bill.title`` is denormalized — it mirrors the row where
    ``title_type='canonical' AND is_current=true``.
    """

    __tablename__ = "bill_titles"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_titles_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title_text: Mapped[str] = mapped_column(Text, nullable=False)
    title_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # title_type vocab: canonical | short | popular | official | display
    #                 | alternative | long | summary_title

    chamber: Mapped[str | None] = mapped_column(String(16), nullable=True)
    as_of_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # as_of_action vocab (free-text): introduced | engrossed | enrolled
    #                               | committee_substitute | etc.

    language_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    amendment_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.amendments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Amendment(Base, TimestampMixin):
    """Proposed change to a bill. Voted on, so the Vote cluster references it."""

    __tablename__ = "amendments"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_amendments_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    amendment_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="traditional")
    """v1.2 (2026-05-28). One of: ``traditional`` (edits) / ``striking`` ("strike
    everything after the enacting clause" — effectively a new full version) /
    ``substitute`` (overt full replacement of the bill, may include new title).

    When adopted, striking and substitute amendments produce a new ``BillVersion``
    with ``BillVersion.amendment_id`` pointing back here. Traditional amendments
    produce no BillVersion row — they're consumed into the next engrossed version
    via the source's normal engrossment process.
    """

    amendment_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Edit text (traditional) or full replacement text (striking / substitute)."""

    sponsor_person_id: Mapped[_ULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.persons.id", ondelete="SET NULL"), nullable=True
    )
    sponsor_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # status vocab: offered | adopted | rejected | withdrawn | pending | tabled

    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillSubject(Base, TimestampMixin):
    """Policy area / topic tag for a bill. New in v1."""

    __tablename__ = "bill_subjects"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_subjects_natural_key"
        ),
        UniqueConstraint("bill_id", "subject", name="uq_bill_subjects_bill_subject"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BillRelationship(Base, TimestampMixin):
    """Bill-to-bill relationship — companion, replaces, etc. New in v1."""

    __tablename__ = "bill_relationships"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_relationships_natural_key",
        ),
        UniqueConstraint(
            "from_bill_id",
            "to_bill_id",
            "relationship_type",
            name="uq_bill_relationships_pair_type",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    from_bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # relationship_type vocab: companion | replaces | replaced_by | related_to
    #                        | prior_session_carryover | derived_from | other

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BillEvent(Base, TimestampMixin):
    """Scheduled event on a bill — public hearings, work sessions, calendar slots. New in v1."""

    __tablename__ = "bill_events"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_events_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # event_type vocab: public_hearing | executive_session | work_session
    #                 | committee_meeting | floor_calendar | other

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    # status vocab: scheduled | completed | cancelled | continued | rescheduled

    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class BillVersionLink(Base, TimestampMixin):
    """1:N alternative representations of a single BillVersion. New in v1.2 (2026-05-28).

    A single bill version may exist in multiple forms — original PDF, scraped HTML,
    OCR'd text from an image PDF, processed git-friendly representation, etc. This
    table holds them all; ``BillVersion.text`` is the canonical plain-text view that
    the query layer reads by default.
    """

    __tablename__ = "bill_version_links"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_version_links_natural_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_version_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    # kind vocab: text | html | pdf | xml | image_pdf | processed_text
    #           | redline | other

    title: Mapped[str | None] = mapped_column(String(256), nullable=True)


class BillStatutoryCitation(Base, TimestampMixin):
    """A statutory citation extracted from a bill version's text. New in v1.2.

    OCD's ``Bill.citations`` carries the same concept. Useful for queries like
    "which bills reference RCW 46.16.005?" without scanning bill text at query
    time. Extraction happens during normalization (P1b enrichment).
    """

    __tablename__ = "bill_statutory_citations"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_statutory_citations_natural_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_version_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    statute_section_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.statute_sections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    raw_text: Mapped[str] = mapped_column(String(256), nullable=False)
    """The citation as it appeared in the bill text — e.g., ``"RCW 46.16.005"``."""

    text_offset_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_offset_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
