"""One-shot migration: collapse per-biennium PDC House seats onto spans (#79).

Before #79 the daily PDC path emitted one House Assignment per member **per biennium**
(``{member}:chamber-house:{biennium}``, ``source=usa_wa_pdc``). The span builder now emits one row
per contiguous House Position tenure, keyed on its **start** biennium **and** the seat
(``{member}:chamber-house:{ld}-position-{p}:{start}`` — 4 parts vs the legacy 3). The shapes never
collide, so every shipped per-biennium row is stranded once the backfill emits the spans.

This is a **retirement pass** over existing rows (deploy order: ``harvest_pdc`` →
``build_pdc_spans`` → this): each legacy row is mapped to the span covering its biennium (same
``(person_id, role_id)`` — the seat Role is per ``(LD, Position)`` — with a validity window
containing the legacy row's ``valid_from``), its PM anchor transferred, then the row + its
citations hard-deleted. A legacy row with no covering span yet (spans not built) is left +
counted ``orphans_no_span`` (safe — nothing deleted; re-run after ``build_pdc_spans``).

**Owner role.** Deleting citations is REVOKEd from the app role (#54), so this runs under
``DATABASE_URL_OWNER`` — like the sponsor/committee migrations.

**Deploy sequencing.** Run with ``usa-wa-sync-powermap`` paused, in the same window as the
backfill: PM keys assignments on ``(person, role, start_date)``, so a span the sidecar anchors
before this runs gets its *own* PM assignment, after which the legacy anchor can only be dropped
(counted ``anchors_dropped`` + warned) — orphaning that PM row (the #80 start-date gap).

Idempotent; ``--dry-run`` rolls back.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Citation
from clearinghouse_domain_legislative.identity import Assignment

logger = get_logger(__name__)

_PDC_SOURCE = "usa_wa_pdc"
_KIND_HOUSE = "chamber-house"
_ASSIGNMENT_CITATION_TYPE = "assignment"


@dataclass(frozen=True)
class MigrationResult:
    """Counts from one migration pass."""

    legacy_found: int
    anchors_transferred: int
    legacy_retired: int
    orphans_no_span: int
    #: Legacy rows retired while their covering span already carried a *different* PM anchor:
    #: the legacy row's PM assignment is orphaned upstream (see the deploy sequencing note).
    anchors_dropped: int = 0


def _is_legacy_house(source_id: str) -> bool:
    """``{member}:chamber-house:{biennium}`` — 3 parts, the pre-#79 per-biennium key."""
    parts = source_id.split(":")
    return len(parts) == 3 and parts[1] == _KIND_HOUSE


def _is_span_house(source_id: str) -> bool:
    """``{member}:chamber-house:{ld}-position-{p}:{start}`` — 4 parts, the #79 span key."""
    parts = source_id.split(":")
    return len(parts) == 4 and parts[1] == _KIND_HOUSE


def _covering_span(
    spans: Sequence[Assignment], legacy_valid_from: date | None
) -> Assignment | None:
    """The span whose validity window contains the legacy row's biennium start."""
    if legacy_valid_from is None:
        return None
    for span in spans:
        upper = span.valid_to or date.max
        if span.valid_from <= legacy_valid_from <= upper:
            return span
    return None


async def migrate_pdc_spans(session: AsyncSession) -> MigrationResult:
    """Retire any per-biennium PDC House row stranded by the merged spans."""
    live = (
        (
            await session.execute(
                select(Assignment).where(
                    Assignment.source == _PDC_SOURCE, Assignment.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    spans_by_key: dict[tuple, list[Assignment]] = defaultdict(list)
    for row in live:
        if _is_span_house(row.source_id):
            spans_by_key[(row.person_id, row.role_id)].append(row)
    legacy = [row for row in live if _is_legacy_house(row.source_id)]

    transferred = retired = orphans = dropped = 0
    for row in legacy:
        span = _covering_span(spans_by_key.get((row.person_id, row.role_id), ()), row.valid_from)
        if span is None:
            logger.warning(
                "pdc_span_migrate_no_successor",
                extra={"source_id": row.source_id, "person_id": str(row.person_id)},
            )
            orphans += 1
            continue
        if row.pm_assignment_id is not None:
            if span.pm_assignment_id is None:
                span.pm_assignment_id = row.pm_assignment_id
                transferred += 1
            elif span.pm_assignment_id != row.pm_assignment_id:
                logger.warning(
                    "pdc_span_migrate_anchor_dropped",
                    extra={
                        "source_id": row.source_id,
                        "orphaned_pm_assignment_id": str(row.pm_assignment_id),
                        "span_pm_assignment_id": str(span.pm_assignment_id),
                    },
                )
                dropped += 1
        await session.execute(
            delete(Citation).where(
                Citation.entity_type == _ASSIGNMENT_CITATION_TYPE, Citation.entity_id == row.id
            )
        )
        await session.delete(row)
        retired += 1
    await session.flush()

    result = MigrationResult(
        legacy_found=len(legacy),
        anchors_transferred=transferred,
        legacy_retired=retired,
        orphans_no_span=orphans,
        anchors_dropped=dropped,
    )
    logger.info("pdc_span_migrate_complete", extra=result.__dict__)
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Collapse per-biennium PDC House seats onto merged spans (#79)."
    )
    parser.add_argument("--dry-run", action="store_true", help="migrate but roll back (preview)")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL_OWNER")
    if not database_url:
        print(
            "DATABASE_URL_OWNER is not set; aborting — retiring legacy rows deletes their "
            "citations, which the app role is REVOKEd (#54); run under the owner role.",
            file=sys.stderr,
        )
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await migrate_pdc_spans(session)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("pdc_span_migrate_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"PDC span migration: legacy_found={result.legacy_found} "
        f"anchors_transferred={result.anchors_transferred} retired={result.legacy_retired} "
        f"anchors_dropped={result.anchors_dropped} orphans_no_span={result.orphans_no_span} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
