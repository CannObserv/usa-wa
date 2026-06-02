"""Bill cluster — bills, sponsorships, actions, versions, amendments, subjects,
relationships, and events.

All tables live in the ``canonical`` Postgres schema.
"""

from datetime import datetime
from typing import Any

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
from sqlalchemy.dialects.postgresql import JSONB
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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    legislative_session_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.legislative_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    originating_chamber_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    """Chamber Org (WA House / WA Senate / etc.) where the bill was first introduced.

    v1.3 (2026-05-30): replaced the ``originating_chamber: text(16)`` enum column.
    Chambers are first-class Organizations (org_type='chamber'), so chamber refs
    are FKs throughout. Use ``organizations.short_name`` for the slug equivalent
    (``"house"`` / ``"senate"`` / ``"unicameral"``) when a query needs the legacy
    enum shape.
    """

    current_chamber_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    """Chamber Org currently considering the bill. Null when in conference or
    fully passed both chambers."""
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    bill_type_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_types.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    """v1.3 (2026-05-30): FK to canonical.bill_types lookup. Replaces the inline
    ``Bill.bill_type: text(32)`` and ``Bill.classification: text(32)`` columns.
    The lookup row carries the code (HB / SB / HJM etc.), the display name, AND
    the OCD-aligned semantic classification (bill / resolution / memorial etc.),
    so the two fields stay in lockstep without per-row drift.
    """

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


class BillType(Base, TimestampMixin):
    """Lookup table for bill-type codes. New in v1.3 (2026-05-30).

    Replaces the inline ``Bill.bill_type: text(32)`` + ``Bill.classification:
    text(32)`` columns. One row per (jurisdiction, code) pair holds:

    - ``code`` — the source's prefix code (WA: ``HB`` / ``SB`` / ``HJM`` etc.;
      federal: ``hr`` / ``hjres`` / ``sconres`` etc.).
    - ``display_name`` — human label (``"House Bill"``, ``"Joint Memorial"``).
    - ``classification`` — OCD-aligned semantic class (``bill`` / ``resolution``
      / ``joint resolution`` / ``memorial`` / etc.). Was previously
      ``Bill.classification`` (added then removed in v1.3 — the value belongs
      with the bill_type, not the per-bill row).

    Bills FK in here via ``Bill.bill_type_id``. Adapter seeds the lookup at
    init or upserts on first encounter.
    """

    __tablename__ = "bill_types"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "code", name="uq_bill_types_jurisdiction_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # classification vocab (OCD-aligned): bill | resolution | joint resolution
    #                                   | concurrent resolution | simple resolution
    #                                   | constitutional amendment | memorial
    #                                   | proclamation | initiative | study request
    #                                   | other.


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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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
    sponsored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # v1.3 (2026-05-31): when this person/org signed on as sponsor. Federal
    # cosponsors[].sponsored_at is the direct populator. WA uses this for
    # tracking when cosponsors join after introduction. Recovers
    # original-cosponsor inference by comparing to Bill.introduced_at.
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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # chamber column dropped in v1.3 (2026-05-30): derivable from
    # acting_organization_id (the chamber Org for chamber-level actions, or
    # ancestor of acting_organization_id when a committee acts). Executive
    # actions point acting_organization_id at the executive Org (e.g., "WA
    # Office of the Governor").
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

    supplement_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_supplements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # v1.3 (2026-05-30): when this action published a supplement document
    # (Bill Analysis / Bill Report / Fiscal Note / Bill Summary), the FK points
    # to the authoritative bill_supplements row. Pair with
    # primary_classification='supplement_published'.


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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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

    v1.3 (2026-05-30): the ``amendment_id`` back-link to amendments was dropped.
    Amendments now link forward to the BillVersion they target via
    ``Amendment.bill_version_id`` — substitute / striking amendments point at
    the newly proposed BillVersion (which exists as a row from the moment the
    substitute is offered), so the back-link became redundant.

    v1.2 (2026-05-28) added ``text`` + ``short_description`` (per-version
    summary). See ``bill_version_links`` for alternative representations
    (HTML, PDF, etc.).
    """

    __tablename__ = "bill_versions"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_versions_natural_key"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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

    chamber_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    """When the title is chamber-specific (federal: House vs Senate short
    titles), FK to the chamber Org. v1.3 (2026-05-30): replaced ``chamber: text(16)``.
    """

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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_version_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    """v1.3 (2026-05-30): the Amendment's primary linkage is to the bill *version*
    being amended, not the overall bill — every amendment is against a specific
    version.

    For traditional amendments: points to the current bill version being amended
    (e.g., the introduced version).

    For substitute / striking amendments: points to the newly proposed BillVersion
    (which exists as a row from the moment the substitute is offered, before
    adoption). The amendment text IS the BillVersion's text — no duplication.

    The previous ``Amendment.bill_id`` direct FK and ``Amendment.amendment_text``
    column were both dropped in v1.3. ``BillVersion.amendment_id`` back-link was
    also dropped — relinking is forward-only now, BillVersion → Bill (via bill_id)
    and Amendment → BillVersion (via bill_version_id).
    """

    label: Mapped[str] = mapped_column(String(64), nullable=False)
    amendment_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="traditional")
    """v1.2 (2026-05-28). One of: ``traditional`` (edits) / ``striking`` ("strike
    everything after the enacting clause" — effectively a new full version) /
    ``substitute`` (overt full replacement of the bill, may include new title).

    Striking and substitute amendments are inherently *also* bill texts — they
    propose a wholesale replacement. The schema models this directly via
    ``bill_version_id`` pointing to the proposed BillVersion; the amendment's
    text IS that BillVersion's text. Traditional amendments edit the current
    version's text in-place; they don't produce a separate BillVersion.
    """

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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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


class BillRelationshipType(Base, TimestampMixin):
    """Lookup table for bill-to-bill relationship kinds. New in v1.3 (2026-05-30).

    Replaces the inline ``BillRelationship.relationship_type: text(32)`` enum.
    The lookup pattern encodes the ``symmetric`` property (whether the relation
    is symmetric, like ``companion``, vs. asymmetric, like ``replaces`` /
    ``replaced_by``) once per type rather than relying on per-relationship
    documentation.
    """

    __tablename__ = "bill_relationship_types"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "code",
            name="uq_bill_relationship_types_jurisdiction_code",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    # Common codes: companion | replaces | replaced_by | related_to
    #             | prior_session_carryover | derived_from | other

    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    symmetric: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """True for relations like ``companion`` where A↔B has the same semantic;
    false for directed relations like ``replaces`` where A→B and B→A differ.
    Query layer can use this to materialize the reverse view of symmetric rows
    without storing both directions."""

    description: Mapped[str | None] = mapped_column(Text, nullable=True)


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
            "relationship_type_id",
            name="uq_bill_relationships_pair_type",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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
    relationship_type_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_relationship_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # v1.3 (2026-05-30): FK to bill_relationship_types lookup (replaces the
    # inline text enum). The lookup carries the symmetric flag.

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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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


class BillSupplement(Base, TimestampMixin):
    """Per-bill-version supplementary document. New in v1.3.

    WA legislative practice attaches four kinds of supplementary documents to
    bill versions, each authored by non-partisan staff or regulatory agencies:

    - ``bill_analysis`` — pre-hearing summary + history composed by committee
      staff before a particular version is heard or acted on. Includes hearing
      details and may mention positions of individuals / orgs (cross-linked to
      the PDC lobbying cluster via :class:`clearinghouse_core.provenance.Citation`).
    - ``bill_report`` — post-hearing version of the same shape, temporally
      distinguished from ``bill_analysis``. Composed after the version has been
      heard / acted on.
    - ``fiscal_note`` — fiscal-impact report by regulatory agencies (single
      agency) or as an aggregate report across agencies. ``status ∈
      {partial, final}`` for fiscal notes; multiple revisions per status are
      distinguished by ``revision_sequence`` and ``published_at``.
    - ``bill_summary`` — brief description by non-partisan chamber staff
      (or by an agency / org), usually published at chamber hand-off.

    All four are per-:class:`BillVersion` (always reference a specific version,
    never just the overall bill). Almost always PDFs in WA; the ``url`` column
    points to the source PDF, ``text`` holds extracted plain text (P1b
    enrichment), ``structured_data`` holds extracted Q&A responses and
    tabular fiscal-impact data (also P1b).

    Sidecar archival to the Archiver sibling service mirrors the identity
    producer/archival pattern: adapters write rows + download PDFs locally; the
    sidecar pushes PDFs to Archiver and populates ``archival_url`` +
    ``archived_at``. Lifecycle integration: each supplement publication
    generates a paired :class:`BillAction` row with
    ``primary_classification='supplement_published'`` whose
    ``BillAction.supplement_id`` FK points back here.
    """

    __tablename__ = "bill_supplements"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_supplements_natural_key",
        ),
        UniqueConstraint(
            "bill_version_id",
            "supplement_kind",
            "status",
            "revision_sequence",
            name="uq_bill_supplements_content_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    bill_version_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bill_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    """Denormalized FK for cheap "all supplements for bill X" queries without a join."""

    supplement_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # vocab: bill_analysis | bill_report | fiscal_note | bill_summary | other

    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """For ``fiscal_note`` only: ``partial`` (some agencies have responded) /
    ``final`` (all agencies have responded). Null for other kinds."""

    revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    """Counter for multiple PDFs under the same (bill_version, kind, status)."""

    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    author_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """The committee, agency, or chamber that authored the supplement."""

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Extracted plain text — P1b enrichment populates."""

    structured_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """Extracted Q&A responses and tabular fiscal-impact data — P1b enrichment populates."""

    archival_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    """Archiver sibling URL once sidecar push completes."""

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
