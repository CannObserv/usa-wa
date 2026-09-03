"""The identity registry (#308): the #302 pipeline's only mutable state.

Cross-source matching is a transform; identity assignment is a ledger. This
module is that ledger — deliberately **jurisdiction-blind**: clusters, opaque
namespaced keys (``"wsl:27992"``, ``"roster:<fold>:<year>"``), and one decision
table. All domain knowledge lives in the matching rules that *propose*
clusters (`usa_wa_pipeline`, dbt SQL + Splink config), none in here.

Three tables in the ``registry`` Postgres schema — master state, small, backed
up with the database:

- ``registry.entities`` — one row per identity (persons and orgs, discriminated
  by ``kind``). ``merged_into`` is the tombstone an adjudicated merge leaves;
  the published crosswalk carries it, and it is the only signal PM's mapping
  layer gets to re-point a merged-away entity (spec § walkthrough).
- ``registry.entity_keys`` — natural key → entity, append-only in spirit.
  Membership never decays: a key absent from today's staging is a staging
  fact, not an identity fact.
- ``registry.adjudications`` — the human decisions (merge/split/move), each
  with a note; corrections are always adjudications, never side effects of a
  matching-rule change (sticky registry, spec § tradeoffs).

The decision table itself is :func:`decide` — pure, so its five rows are
unit-tested without a database; :func:`apply_decision` maps one decision onto
the tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _PyULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base

SCHEMA = "registry"

KIND_PERSON = "person"
KIND_ORG = "org"


def _new_ulid() -> _PyULID:
    return _PyULID()


class RegistryEntity(Base):
    """One registered identity. ``id`` is the ULID published datasets carry."""

    __tablename__ = "entities"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[_PyULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    merged_into: Mapped[_PyULID | None] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.entities.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class RegistryKey(Base):
    """Natural key → entity. The crosswalk's rows."""

    __tablename__ = "entity_keys"
    __table_args__ = (
        UniqueConstraint("kind", "natural_key", name="uq_registry_kind_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_PyULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    natural_key: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_id: Mapped[_PyULID] = mapped_column(
        ULID(), ForeignKey(f"{SCHEMA}.entities.id"), nullable=False, index=True
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    #: What registered it: ``seed`` | ``registrar`` | ``adjudication:<id>``.
    registered_by: Mapped[str] = mapped_column(String(64), nullable=False)


class RegistryAdjudication(Base):
    """A human identity decision — the only path that moves or merges.

    Declared tier until the triage CLI lands with the rest of #308 — the
    registrar core ships first so the seed can run.
    """

    __implementation_status__ = "declared"
    __implementation_tracking_issues__ = (308,)
    __implementation_rationale__ = (
        "The registrar core + seed ship first; the triage CLI that writes "
        "adjudications lands with the matching half of #308."
    )

    __tablename__ = "adjudications"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[_PyULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # merge | split | move
    subject_entity_id: Mapped[_PyULID | None] = mapped_column(ULID(), nullable=True)
    target_entity_id: Mapped[_PyULID | None] = mapped_column(ULID(), nullable=True)
    natural_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


Action = Literal["mint", "append", "noop", "conflict"]


@dataclass(frozen=True)
class Decision:
    """The registrar's verdict for one proposed cluster."""

    action: Action
    keys_to_register: frozenset[str] = field(default_factory=frozenset)
    entity_id: str | None = None
    entity_ids: frozenset[str] = field(default_factory=frozenset)


def decide(cluster: frozenset[str], registered: dict[str, str]) -> Decision:
    """The decision table (spec § registrar): pure over a cluster's keys and the
    registry's view of them (key → entity id, resolved through merges).

    - none known → **mint** (id assigned at apply time)
    - all known members map to one entity → **append** the new keys (no-op when
      there are none)
    - members map to ≥2 entities → **conflict**, no write — merge is human work
    - stickiness is implicit: a registered key's mapping is never changed here
    """
    known = {key: registered[key] for key in cluster if key in registered}
    unknown = frozenset(key for key in cluster if key not in registered)
    entity_ids = frozenset(known.values())
    if len(entity_ids) > 1:
        return Decision("conflict", entity_ids=entity_ids)
    if not entity_ids:
        return Decision("mint", keys_to_register=unknown or cluster)
    entity_id = next(iter(entity_ids))
    if not unknown:
        return Decision("noop", entity_id=entity_id)
    return Decision("append", keys_to_register=unknown, entity_id=entity_id)


async def registered_view(session: AsyncSession, kind: str) -> dict[str, str]:
    """Natural key → LIVE entity id (merges resolved to the survivor)."""
    rows = (
        await session.execute(
            select(RegistryKey.natural_key, RegistryKey.entity_id).where(RegistryKey.kind == kind)
        )
    ).all()
    merges = {
        str(entity_id): str(merged_into)
        for entity_id, merged_into in (
            await session.execute(
                select(RegistryEntity.id, RegistryEntity.merged_into).where(
                    RegistryEntity.kind == kind, RegistryEntity.merged_into.isnot(None)
                )
            )
        ).all()
    }

    def resolve(entity_id: str) -> str:
        seen = set()
        while entity_id in merges and entity_id not in seen:
            seen.add(entity_id)
            entity_id = merges[entity_id]
        return entity_id

    return {key: resolve(str(entity_id)) for key, entity_id in rows}


async def apply_decision(
    session: AsyncSession,
    kind: str,
    decision: Decision,
    *,
    registered_by: str,
    entity_id: str | None = None,
) -> str | None:
    """Apply one mint/append to the tables; noop/conflict write nothing.

    Returns the entity id the cluster resolved to (``None`` for conflict).
    ``entity_id`` overrides the minted id — the seed path passes the canonical
    ULID so identity survives the replatform.
    """
    if decision.action == "conflict":
        return None
    if decision.action == "noop":
        return decision.entity_id
    if decision.action == "mint":
        entity = RegistryEntity(kind=kind, **({"id": entity_id} if entity_id else {}))
        session.add(entity)
        await session.flush()
        target = str(entity.id)
    else:
        target = decision.entity_id
    for key in decision.keys_to_register:
        session.add(
            RegistryKey(kind=kind, natural_key=key, entity_id=target, registered_by=registered_by)
        )
    await session.flush()
    return str(target)
