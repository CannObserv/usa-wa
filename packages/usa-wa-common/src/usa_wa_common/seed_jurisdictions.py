"""Seed the WA jurisdiction registry from the local vocabulary (#310).

    python -m usa_wa_common.seed_jurisdictions [--json]

The ownership transfer: `usa_wa_common.jurisdictions` (declared WA facts,
extracted verbatim from the PM mirror) becomes the writer of
``clearinghouse_core.jurisdictions``. Upserts by slug — creates what is
missing, **asserts** a drifted name/type back to the vocabulary (post-#302
names are ours, not PM's), and leaves rows outside the vocabulary alone
(counted as ``unknown_local``, never deleted — the PM-discovered Seattle row
survives until someone decides otherwise). Idempotent; run after a vocabulary
edit (e.g. redistricting) and at cutover. The sidecar's jurisdiction sync
becomes redundant and retires at #314.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.logging import get_logger
from usa_wa_common.jurisdictions import JURISDICTION_TYPE_NAMES, WA_JURISDICTIONS

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "jurisdictions-seed"


async def seed_jurisdictions(session: AsyncSession) -> dict[str, int]:
    """Assert the vocabulary into the table. Returns counters."""
    types = {t.slug: t for t in (await session.execute(select(JurisdictionType))).scalars()}
    for slug, display_name in JURISDICTION_TYPE_NAMES.items():
        if slug not in types:
            row = JurisdictionType(slug=slug, display_name=display_name)
            session.add(row)
            await session.flush()
            types[slug] = row

    existing = {j.slug: j for j in (await session.execute(select(Jurisdiction))).scalars()}
    summary = {"created": 0, "updated": 0, "unchanged": 0, "unknown_local": 0}
    vocabulary_slugs = set()
    for fact in WA_JURISDICTIONS:
        vocabulary_slugs.add(fact.slug)
        type_id = types[fact.type_slug].id
        row = existing.get(fact.slug)
        if row is None:
            session.add(
                Jurisdiction(
                    slug=fact.slug,
                    name=fact.name,
                    type_id=type_id,
                    recorded_at=datetime.now(UTC),
                )
            )
            summary["created"] += 1
        elif row.name != fact.name or row.type_id != type_id:
            logger.info(
                "jurisdiction_asserted",
                extra={"slug": fact.slug, "from_name": row.name, "to_name": fact.name},
            )
            row.name = fact.name
            row.type_id = type_id
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1
    for slug in existing:
        if slug not in vocabulary_slugs:
            summary["unknown_local"] += 1
            logger.info("jurisdiction_outside_vocabulary", extra={"slug": slug})
    await session.flush()
    # "created" is a reserved stdlib LogRecord attribute — nest the counters.
    logger.info("jurisdictions_seed_complete", extra={"summary": dict(summary)})
    return summary


async def _seed_job(ctx: JobContext) -> JobResult:
    return JobResult.ok(await seed_jurisdictions(ctx.require_session()))


def main(argv: list[str] | None = None) -> int:
    """Seed/assert the WA jurisdiction registry. Idempotent."""
    return run_job(
        JOB_SLUG,
        _seed_job,
        argv=argv,
        prog="python -m usa_wa_common.seed_jurisdictions",
        description="Assert the locally-owned WA jurisdiction vocabulary into the table (#310).",
    )


if __name__ == "__main__":
    raise SystemExit(main())
