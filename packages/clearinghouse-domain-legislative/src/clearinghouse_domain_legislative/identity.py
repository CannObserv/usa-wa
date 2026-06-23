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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class RetirableMixin:
    """Soft-delete tombstone shared by the four identity models (usa-wa#31/#36/#38).

    ``retired_at`` is stamped when PM deletes the entity with no surviving merge-
    winner — a genuine delete, not a merge. The row is **kept as provenance**
    (the local cache mirrors PM; the tombstone is the evidence of the delete),
    never hard-deleted. Retired rows are excluded from the PM-sync sweep/reconcile
    (never re-created or re-fetched) and must be excluded from *live* reads via
    :func:`clearinghouse_domain_legislative.queries.exclude_retired`.
    """

    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @classmethod
    def not_retired(cls) -> ColumnElement[bool]:
        """SQL predicate selecting only live (non-retired) rows: ``retired_at IS NULL``."""
        return cls.retired_at.is_(None)


class Person(Base, TimestampMixin, RetirableMixin):
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
    # retired_at tombstone provided by RetirableMixin (#31/#38).


class Organization(Base, TimestampMixin, RetirableMixin):
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
    # value. Added 2026-06-18 for WSL committees.
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
    # retired_at tombstone provided by RetirableMixin (#31/#38).


class Role(Base, TimestampMixin, RetirableMixin):
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
    # retired_at tombstone provided by RetirableMixin (#31/#38).


class Assignment(Base, TimestampMixin, RetirableMixin):
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
    # retired_at tombstone provided by RetirableMixin (#31/#38).


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
