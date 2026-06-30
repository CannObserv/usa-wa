"""Seed ingest (#39) — materialize the frozen Joint/`Other` cohort, no WSL.

The no-network counterpart to the harvester: on a fresh deploy (or after a DB wipe),
load the checked-in seed and upsert the durable Joint/`Other` ``org_type='other'`` rows
so they exist without contacting WSL. Verifies the seed bytes against their
`seed_manifest` sidecar (`verified_digest`) and fails closed on any mismatch — an
unverifiable seed is never ingested. The returned digest becomes the synthetic
``FetchEvent.content_hash``, unifying the seed under the same provenance baseline (#54)
as a live fetch; the seed bytes themselves are archived as the ``RawPayload``.

Upsert is **fill-only** (``ON CONFLICT DO NOTHING``): the seed is a floor, not an
authority — a body the daily refresh or PM curation already produced (possibly with a
newer name) is left untouched.

    python -m usa_wa_adapter_legislature.ingest_committee_seed [--seed-path PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from clearinghouse_core.seed_manifest import verified_digest
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.committee_seed import DEFAULT_SEED_PATH, deserialize_seed
from usa_wa_adapter_legislature.refresh import (
    _get_or_create_source,
    _resolve_jurisdiction,
    biennium_for_date,
)

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
#: Stable provenance handle for a seed ingest (distinct from live fetch resource ids).
SEED_RESOURCE_ID = "committee-seed:joint-other"


@dataclass(frozen=True)
class IngestSummary:
    """Outcome of one :func:`ingest_committee_seed` run."""

    in_seed: int
    inserted: int
    seed_path: Path


async def ingest_committee_seed(
    session: AsyncSession,
    *,
    seed_path: Path = DEFAULT_SEED_PATH,
) -> IngestSummary:
    """Verify + load the seed; fill-only upsert the Joint/`Other` cohort."""
    content = seed_path.read_bytes()
    content_hash = verified_digest(seed_path, content)  # raises SeedIntegrityError on mismatch
    committees = deserialize_seed(content)

    jurisdiction = await _resolve_jurisdiction(session)
    source = await _get_or_create_source(session, jurisdiction)
    anchors = await bootstrap_synthetic_anchors(
        session,
        biennium=biennium_for_date(datetime.now(UTC).date()),
        jurisdiction_id=jurisdiction.id,
    )

    # Synthetic provenance: the seed is a fetch-equivalent, hashed under the same
    # baseline as live SOAP (#54); its bytes are the archived RawPayload.
    event = FetchEvent(
        source_id=source.id,
        resource_id=SEED_RESOURCE_ID,
        resource_version_key=content_hash.hex(),
        url=seed_path.as_uri(),
        fetched_at=datetime.now(UTC),
        http_status=None,
        content_hash=content_hash,
        status=FetchStatus.ok,
    )
    session.add(event)
    await session.flush()
    session.add(
        RawPayload(
            fetch_event_id=event.id,
            content_type="application/json",
            body=content,
            size_bytes=len(content),
        )
    )

    inserted = 0
    for committee in committees:
        stmt = (
            pg_insert(Organization)
            .values(
                source=_SOURCE,
                source_id=committee.source_id,
                jurisdiction_id=jurisdiction.id,
                name=committee.name,
                short_name=committee.short_name,
                org_type="other",
                parent_organization_id=anchors.legislature_id,
                acronym=committee.acronym,
                phone=committee.phone,
            )
            .on_conflict_do_nothing(index_elements=["source", "source_id"])
            .returning(Organization.id)
        )
        if (await session.execute(stmt)).scalar_one_or_none() is not None:
            inserted += 1

    logger.info(
        "wsl_committee_seed_ingested",
        extra={"in_seed": len(committees), "inserted": inserted, "seed_path": str(seed_path)},
    )
    return IngestSummary(in_seed=len(committees), inserted=inserted, seed_path=seed_path)


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Ingest the frozen Joint/Other seed (#39).")
    parser.add_argument("--seed-path", type=Path, default=DEFAULT_SEED_PATH)
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session, session.begin():
            summary = await ingest_committee_seed(session, seed_path=args.seed_path)
    except Exception:
        logger.exception("wsl_committee_seed_ingest_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Committee seed ingest: in_seed={summary.in_seed} inserted={summary.inserted} "
        f"(existing left untouched) seed={summary.seed_path}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
