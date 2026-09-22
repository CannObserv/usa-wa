"""Registry crosswalk parity probe (#308): canonical identity ⊆ the registry.

    python -m usa_wa_pipeline.parity_registry [--json]

Write-free. For every canonical Person and Organization, the registry must map
its ``source:source_id`` key to its own ULID — the invariant the seed created
and the sticky registrar must never erode. Registry keys with no canonical
counterpart (matching-appended crosswalk keys, e.g. roster attestation keys)
are the registry doing its job, reported as a count only. A key whose RAW
binding is the canonical ULID stays clean even when that entity was later
merged away, and a binding that reaches the same survivor as the canonical
ULID resolves to is an **adjudicated merge** — sanctioned policy, its own
count, never a standing ``mismapped`` alarm (#302 CR: alert fatigue is how
#49 alerting dies).

The ULID half of the invariant holds only for identities the seed carried
across. After it, the canonical tier and the registrar mint independently, so a
canonical row created later — one whose ULID the registry never held — cannot
carry the registrar's ULID for the same key. That key bound to the registrar's
entity is ``post_seed``: counted, never alarmed, because no seeded identity
moved. Its key must still be registered — unbound, it is ``missing`` as ever
(2026-09-22: Joint committee 36500, the first entity of any kind born after
the seed, went unregistered because the registrar had no org pass).

Exit ``0`` clean · ``1`` any canonical row unmapped or mapped to a different
identity graph.
"""

from __future__ import annotations

from sqlalchemy import select

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import (
    KIND_ORG,
    KIND_PERSON,
    RegistryEntity,
    merge_map,
    registered_view,
    resolve_merged,
)
from clearinghouse_domain_legislative.identity import Organization, Person

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "parity-registry"


async def run_parity(session) -> tuple[dict[str, int], bool]:
    """The probe's comparator: (counters, failed). Read-only on both stores."""
    counters: dict[str, int] = {}
    failed = False
    for kind, model in ((KIND_PERSON, Person), (KIND_ORG, Organization)):
        view = await registered_view(session, kind, resolve_merges=False)
        merges = await merge_map(session, kind)
        entities = {
            str(entity_id)
            for entity_id in (
                await session.execute(select(RegistryEntity.id).where(RegistryEntity.kind == kind))
            ).scalars()
        }
        missing = 0
        mismapped = 0
        merged = 0
        post_seed = 0
        total = 0
        for row_id, source, source_id in (
            await session.execute(select(model.id, model.source, model.source_id))
        ).all():
            total += 1
            mapped = view.get(f"{source}:{source_id}")
            if mapped is None:
                missing += 1
            elif mapped == str(row_id):
                continue  # the seed invariant holds, merged away or not
            elif resolve_merged(merges, mapped) == resolve_merged(merges, str(row_id)):
                merged += 1  # adjudicated merge: same survivor, sanctioned policy
            elif str(row_id) not in entities:
                post_seed += 1  # canonical minted after the seed: independent ULIDs
            else:
                mismapped += 1
        counters[f"{kind}_canonical"] = total
        counters[f"{kind}_registry_keys"] = len(view)
        counters[f"{kind}_missing"] = missing
        counters[f"{kind}_mismapped"] = mismapped
        counters[f"{kind}_merged"] = merged
        counters[f"{kind}_post_seed"] = post_seed
        if missing or mismapped:
            failed = True
            logger.error(
                "parity_registry_divergence",
                extra={"kind": kind, "missing": missing, "mismapped": mismapped},
            )
    return counters, failed


async def _parity_job(ctx: JobContext) -> JobResult:
    counters, failed = await run_parity(ctx.require_session())
    if failed:
        return JobResult.failed(counters, exit_code=1)
    logger.info("parity_registry_clean", extra=dict(counters))
    return JobResult.ok(counters)


def main(argv: list[str] | None = None) -> int:
    """Verify every canonical row's key maps to its own ULID in the registry."""
    return run_job(
        JOB_SLUG,
        _parity_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.parity_registry",
        description="Write-free parity: canonical identity ⊆ the registry crosswalk (#308).",
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
