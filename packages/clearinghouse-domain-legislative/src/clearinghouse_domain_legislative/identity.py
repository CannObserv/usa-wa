"""Identity cluster — power-map terminology.

Person + Organization + Role + Assignment, plus the N:1 external-ID child
tables PersonIdentifier and OrganizationIdentifier.

usa-wa is a producer of identity data; the long-term archival store is
power-map. Local copies in this schema serve query latency; the canonical
truth lives upstream (see project memory: project_identity_producer_archival).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ColumnElement,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class LifecycleMixin:
    """Two PM-parity lifecycle tombstones shared by the four identity models — the
    local cache mirrors PM (rows are never hard-deleted), so both are soft markers
    kept as provenance (usa-wa#31/#36/#38/#40/#41/#42).

    PM tracks these as **orthogonal axes with opposite re-fetch semantics**; usa-wa
    mirrors PM's nomenclature 1:1 with two columns rather than overloading one:

    - **``archived_at``** — mirrors PM's ``archived_at`` (its reversible "inactive"
      soft-delete gate). The PM id stays **live**, so the sync engine keeps an
      archived row in its sweep/reconcile cohort and re-fetches it — that is how a
      dropped un-archive event self-heals (#42). Set/cleared by the descriptors'
      ``EntityDescriptor.mirror_archival`` from PM's own clock (#40 orgs;
      #41 person/role/assignment).
    - **``deleted_at``** — terminal tombstone for a genuine delete / merge-orphan
      with no surviving winner. The PM id is **gone** (re-fetch 404s), so the engine
      excludes a deleted row from the sweep/reconcile — it must never be re-created
      or re-fetched. Stamped only by the engine's dead-anchor heal path (#31/#36/#38).

    A row is **live** iff *both* are NULL. Live reads route through
    :func:`clearinghouse_domain_legislative.queries.live_only` (filters both); the
    PM-sync sweep/reconcile filters on ``deleted_at IS NULL`` only — via the portable
    ``EntityDescriptor.deleted_column_expr`` (the engine can't import this domain
    layer) — so an archived row (live anchor) stays in the cohort (#42).
    """

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @classmethod
    def is_live(cls) -> ColumnElement[bool]:
        """Predicate for live rows: ``archived_at IS NULL AND deleted_at IS NULL``.

        The live-read filter (both axes hide a row from the read fan-out)."""
        return cls.archived_at.is_(None) & cls.deleted_at.is_(None)


class Person(Base, TimestampMixin, LifecycleMixin):
    """A human. Replaces Legislator from the P0 skeleton."""

    __tablename__ = "persons"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_persons_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    # People never belong to a jurisdiction (decoupling 2026-06-09).
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    name_full: Mapped[str] = mapped_column(String(256), nullable=False)
    name_first: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name_last: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name_middle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name_suffix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name_used: Mapped[str | None] = mapped_column(String(256), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # birth_year removed v1.1 (2026-05-28): birth/death/lifecycle events defer to
    # Power Map's planned lifecycle_events schema (CannObserv/power-map#165).

    # Cross-cohort denormalization; the full N-scheme graph lives in PersonIdentifier.
    # PM anchor (sidecar sync). Standardized to ``pm_<entity>_id`` (was
    # ``powermap_person_id`` pre-sidecar) so the sync engine keys uniformly.
    pm_person_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
    # archived_at + deleted_at tombstones provided by LifecycleMixin (#31/#38/#42).


class Organization(Base, TimestampMixin, LifecycleMixin):
    """Any non-person legal/political entity. Discriminated by org_type."""

    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_organizations_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    # The binding root: only *public* orgs belong to a jurisdiction; private orgs
    # are global (nullable). Decoupling 2026-06-09.
    jurisdiction_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey("clearinghouse_core.jurisdictions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    name: Mapped[str] = mapped_column(String(512), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    org_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # org_type vocab: chamber | party | committee | subcommittee | caucus
    #               | candidate_committee | lobbying_firm | pac
    #               | legislature | government_agency | other
    # (legislature added 2026-06-18 for the WSL P1a synthesis — the legislative
    # branch itself, distinct from executive `government_agency` regulators.)

    # Canonical acronym (e.g. "APP" for House Appropriations). PM Org observations
    # support a list of acronyms; this column tracks the single canonical/source-of-truth
    # value. Added 2026-06-18 for WSL committees. The org descriptor's read mirror adopts
    # PM's ``is_canonical`` acronym into this scalar (#65), symmetric with ``name`` — so it
    # is the PM-resolved current value, while ``organization_acronyms`` holds every variant.
    acronym: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Primary phone contact (E.164-ish source string; not normalized at write time).
    # PM Org observations accept this as a `phone` contact_method. Added 2026-06-18.
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    parent_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # PM anchor (sidecar sync). Was ``powermap_organization_id`` pre-sidecar.
    pm_organization_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
    # archived_at + deleted_at tombstones provided by LifecycleMixin (#31/#38/#42).

    # PM's third lifecycle axis — the operationally-live-vs-dissolved domain flag
    # (``organizations.active``, orgs-only; power-map#240/usa-wa#43). Mirrored from PM
    # (authority="pm") in the org descriptor's ``upsert_from_pm``. Distinct from the
    # archived/deleted tombstones: a dissolved committee is **inactive**, not archived,
    # so ``active`` is a plain column here — NOT in LifecycleMixin/is_live/live_only.
    # It never hides a row from reads; inactive orgs stay in the read fan-out.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class Role(Base, TimestampMixin, LifecycleMixin):
    """A named slot within an Organization. Roles are templates; Assignment binds them in time."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_roles_natural_key"),
        # Semantic uniqueness — a named slot within an Organization is one Role
        # ("Representative" under the House chamber). District context (LD-21)
        # lives on the Assignment/Person, not the Role. Jurisdiction is derived
        # via the org (decoupling 2026-06-09).
        UniqueConstraint("organization_id", "name", name="uq_roles_org_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    organization_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    role_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # role_type vocab: elected_member | leadership | committee_member
    #                | committee_leadership | staff | party_member | other

    # PM anchor (sidecar sync). Write path dormant until power-map#176 ships
    # the roles observation endpoint.
    pm_role_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
    # archived_at + deleted_at tombstones provided by LifecycleMixin (#31/#38/#42).


class Assignment(Base, TimestampMixin, LifecycleMixin):
    """Person × Role × Period. Bridges people to their chamber/party/committee context."""

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_assignments_natural_key"),
        CheckConstraint(
            "person_id IS NOT NULL OR holder_name_raw IS NOT NULL",
            name="ck_assignments_person_or_name",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    # Jurisdiction derived via role → org (decoupling 2026-06-09).
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    person_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.persons.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    holder_name_raw: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # PM anchor (sidecar sync). Write path dormant until power-map#177 ships
    # the assignments observation endpoint.
    pm_assignment_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
    # archived_at + deleted_at tombstones provided by LifecycleMixin (#31/#38/#42).


class PersonIdentifier(Base, TimestampMixin):
    """External-ID mapping per Person — bioguide, LIS, FollowTheMoney, etc."""

    __tablename__ = "person_identifiers"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_person_identifiers_natural_key"),
        UniqueConstraint("person_id", "scheme", name="uq_person_identifiers_person_scheme"),
        UniqueConstraint(
            "scheme",
            "value",
            name="uq_person_identifiers_scheme_value",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    # Jurisdiction-free: a person's external IDs are global (decoupling 2026-06-09).
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    person_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheme: Mapped[str] = mapped_column(String(64), nullable=False)
    # Common scheme slugs: bioguide | lis | ftm_eid | votesmart | opensecrets
    #                    | ballotpedia | knowwho_pid | icpsr | wikipedia
    #                    | wsl_member_id | pdc_filer_id | powermap

    value: Mapped[str] = mapped_column(String(128), nullable=False)
    verified_at: Mapped["Text | None"] = mapped_column(Text, nullable=True)  # ISO timestamp string


class OrganizationIdentifier(Base, TimestampMixin):
    """External-ID mapping per Organization — FEC, IRS EIN, OpenSecrets, etc."""

    __tablename__ = "organization_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_id",
            name="uq_organization_identifiers_natural_key",
        ),
        UniqueConstraint(
            "organization_id", "scheme", name="uq_organization_identifiers_org_scheme"
        ),
        UniqueConstraint(
            "scheme",
            "value",
            name="uq_organization_identifiers_scheme_value",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    # Jurisdiction-free: an org's external IDs are global (decoupling 2026-06-09).
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    organization_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheme: Mapped[str] = mapped_column(String(64), nullable=False)
    # Common scheme slugs: wsl_committee_id | pdc_filer_id | fec_committee_id
    #                    | irs_ein | opensecrets_org | ftm_org_eid | powermap

    value: Mapped[str] = mapped_column(String(128), nullable=False)
    verified_at: Mapped["Text | None"] = mapped_column(Text, nullable=True)


class OrganizationName(Base, TimestampMixin):
    """A single dated name variant for an Organization — mirror of PM's ``OrgName``.

    PM ships dated org names (power-map#239): a name valid over an
    ``[effective_start, effective_end)`` window (e.g. a committee renamed
    mid-biennium). ``Organization.name`` stays the resolved **current** scalar (the
    hot-path live read); this child table is the history/association surface —
    queried by name when historical WSL data references a *former* committee name
    (usa-wa#45).

    Read-mirror only: the org descriptor's ``upsert_from_pm`` mirrors the embedded
    ``OrgDetail.names[]`` via ``sync_org_names``. usa-wa does not write this table
    as a producer — the rename producer (usa-wa#46) emits to PM and the mirror
    brings it back. ``(source, source_id)`` is the idempotency key; ``source_id``
    is PM's ``OrgName`` id, so it equals ``pm_org_name_id`` for mirrored rows.

    No ``(organization_id, name)`` unique constraint: the same name can recur
    across disjoint windows.
    """

    __tablename__ = "organization_names"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_organization_names_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    organization_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    # Open vocab mirrored verbatim from PM (legal | common | former | …); no CHECK —
    # PM is system-of-record and a CHECK would 422-drift on a new PM slug (usa-wa#45).
    name_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Per-name PM anchor (sidecar sync) — PM's ``OrgName`` id.
    pm_org_name_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)


class OrganizationAcronym(Base, TimestampMixin):
    """A single acronym variant for an Organization — mirror of PM's ``OrgAcronym``.

    PM models acronyms as a list **separate** from ``names``
    (``acronyms: list[OrgAcronym]`` embedded in ``OrgDetail``). ``Organization.acronym``
    stays the resolved **current** scalar (the hot-path live read) — the org descriptor's
    ``upsert_from_pm`` adopts PM's ``is_canonical`` entry into it (usa-wa#65), symmetric
    with the ``name`` adoption; this child table is the history/association surface —
    queried by acronym when historical WSL data references a *former* committee acronym
    (usa-wa#47).

    Thinner than :class:`OrganizationName`: PM's ``OrgAcronym`` is
    ``{id, acronym, is_canonical}`` only — no ``name_type``, no dated window. So no
    ``effective_*`` columns and no type vocab.

    Read-mirror only: the org descriptor's ``upsert_from_pm`` mirrors the embedded
    ``OrgDetail.acronyms[]`` via ``sync_org_acronyms``. usa-wa does not write this table
    as a producer — the rename producer (usa-wa#46) emits to PM and the mirror brings
    it back. ``(source, source_id)`` is the idempotency key; ``source_id`` is PM's
    ``OrgAcronym`` id, so it equals ``pm_org_acronym_id`` for mirrored rows.

    No ``(organization_id, acronym)`` unique constraint: the same acronym can recur
    (canonical → former → re-adopted).
    """

    __tablename__ = "organization_acronyms"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_organization_acronyms_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    organization_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    acronym: Mapped[str] = mapped_column(String(64), nullable=False)
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Per-acronym PM anchor (sidecar sync) — PM's ``OrgAcronym`` id.
    pm_org_acronym_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)


class EntityEvent(Base, TimestampMixin):
    """Polymorphic lifecycle event for a Person or Organization.

    Mirrors Power Map's ``ObservationEventItem`` (power-map#170): birth / death
    for people, founding / dissolution for organizations. ``entity_id`` is
    polymorphic (resolved by ``entity_kind``) so it carries no DB-level FK.

    The event instant is stored as granular, individually nullable components
    (``event_year`` … ``event_second``) rather than a single ``Date`` so partial
    dates round-trip faithfully (e.g. "born 1970, month unknown"). The event
    type is referenced either by ``event_type_slug`` *or* by ``event_type_id``
    (exactly one — see ``ck_entity_events_event_type_xor``), mirroring PM's
    slug-or-id dispatch.

    Read-mirror sync is wired (usa-wa#19): the person/org descriptors pull
    ``GET /{people|orgs}/{id}/events`` and refresh this mirror via
    ``sync_entity_events``. The write direction (person/org ``to_observation``
    embedding ``events``) is deferred until a local adapter actually *produces*
    entity events — nothing writes this table today, so an embed would always be
    empty (tracked as a usa-wa follow-up).
    """

    __tablename__ = "entity_events"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_entity_events_natural_key"),
        CheckConstraint(
            "entity_kind IN ('person', 'organization')",
            name="ck_entity_events_kind",
        ),
        CheckConstraint(
            "(event_type_slug IS NOT NULL) <> (event_type_id IS NOT NULL)",
            name="ck_entity_events_event_type_xor",
        ),
        CheckConstraint(
            "visibility IN ('public', 'legal_only', 'hidden')",
            name="ck_entity_events_visibility",
        ),
        CheckConstraint(
            "linked_entity_kind IS NULL OR linked_entity_kind IN ('person', 'organization')",
            name="ck_entity_events_linked_entity_kind",
        ),
        CheckConstraint(
            "(linked_entity_kind IS NULL) = (linked_entity_id IS NULL)",
            name="ck_entity_events_linked_entity_together",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    # Jurisdiction derived via the parent org (person-events have none). 2026-06-09.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)

    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False, index=True)

    # Event type — slug XOR id (exactly one set). Slug vocab is open per PM:
    # birth | death | founding | dissolution | other.
    event_type_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)

    # Granular partial-date components; each nullable so any prefix can be
    # known independently (e.g. year-only).
    event_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_second: Mapped[int | None] = mapped_column(Integer, nullable=True)

    event_place_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured place address mirrored verbatim from PM (id/city/region/…); JSONB
    # so the nested optional sub-object round-trips without flattening (#19).
    event_place_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Curatorial mirror fields from PM's read EntityEvent (#19).
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PM's own record-creation timestamp (distinct from this row's created_at).
    pm_created_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Visibility — constrained to PM's enum: public | legal_only | hidden.
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="public")

    # Optional polymorphic link to another entity (set together or not at all).
    linked_entity_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    linked_entity_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)

    # PM anchor (sidecar sync).
    pm_entity_event_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
