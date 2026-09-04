"""The `serving` schema (#313): a disposable projection of the published datasets.

**Disposable, and therefore not migrated.** These tables carry their own
``MetaData`` rather than joining ``clearinghouse_core.models.Base``, so alembic
neither autogenerates nor owns them: the loader creates what is missing and
replaces every row on each run, and the whole schema can be dropped and rebuilt
from ``published/`` alone. That is the acceptance criterion for #313, and it is
also the reason a migration would be wrong — a migration implies state worth
preserving, and there is none here.

**Shape follows the datapackage, not the old canonical tables** (spec § Contract
diff). What is gone: ``pm_*`` anchor ids, the ``source``/``source_id`` scalars
on persons and orgs (multi-source by construction — the crosswalk carries the
keys), row-level ``created_at``/``updated_at`` (the dataset *version* is the
clock), and the lifecycle tombstone columns (absence and the crosswalk's
``merged_into`` replace them). What arrives new: the span key's parts as real
columns, so ``/api/v1/assignments?span_kind=`` filters a column instead of
splitting a string — which is what retires #335.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, MetaData, String, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "serving"


class ServingBase(DeclarativeBase):
    """Its own metadata — see the module docstring on why alembic must not see it."""

    metadata = MetaData(schema=SCHEMA)


class Person(ServingBase):
    """One live person entity. Name survivorship is already resolved upstream;
    ``name_source`` records which source won, which is the audit trail the old
    per-source scalars used to provide."""

    __tablename__ = "persons"

    entity_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name_full: Mapped[str | None] = mapped_column(String(512))
    name_source: Mapped[str | None] = mapped_column(String(64))


class Organization(ServingBase):
    """One live organization entity, with its newest-biennium attributes."""

    __tablename__ = "organizations"

    entity_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(512))
    long_name: Mapped[str | None] = mapped_column(String(512))
    acronym: Mapped[str | None] = mapped_column(String(64))
    agency: Mapped[str | None] = mapped_column(String(64))
    org_type: Mapped[str | None] = mapped_column(String(64))
    first_biennium: Mapped[str | None] = mapped_column(String(16))
    last_biennium: Mapped[str | None] = mapped_column(String(16))


class Role(ServingBase):
    """One seat/slot. ``role_key`` is the deterministic structural name (#309)
    and ``entity_id`` the registry ULID the API addresses it by (#313) — both
    published, because the key is what Power Map matches a seat on."""

    __tablename__ = "roles"

    role_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    entity_id: Mapped[str | None] = mapped_column(String(26), index=True)
    role_type: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(512))
    span_kind: Mapped[str | None] = mapped_column(String(64))
    span_discriminator: Mapped[str | None] = mapped_column(String(128))
    org_source_id: Mapped[str | None] = mapped_column(String(256))
    org_entity_id: Mapped[str | None] = mapped_column(String(26), index=True)
    district: Mapped[int | None] = mapped_column()
    qualifier: Mapped[str | None] = mapped_column(String(64))


class Assignment(ServingBase):
    """One tenure span, bound to a person and a role.

    Keyed on ``(source, member_id, span_kind, span_discriminator,
    span_start_biennium)`` — the span identity spelled out, rather than the
    4-part ``source_id`` string the old table carried. The roster family's
    member ids contain colons, which is exactly why the parts are columns now.
    """

    __tablename__ = "assignments"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    span_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    span_discriminator: Mapped[str] = mapped_column(String(128), primary_key=True)
    span_start_biennium: Mapped[str] = mapped_column(String(16), primary_key=True)
    entity_id: Mapped[str | None] = mapped_column(String(26), index=True)
    role_key: Mapped[str | None] = mapped_column(String(256), index=True)
    span_end_biennium: Mapped[str | None] = mapped_column(String(16))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool | None] = mapped_column(Boolean)


class PersonCrosswalk(ServingBase):
    """Natural key → person entity, with the merge tombstone. The un-embedded
    successor to ``PersonDetail.identifiers`` (spec § Contract diff)."""

    __tablename__ = "person_crosswalk"

    natural_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    entity_id: Mapped[str | None] = mapped_column(String(26), index=True)
    key_namespace: Mapped[str | None] = mapped_column(String(64))
    key_value: Mapped[str | None] = mapped_column(String(256))
    registered_by: Mapped[str | None] = mapped_column(String(64))
    merged_into: Mapped[str | None] = mapped_column(String(26))


class OrgCrosswalk(ServingBase):
    """Natural key → org entity, with the merge tombstone."""

    __tablename__ = "org_crosswalk"

    natural_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    entity_id: Mapped[str | None] = mapped_column(String(26), index=True)
    key_namespace: Mapped[str | None] = mapped_column(String(64))
    key_value: Mapped[str | None] = mapped_column(String(256))
    registered_by: Mapped[str | None] = mapped_column(String(64))
    merged_into: Mapped[str | None] = mapped_column(String(26))


class LoadState(ServingBase):
    """Which published version each served table currently holds (CR 92).

    Without this the only currency signal is a row count, and an **unchanged
    count is the normal case** for this corpus — the publisher has a
    skip-if-unchanged path precisely because quiet days are typical, so
    yesterday's 8,772 assignments are indistinguishable from today's. The whole
    value of immutable versioned datasets is provenance, and a consumer that
    keeps none cannot answer the one question its health probe asks.

    One row per dataset: this is what *is* loaded, not a history of what was.
    The history of load runs belongs to the job ledger (#178).
    """

    __tablename__ = "load_state"

    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    #: The digest the datapackage published, verified before the load (CR 91).
    sha256: Mapped[str | None] = mapped_column(String(80))
    rows: Mapped[int] = mapped_column(Integer)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


#: dataset name → the table it loads into. The names are the *published* ones,
#: so this mapping is also the statement of which datasets the API depends on.
#: ``load_state`` is deliberately absent: it is the loader's own bookkeeping,
#: not a projection of anything published.
SERVING_TABLES: dict[str, Table] = {
    "persons": Person.__table__,
    "organizations": Organization.__table__,
    "roles": Role.__table__,
    "assignments": Assignment.__table__,
    "person_crosswalk": PersonCrosswalk.__table__,
    "org_crosswalk": OrgCrosswalk.__table__,
}
