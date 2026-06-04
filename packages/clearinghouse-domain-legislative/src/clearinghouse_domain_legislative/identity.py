"""Identity cluster — power-map terminology.

Person + Organization + Role + Assignment, plus the N:1 external-ID child
tables PersonIdentifier and OrganizationIdentifier.

usa-wa is a producer of identity data; the long-term archival store is
power-map. Local copies in this schema serve query latency; the canonical
truth lives upstream (see project memory: project_identity_producer_archival).
"""

from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
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


class Person(Base, TimestampMixin):
    """A human. Replaces Legislator from the P0 skeleton."""

    __tablename__ = "persons"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "source", "source_id", name="uq_persons_natural_key"),
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


class Organization(Base, TimestampMixin):
    """Any non-person legal/political entity. Discriminated by org_type."""

    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_organizations_natural_key"
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

    name: Mapped[str] = mapped_column(String(512), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    org_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # org_type vocab: chamber | party | committee | subcommittee | caucus
    #               | candidate_committee | lobbying_firm | pac
    #               | government_agency | other

    parent_organization_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # PM anchor (sidecar sync). Was ``powermap_organization_id`` pre-sidecar.
    pm_organization_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)


class Role(Base, TimestampMixin):
    """A named slot within an Organization. Roles are templates; Assignment binds them in time."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "source", "source_id", name="uq_roles_natural_key"),
        # Semantic uniqueness — a chamber-position within a jurisdiction is one
        # Role. Pre-v1.4 also keyed on `district` (text label); v1.4 collapses
        # district context into `jurisdiction_id` (e.g., LD-21 cache row) so
        # (jurisdiction_id, organization_id, name) is the new natural key.
        UniqueConstraint("jurisdiction_id", "organization_id", "name", name="uq_roles_org_name"),
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


class Assignment(Base, TimestampMixin):
    """Person × Role × Period. Bridges people to their chamber/party/committee context."""

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_assignments_natural_key"
        ),
        CheckConstraint(
            "person_id IS NOT NULL OR holder_name_raw IS NOT NULL",
            name="ck_assignments_person_or_name",
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


class PersonIdentifier(Base, TimestampMixin):
    """External-ID mapping per Person — bioguide, LIS, FollowTheMoney, etc."""

    __tablename__ = "person_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_person_identifiers_natural_key"
        ),
        UniqueConstraint("person_id", "scheme", name="uq_person_identifiers_person_scheme"),
        UniqueConstraint(
            "jurisdiction_id",
            "scheme",
            "value",
            name="uq_person_identifiers_jurisdiction_scheme_value",
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
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_organization_identifiers_natural_key",
        ),
        UniqueConstraint(
            "organization_id", "scheme", name="uq_organization_identifiers_org_scheme"
        ),
        UniqueConstraint(
            "jurisdiction_id",
            "scheme",
            "value",
            name="uq_organization_identifiers_jurisdiction_scheme_value",
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

    Mirrors Power Map's entity-events surface (power-map#170): birth / death for
    people, founding / dissolution for organizations. ``entity_id`` is
    polymorphic (resolved by ``entity_kind``) so it carries no DB-level FK.

    Sidecar sync is fully dormant until power-map#178 wires the public
    entity-events router (read + observation); the local mirror + anchor exist
    now so the schema is ready when that lands.
    """

    __tablename__ = "entity_events"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_entity_events_natural_key"
        ),
        CheckConstraint(
            "entity_kind IN ('person', 'organization')",
            name="ck_entity_events_kind",
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

    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[_ULID] = mapped_column(ULID(), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # event_type vocab: birth | death | founding | dissolution | other
    date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # PM anchor (sidecar sync).
    pm_entity_event_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
