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

    python -m usa_wa_adapter_legislature.committees.ingest_seed [--seed-path PATH]
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.job import JobContext, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from clearinghouse_core.seed_manifest import verified_digest
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.committees.seed import (
    DEFAULT_SEED_PATH,
    SeedCommittee,
    deserialize_seed,
)
from usa_wa_adapter_legislature.provisioning import get_or_create_source
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
#: Stable provenance handle for a seed ingest (distinct from live fetch resource ids).
SEED_RESOURCE_ID = "committee-seed:joint-other"

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "wsl-committee-seed-ingest"


@dataclass(frozen=True)
class _LoadedSeed:
    """The seed as read from disk: resolved path, bytes, verified digest, rows."""

    path: Path
    content: bytes
    content_hash: bytes
    committees: list[SeedCommittee]


def _read_seed(seed_path: Path) -> _LoadedSeed:
    """Resolve, read, verify and parse the seed — the whole blocking half, in one place.

    Synchronous on purpose: :func:`_load_seed` hands it to a worker thread. Grouping the
    four steps means one hop rather than four, and it sweeps in the sidecar read hiding
    inside ``verified_digest`` — blocking too, but a call into another module and so
    invisible to ruff's ``ASYNC`` rules.
    """
    resolved = seed_path.resolve()
    content = resolved.read_bytes()
    return _LoadedSeed(
        path=resolved,
        content=content,
        # raises SeedIntegrityError on mismatch
        content_hash=verified_digest(resolved, content),
        committees=deserialize_seed(content),
    )


async def _load_seed(seed_path: Path) -> _LoadedSeed:
    """Load the seed without blocking the event loop (#196)."""
    return await asyncio.to_thread(_read_seed, seed_path)


@dataclass(frozen=True)
class IngestSummary:
    """Outcome of one :func:`ingest_seed` run."""

    in_seed: int
    inserted: int
    seed_path: Path
    provenance_recorded: bool


async def _seed_already_recorded(
    session: AsyncSession, source_id: _ULID, content_hash: bytes
) -> bool:
    """True if a prior ingest already archived this exact seed (same bytes).

    Seed ingest is append-only provenance with no cache TTL, so re-ingesting an
    unchanged seed would duplicate the FetchEvent + RawPayload. Skip the provenance
    write when this content_hash is already on record for the seed resource (the
    fill-only org upsert still runs — it is idempotent)."""
    stmt = (
        select(FetchEvent.id)
        .where(
            FetchEvent.source_id == source_id,
            FetchEvent.resource_id == SEED_RESOURCE_ID,
            FetchEvent.content_hash == content_hash,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def ingest_seed(
    session: AsyncSession,
    *,
    seed_path: Path = DEFAULT_SEED_PATH,
) -> IngestSummary:
    """Verify + load the seed; fill-only upsert the Joint/`Other` cohort."""
    loaded = await _load_seed(seed_path)
    seed_path = loaded.path
    content = loaded.content
    content_hash = loaded.content_hash
    committees = loaded.committees

    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    anchors = await bootstrap_synthetic_anchors(
        session,
        biennium=biennium_for_date(datetime.now(UTC).date()),
        jurisdiction_id=jurisdiction.id,
    )

    # Synthetic provenance: the seed is a fetch-equivalent, hashed under the same
    # baseline as live SOAP (#54); its bytes are the archived RawPayload. Skip the
    # write when this exact seed was already ingested (append-only dedup).
    provenance_recorded = not await _seed_already_recorded(session, source.id, content_hash)
    if provenance_recorded:
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
        extra={
            "in_seed": len(committees),
            "inserted": inserted,
            "provenance_recorded": provenance_recorded,
            "seed_path": str(seed_path),
        },
    )
    return IngestSummary(
        in_seed=len(committees),
        inserted=inserted,
        seed_path=seed_path,
        provenance_recorded=provenance_recorded,
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the ingest's own flag to the harness's shared parser."""
    parser.add_argument("--seed-path", type=Path, default=DEFAULT_SEED_PATH)


async def _ingest_job(ctx: JobContext) -> IngestSummary:
    """Harness handler; the harness owns the commit (and the ``--dry-run`` rollback)."""
    return await ingest_seed(ctx.require_session(), seed_path=ctx.args.seed_path)


def main(argv: list[str] | None = None) -> int:
    """Ingest the frozen seed. Exit ``0`` clean · ``1`` failed · ``2`` config.

    ``--dry-run`` is new (#179b gives every job one) and rolls the ingest back; the
    ingest never had one before, and the explicit ``session.begin()`` it used committed
    unconditionally.
    """
    return run_job(
        JOB_SLUG,
        _ingest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.committees.ingest_seed",
        description="Ingest the frozen Joint/Other seed (#39).",
        extra_args=_add_args,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
