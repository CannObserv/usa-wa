"""One-shot #101 re-source migration — usa_wa_pdc House Position rows → usa_wa_legislature.

The re-partition makes the House Position seat ``usa_wa_legislature``-sourced (symmetric with the
Senate seat), built by the WSL+SOS builder
(:func:`usa_wa_adapter_sos.build_house_spans.build_house_position_spans`). Existing prod rows were
built by the retired PDC House emission and are ``usa_wa_pdc``-sourced. Because the new builder
emits the **identical** 4-part ``source_id`` (``{member}:chamber-house:{ld}-position-{p}:{start}``),
this migration re-homes each such row so the new builder upserts onto it instead of minting a
duplicate that would collide on the #86 one-row-per-PM-anchor index.

Two cases per ``usa_wa_pdc`` 4-part chamber-house row:

1. **No ``usa_wa_legislature`` counterpart** (the expected case, migration run first): flip
   ``source`` **in place** — the row keeps its id, its PM anchor (PM keys assignments on
   ``(person, role, start_date)``, all unchanged by the re-source, so the anchor stays valid), and
   its citations (valid historical provenance; the new SOS builder appends ``sos-whofiled``
   citations on its next run). Counted ``resourced``.
2. **A ``usa_wa_legislature`` row with the same ``source_id`` already exists** (out-of-order — the
   new builder drained first): **collapse** — transfer the PDC row's anchor onto the legislature
   row (index-safe: delete the PDC row + its citations first, freeing the anchor, then assign it),
   counted ``collapsed`` + ``anchors_transferred`` (a keeper already carrying a *different* anchor
   drops the PDC one, ``anchors_dropped`` + warned — the #80 orphaned-upstream case).

A **3-part legacy** PDC House row (``{member}:chamber-house:{biennium}``) is
:mod:`usa_wa_adapter_pdc.migrate_pdc_spans`'s job — run that first; here it is left + counted
``skipped_legacy`` (the stale sweep only maintains 4-part span keys, so flipping a 3-part row to
legislature would strand an immortal open row).

**Owner role.** Deleting citations (case 2) is REVOKEd from the app role (#54), so this runs under
``DATABASE_URL_OWNER`` — like the sponsor/committee/PDC migrations.

**Deploy sequencing.** Run with ``usa-wa-sync-powermap`` paused, in the same window as the WSL +
SOS harvests, **before** the new builder drains any ``usa_wa_legislature`` House row to PM — else
that row mints its own PM assignment and the re-sourced anchor collides / is dropped.

Idempotent; ``--dry-run`` rolls back.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Citation
from clearinghouse_domain_legislative.identity import Assignment

logger = get_logger(__name__)

_PDC_SOURCE = "usa_wa_pdc"
_WSL_SOURCE = "usa_wa_legislature"
_KIND_HOUSE = "chamber-house"
_ASSIGNMENT_CITATION_TYPE = "assignment"


@dataclass(frozen=True)
class MigrationResult:
    """Counts from one re-source pass."""

    pdc_house_found: int
    resourced: int
    collapsed: int
    anchors_transferred: int
    anchors_dropped: int
    skipped_legacy: int


def _is_span_house(source_id: str) -> bool:
    """``{member}:chamber-house:{ld}-position-{p}:{start}`` — 4 parts, the span key."""
    parts = source_id.split(":")
    return len(parts) == 4 and parts[1] == _KIND_HOUSE


def _is_legacy_house(source_id: str) -> bool:
    """``{member}:chamber-house:{biennium}`` — 3 parts, the pre-#79 per-biennium key."""
    parts = source_id.split(":")
    return len(parts) == 3 and parts[1] == _KIND_HOUSE


async def migrate_house_source(session: AsyncSession) -> MigrationResult:
    """Re-source ``usa_wa_pdc`` House Position span rows to ``usa_wa_legislature`` (#101).
    Idempotent — a second run finds no ``usa_wa_pdc`` chamber-house span rows."""
    pdc_rows = (
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
    span_rows = [r for r in pdc_rows if _is_span_house(r.source_id)]
    skipped_legacy = sum(1 for r in pdc_rows if _is_legacy_house(r.source_id))

    resourced = collapsed = transferred = dropped = 0
    for row in span_rows:
        existing = (
            await session.execute(
                select(Assignment).where(
                    Assignment.source == _WSL_SOURCE,
                    Assignment.source_id == row.source_id,
                    Assignment.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            row.source = _WSL_SOURCE  # flip in place — id, anchor, citations ride along
            await session.flush()
            resourced += 1
            continue
        # Collapse onto the existing legislature row: index-safe anchor transfer (#86/#91).
        anchor = row.pm_assignment_id
        await session.execute(
            delete(Citation).where(
                Citation.entity_type == _ASSIGNMENT_CITATION_TYPE, Citation.entity_id == row.id
            )
        )
        await session.delete(row)
        await session.flush()  # free the anchor + remove the row before touching the keeper
        collapsed += 1
        if anchor is None:
            continue
        if existing.pm_assignment_id is None:
            existing.pm_assignment_id = anchor
            transferred += 1
            await session.flush()
        elif existing.pm_assignment_id != anchor:
            logger.warning(
                "house_source_migrate_anchor_dropped",
                extra={
                    "source_id": row.source_id,
                    "orphaned_pm_assignment_id": str(anchor),
                    "keeper_pm_assignment_id": str(existing.pm_assignment_id),
                },
            )
            dropped += 1

    result = MigrationResult(
        pdc_house_found=len(span_rows),
        resourced=resourced,
        collapsed=collapsed,
        anchors_transferred=transferred,
        anchors_dropped=dropped,
        skipped_legacy=skipped_legacy,
    )
    logger.info("house_source_migrate_complete", extra=result.__dict__)
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Re-source usa_wa_pdc House Position rows to usa_wa_legislature (#101)."
    )
    parser.add_argument("--dry-run", action="store_true", help="migrate but roll back (preview)")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL_OWNER")
    if not database_url:
        print(
            "DATABASE_URL_OWNER is not set; aborting — collapsing a colliding row deletes its "
            "citations, which the app role is REVOKEd (#54); run under the owner role.",
            file=sys.stderr,
        )
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await migrate_house_source(session)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("house_source_migrate_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"House source migration: pdc_house_found={result.pdc_house_found} "
        f"resourced={result.resourced} collapsed={result.collapsed} "
        f"anchors_transferred={result.anchors_transferred} "
        f"anchors_dropped={result.anchors_dropped} skipped_legacy={result.skipped_legacy} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
