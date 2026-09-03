"""Registry seed (#308): canonical identity → the registry, ULIDs preserved.

    python -m usa_wa_pipeline.registry_seed [--json]

One cluster per canonical Person (its ``source:source_id`` key plus every
``scheme:value`` child identifier) and per canonical Organization
(``source:source_id``), applied through the registrar decision table with the
canonical ULID as the minted id — **existing ULIDs survive the replatform by
construction** (replatform spec § Identity registry), which is what keeps the
PM crosswalk seed (#312) valid. Idempotent: a re-run no-ops via the decision
table; a conflict (two canonical rows sharing a key — pre-existing duplicate)
is counted and logged, never guessed at.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import (
    KIND_ORG,
    KIND_PERSON,
    apply_decision,
    decide,
    registered_view,
)
from clearinghouse_domain_legislative.identity import Organization, Person, PersonIdentifier

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "registry-seed"


async def seed_registry(session: AsyncSession) -> dict[str, int]:
    """Register every canonical person + org cluster. Returns counters."""
    summary = {"persons_minted": 0, "orgs_minted": 0, "appended": 0, "noops": 0, "conflicts": 0}

    identifiers: dict[str, list[str]] = {}
    for person_id, scheme, value in (
        await session.execute(
            select(PersonIdentifier.person_id, PersonIdentifier.scheme, PersonIdentifier.value)
        )
    ).all():
        identifiers.setdefault(str(person_id), []).append(f"{scheme}:{value}")

    view = await registered_view(session, KIND_PERSON)
    for person in (await session.execute(select(Person))).scalars():
        cluster = frozenset(
            [f"{person.source}:{person.source_id}", *identifiers.get(str(person.id), [])]
        )
        decision = decide(cluster, view)
        if decision.action == "conflict":
            logger.error(
                "registry_seed_conflict",
                extra={"kind": KIND_PERSON, "cluster": sorted(cluster)},
            )
            summary["conflicts"] += 1
            continue
        resolved = await apply_decision(
            session, KIND_PERSON, decision, registered_by="seed", entity_id=str(person.id)
        )
        if decision.action == "mint":
            summary["persons_minted"] += 1
        elif decision.action == "append":
            summary["appended"] += 1
        else:
            summary["noops"] += 1
        for key in decision.keys_to_register:
            view[key] = resolved  # keep the in-memory view current within the run

    view = await registered_view(session, KIND_ORG)
    for org in (await session.execute(select(Organization))).scalars():
        cluster = frozenset([f"{org.source}:{org.source_id}"])
        decision = decide(cluster, view)
        if decision.action == "conflict":
            logger.error(
                "registry_seed_conflict", extra={"kind": KIND_ORG, "cluster": sorted(cluster)}
            )
            summary["conflicts"] += 1
            continue
        resolved = await apply_decision(
            session, KIND_ORG, decision, registered_by="seed", entity_id=str(org.id)
        )
        if decision.action == "mint":
            summary["orgs_minted"] += 1
        elif decision.action == "append":
            summary["appended"] += 1
        else:
            summary["noops"] += 1
        for key in decision.keys_to_register:
            view[key] = resolved

    logger.info("registry_seed_complete", extra=dict(summary))
    return summary


async def _seed_job(ctx: JobContext) -> JobResult:
    summary = await seed_registry(ctx.require_session())
    if summary["conflicts"]:
        return JobResult.degraded(summary)
    return JobResult.ok(summary)


def main(argv: list[str] | None = None) -> int:
    """Seed the identity registry from canonical rows. Exit ``4`` on conflicts."""
    return run_job(
        JOB_SLUG,
        _seed_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.registry_seed",
        description="Seed the identity registry from canonical rows, ULIDs preserved (#308).",
    )


if __name__ == "__main__":
    raise SystemExit(main())
