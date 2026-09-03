"""Registry crosswalk parity probe (#308): canonical identity ⊆ the registry.

    python -m usa_wa_pipeline.parity_registry [--json]

Write-free. For every canonical Person and Organization, the registry must map
its ``source:source_id`` key to its own ULID — the invariant the seed created
and the sticky registrar must never erode. Registry keys with no canonical
counterpart (matching-appended crosswalk keys, e.g. roster attestation keys)
are the registry doing its job, reported as a count only.

Exit ``0`` clean · ``1`` any canonical row unmapped or mapped elsewhere.
"""

from __future__ import annotations

from sqlalchemy import select

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import KIND_ORG, KIND_PERSON, registered_view
from clearinghouse_domain_legislative.identity import Organization, Person

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "parity-registry"


async def _parity_job(ctx: JobContext) -> JobResult:
    session = ctx.require_session()
    counters: dict[str, int] = {}
    failed = False
    for kind, model in ((KIND_PERSON, Person), (KIND_ORG, Organization)):
        view = await registered_view(session, kind)
        missing = 0
        mismapped = 0
        total = 0
        for row_id, source, source_id in (
            await session.execute(select(model.id, model.source, model.source_id))
        ).all():
            total += 1
            mapped = view.get(f"{source}:{source_id}")
            if mapped is None:
                missing += 1
            elif mapped != str(row_id):
                mismapped += 1
        counters[f"{kind}_canonical"] = total
        counters[f"{kind}_registry_keys"] = len(view)
        counters[f"{kind}_missing"] = missing
        counters[f"{kind}_mismapped"] = mismapped
        if missing or mismapped:
            failed = True
            logger.error(
                "parity_registry_divergence",
                extra={"kind": kind, "missing": missing, "mismapped": mismapped},
            )
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
