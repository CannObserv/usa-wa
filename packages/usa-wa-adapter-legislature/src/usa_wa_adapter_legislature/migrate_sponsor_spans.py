"""One-shot migration: collapse pre-#78 per-biennium sponsor Assignments into spans (#78-3).

Before #78 the daily refresh emitted one Assignment per member **per biennium** per
dimension (``{member_id}:party:{biennium}`` / ``{member_id}:chamber-senate:{biennium}``).
Increment 2c switched the daily path to persons-only + an archive-derived span builder, so
those legacy rows are no longer re-emitted — but the ones already shipped to prod persist,
each carrying a ``pm_assignment_id`` (its PM anchor). This migration retires them onto the
merged span that supersedes them, so the local cache stops carrying **two** rows anchored to
the same PM assignment (which would break the assignment descriptor's ``local_match``
``scalar_one_or_none``).

**Why match on ``(person_id, role_id)``.** PM identifies an assignment **structurally** by
``(person, role)`` (the assignment descriptor's observation carries no source_id); a span
shares the *same* person + role as the legacy per-biennium rows it collapses, so PM already
folds them onto one assignment. The migration mirrors that: the successor span is the live
Assignment with the same ``(person_id, role_id)`` and a **span-shaped** source_id (4 colon
parts), distinct from the 3-part legacy key. It transfers the legacy anchor to that span
(if the span lacks one) and hard-deletes the legacy row + its citations.

**Scope — party + Senate seat only.** The span builder emits only ``party`` +
``chamber-senate`` observations, so only those legacy dims are superseded. ``chamber-house``
(PDC/#69) and ``committee`` (#82) per-biennium rows are **left untouched** — their span
migrations belong to those issues. A legacy row with no successor span (e.g. an unsynced-LD
Senate seat that never produced a span) is **left in place and logged**, never orphaned.

Idempotent: re-running finds no legacy rows (they were retired) and re-asserts the spans.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Citation
from clearinghouse_domain_legislative.identity import Assignment
from usa_wa_adapter_legislature.harvest_sponsor_spans import build_sponsor_spans
from usa_wa_adapter_legislature.synthesis import biennium_for_date

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
_ASSIGNMENT_CITATION_TYPE = "assignment"
#: Legacy per-biennium dims the sponsor span builder supersedes (party + Senate seat).
#: NOT ``chamber-house`` (PDC/#69) or ``committee`` (#82) — those keep per-biennium rows.
_LEGACY_DIMS = ("party", "chamber-senate")
_BIENNIUM_RE = re.compile(r"^\d{4}-\d{2}$")


@dataclass(frozen=True)
class MigrationResult:
    """Counts from one migration pass."""

    spans_built: int
    legacy_found: int
    anchors_transferred: int
    legacy_retired: int
    orphans_no_span: int


def _is_legacy_sponsor_source_id(source_id: str) -> bool:
    """A pre-#78 per-biennium party/Senate assignment key: ``{member}:{dim}:{YYYY-YY}``
    (exactly 3 colon parts, dim ∈ party/chamber-senate). Excludes span keys (4 parts) and
    chamber-house/committee dims."""
    parts = source_id.split(":")
    return len(parts) == 3 and parts[1] in _LEGACY_DIMS and _BIENNIUM_RE.match(parts[2]) is not None


def _is_span_source_id(source_id: str) -> bool:
    """A #78 merged-span key: ``{member}:{kind}:{discriminator}:{start_biennium}`` (4 parts,
    kind ∈ party/chamber-senate) — the shape the successor lookup keys on."""
    parts = source_id.split(":")
    return len(parts) == 4 and parts[1] in _LEGACY_DIMS and _BIENNIUM_RE.match(parts[3]) is not None


async def migrate_sponsor_spans(
    session: AsyncSession,
    *,
    current_biennium: str | None = None,
    sponsor_client=None,
) -> MigrationResult:
    """Build the spans, then collapse each legacy per-biennium party/Senate row onto its
    successor span (transfer the PM anchor + retire the legacy row). Returns the counts."""
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())
    spans_built = await build_sponsor_spans(
        session, sponsor_client=sponsor_client, current_biennium=current
    )

    live = (
        (
            await session.execute(
                select(Assignment).where(
                    Assignment.source == _SOURCE, Assignment.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    # Index the span rows by their structural key (PM's own assignment identity).
    span_by_key: dict[tuple, Assignment] = {
        (a.person_id, a.role_id): a for a in live if _is_span_source_id(a.source_id)
    }
    legacy = [a for a in live if _is_legacy_sponsor_source_id(a.source_id)]

    transferred = retired = orphans = 0
    for row in legacy:
        span = span_by_key.get((row.person_id, row.role_id))
        if span is None:
            logger.warning(
                "sponsor_span_migrate_no_successor",
                extra={"source_id": row.source_id, "person_id": str(row.person_id)},
            )
            orphans += 1
            continue
        if span.pm_assignment_id is None and row.pm_assignment_id is not None:
            # Move the PM anchor onto the span so it (not the retired legacy row) is the
            # single local representative of that PM assignment.
            span.pm_assignment_id = row.pm_assignment_id
            transferred += 1
        await session.execute(
            delete(Citation).where(
                Citation.entity_type == _ASSIGNMENT_CITATION_TYPE, Citation.entity_id == row.id
            )
        )
        await session.delete(row)
        retired += 1
    await session.flush()

    result = MigrationResult(
        spans_built=spans_built,
        legacy_found=len(legacy),
        anchors_transferred=transferred,
        legacy_retired=retired,
        orphans_no_span=orphans,
    )
    logger.info("sponsor_span_migrate_complete", extra=result.__dict__)
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Collapse pre-#78 per-biennium sponsor Assignments into merged spans (#78-3)."
    )
    parser.add_argument("--dry-run", action="store_true", help="migrate but roll back (preview)")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await migrate_sponsor_spans(session)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("sponsor_span_migrate_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Sponsor span migration: legacy_found={result.legacy_found} "
        f"anchors_transferred={result.anchors_transferred} retired={result.legacy_retired} "
        f"orphans_no_span={result.orphans_no_span} spans_built={result.spans_built} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
