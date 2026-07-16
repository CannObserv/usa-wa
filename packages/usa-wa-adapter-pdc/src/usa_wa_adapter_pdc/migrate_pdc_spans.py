"""One-shot migration: retire stranded PDC House rows onto their merged spans (#79 + #91).

The span builder emits one row per contiguous House Position tenure, keyed on its **start**
biennium **and** the seat (``{member}:chamber-house:{ld}-position-{p}:{start}`` — 4 parts). Two
kinds of stranded row get retired onto the covering span (same ``(person_id, role_id)`` — the
seat Role is per ``(LD, Position)`` — whose validity window contains the stranded row's
``valid_from``): its PM anchor is transferred, then the row + its citations are hard-deleted.

1. **Legacy per-biennium rows (#79).** The pre-#79 daily path emitted one Assignment per member
   *per biennium* (``{member}:chamber-house:{biennium}`` — 3 parts). Every such row is stranded
   once the backfill emits the 4-part spans.

2. **Superseded 4-part daily-spans (#91).** The #79 daily path itself keys a span on the
   *current* biennium start. When the historical backfill later merges the same tenure into a
   span starting **earlier**, the current-start row (still 4-part, but a different ``source_id``)
   is stranded — the #83 stale-sweep closes it but leaves it anchored. A 4-part row is
   *superseded* iff another 4-part row of the same seat has an **earlier** start whose window
   covers it (a dormancy-gap pair, whose windows are disjoint, is two real tenures — kept).

A legacy row with no covering span yet (spans not built) is left + counted ``orphans_no_span``
(safe — nothing deleted; re-run after ``build_pdc_spans``).

**Index-safe anchor transfer (#91).** The #86 partial unique index forbids two rows sharing one
``pm_assignment_id``. The retire deletes the stranded row **before** moving its anchor to the
keeper, so the index is never transiently violated — the whole migration runs against the live
constraint (the tests carry the index, no drop fixture).

**Owner role.** Deleting citations is REVOKEd from the app role (#54), so this runs under
``DATABASE_URL_OWNER`` — like the sponsor/committee migrations.

**Deploy sequencing.** Run with ``usa-wa-sync-powermap`` paused, in the same window as the
backfill: PM keys assignments on ``(person, role, start_date)``, so a span the sidecar anchors
before this runs gets its *own* PM assignment, after which the stranded anchor can only be dropped
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
    #: Rows retired while their covering span already carried a *different* PM anchor: the
    #: stranded row's PM assignment is orphaned upstream (see the deploy sequencing note).
    #: Combined across the legacy + superseded paths.
    anchors_dropped: int = 0
    #: Superseded 4-part daily-spans (#91) found / retired onto an earlier-start merged span.
    superseded_found: int = 0
    superseded_retired: int = 0


@dataclass
class _Counters:
    """Shared anchor tallies across the legacy + superseded retire paths."""

    transferred: int = 0
    dropped: int = 0


def _is_legacy_house(source_id: str) -> bool:
    """``{member}:chamber-house:{biennium}`` — 3 parts, the pre-#79 per-biennium key."""
    parts = source_id.split(":")
    return len(parts) == 3 and parts[1] == _KIND_HOUSE


def _is_span_house(source_id: str) -> bool:
    """``{member}:chamber-house:{ld}-position-{p}:{start}`` — 4 parts, the #79 span key."""
    parts = source_id.split(":")
    return len(parts) == 4 and parts[1] == _KIND_HOUSE


def _covering_span(spans: Sequence[Assignment], stranded_valid_from: date) -> Assignment | None:
    """The span whose validity window contains a stranded row's start
    (``Assignment.valid_from`` is non-nullable, so the stranded start is always present)."""
    for span in spans:
        upper = span.valid_to or date.max
        if span.valid_from <= stranded_valid_from <= upper:
            return span
    return None


def _superseded_pairs(
    spans_by_key: dict[tuple, list[Assignment]],
) -> list[tuple[Assignment, Assignment]]:
    """``(superseded_row, keeper)`` for every 4-part span covered by an **earlier-start** span
    of the same seat (#91). A dormancy-gap pair — disjoint windows, neither covering the other
    — yields nothing (two real tenures). The earliest-start row is never superseded."""
    pairs: list[tuple[Assignment, Assignment]] = []
    for rows in spans_by_key.values():
        if len(rows) < 2:
            continue
        by_start = sorted(rows, key=lambda r: r.valid_from)
        for i, row in enumerate(by_start):
            keeper = _covering_span(by_start[:i], row.valid_from)  # strictly-earlier starts only
            if keeper is not None:
                pairs.append((row, keeper))
    return pairs


async def _retire_onto(
    session: AsyncSession, row: Assignment, keeper: Assignment, counters: _Counters
) -> None:
    """Delete a stranded row + its citations, then move its PM anchor to the ``keeper`` span.

    **Index-safe (#91):** the row is deleted (freeing its anchor) *before* the anchor is
    assigned to the keeper, so the #86 one-row-per-PM-anchor partial unique index is never
    transiently violated. A keeper that already carries a *different* anchor can't adopt this
    one — it's dropped + warned (the orphaned-upstream case)."""
    anchor = row.pm_assignment_id
    await session.execute(
        delete(Citation).where(
            Citation.entity_type == _ASSIGNMENT_CITATION_TYPE, Citation.entity_id == row.id
        )
    )
    await session.delete(row)
    await session.flush()  # free the anchor + remove the row before touching the keeper
    if anchor is None:
        return
    if keeper.pm_assignment_id is None:
        keeper.pm_assignment_id = anchor
        counters.transferred += 1
        await session.flush()
    elif keeper.pm_assignment_id != anchor:
        logger.warning(
            "pdc_span_migrate_anchor_dropped",
            extra={
                "source_id": row.source_id,
                "orphaned_pm_assignment_id": str(anchor),
                "keeper_pm_assignment_id": str(keeper.pm_assignment_id),
            },
        )
        counters.dropped += 1


async def migrate_pdc_spans(session: AsyncSession) -> MigrationResult:
    """Retire any legacy per-biennium (#79) or superseded 4-part (#91) PDC House row onto the
    merged span that covers it, transferring the PM anchor. Idempotent."""
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

    superseded = _superseded_pairs(spans_by_key)
    superseded_ids = {row.id for row, _ in superseded}
    # A legacy row's covering span must be a keeper (not itself a superseded row about to go).
    keepers_by_key: dict[tuple, list[Assignment]] = defaultdict(list)
    for key, rows in spans_by_key.items():
        keepers_by_key[key] = [r for r in rows if r.id not in superseded_ids]

    counters = _Counters()

    legacy_retired = orphans = 0
    for row in legacy:
        candidates = keepers_by_key.get((row.person_id, row.role_id), ())
        keeper = _covering_span(candidates, row.valid_from)
        if keeper is None:
            logger.warning(
                "pdc_span_migrate_no_successor",
                extra={"source_id": row.source_id, "person_id": str(row.person_id)},
            )
            orphans += 1
            continue
        await _retire_onto(session, row, keeper, counters)
        legacy_retired += 1

    superseded_retired = 0
    for row, keeper in superseded:
        await _retire_onto(session, row, keeper, counters)
        superseded_retired += 1

    result = MigrationResult(
        legacy_found=len(legacy),
        anchors_transferred=counters.transferred,
        legacy_retired=legacy_retired,
        orphans_no_span=orphans,
        anchors_dropped=counters.dropped,
        superseded_found=len(superseded),
        superseded_retired=superseded_retired,
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
        f"legacy_retired={result.legacy_retired} "
        f"superseded_found={result.superseded_found} "
        f"superseded_retired={result.superseded_retired} "
        f"anchors_transferred={result.anchors_transferred} "
        f"anchors_dropped={result.anchors_dropped} orphans_no_span={result.orphans_no_span} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
