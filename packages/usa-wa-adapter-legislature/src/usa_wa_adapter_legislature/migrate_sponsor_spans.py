"""One-shot migration: collapse stranded per-biennium/shallow sponsor Assignments into spans.

Before #78 the daily refresh emitted one Assignment per member **per biennium** per
dimension (``{member_id}:party:{biennium}`` / ``{member_id}:chamber-senate:{biennium}``).
Increment 2c switched the daily path to persons-only + an archive-derived span builder, so
those legacy rows are no longer re-emitted — but rows already shipped to prod persist, each
carrying a ``pm_assignment_id`` (its PM anchor). This migration retires the **stranded** ones
onto the merged span that supersedes them, so the local cache stops carrying **two** rows
anchored to the same PM assignment (which would break the assignment descriptor's
``local_match`` ``scalar_one_or_none`` and the #86 one-row-per-PM-anchor unique index).

Two stranded shapes get retired onto their covering span (same ``(person_id, role_id)`` — the
party/Senate seat Role — whose validity window contains the stranded row's ``valid_from``):

1. **Legacy per-biennium rows (#78-3).** ``{member}:{dim}:{YYYY-YY}`` — 3 parts, the pre-#78
   daily key. Superseded once the span builder emits the 4-part span.

2. **Superseded 4-part shallow spans (#97).** The 2c daily path builds a span keyed on the
   *current* biennium start (``{member}:{dim}:{disc}:2025-26`` — already 4-part). When the
   full-archive backfill (``harvest_sponsor_spans`` at natural depth) later merges the same
   tenure into a span starting **earlier**, the current-start row (still 4-part, a different
   ``source_id``) is stranded — the #83 stale-sweep closes it but leaves it anchored. This is
   the **same** case #91 fixed for PDC House and #95 for committee memberships; the original
   #78-3 migration only handled shape 1, so on the 2c deploy every current row was a 4-part
   ``orphans_no_span`` left uncollapsed — this closes that gap. A 4-part row is *superseded*
   iff another 4-part row of the same seat has an **earlier** start whose window covers it (a
   dormancy-gap pair — disjoint windows — is two real tenures, kept).

**Why retire the stranded row.** PM identifies an assignment by ``(person, role, start_date)``
with NULLS NOT DISTINCT (power-map#177/#289) — the descriptor's observation carries no
source_id. The successor span is the live Assignment with the same ``(person_id, role_id)`` and
a validity window covering the stranded row's ``valid_from``. The window check disambiguates a
member with non-contiguous tenure in one role (a dormancy gap yields two spans under the same
``(person, role)``, each a distinct PM assignment via its own ``start_date``).

**Scope — party + Senate seat only.** The span builder emits only ``party`` + ``chamber-senate``
observations. ``chamber-house`` (PDC/#79) and ``committee`` (#82) rows are **left untouched** —
their span migrations belong to those issues. A stranded row with no covering span (e.g. an
unsynced-LD Senate seat that never produced a span) is **left in place and logged**, never
orphaned.

Idempotent: re-running finds no stranded rows (they were retired) and re-asserts the spans.

**Index-safe anchor transfer (#97, mirroring #91/#95).** The #86 partial unique index forbids
two rows sharing one ``pm_assignment_id``. :func:`_retire_onto` deletes the stranded row
(freeing its anchor) + flushes **before** moving the anchor to the keeper, so the index is
never transiently violated — the migration runs against the live constraint.

**Owner role.** Retiring a row hard-deletes its ``citations``, which the app role is REVOKEd
(#54 provenance immutability), so the CLI runs under ``DATABASE_URL_OWNER`` (like
``baseline_unbaselined_committees``). The daily span re-drive stays app-role-safe because
:func:`~usa_wa_adapter_legislature.span_emit._ensure_citations` is insert-only.

**Deploy sequencing.** Run this in the *same* maintenance window as the backfill, with the
sync sidecar paused. PM keys assignments on ``(person, role, start_date)``, so a deepened span
the sidecar anchors before this runs gets its *own* PM assignment, after which the stranded
anchor can only be dropped (counted ``anchors_dropped`` + warned) — orphaning that PM row (the
#80 start-date gap).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

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
#: NOT ``chamber-house`` (PDC/#79) or ``committee`` (#82) — those keep per-biennium rows.
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
    #: Rows retired while their covering span already carried a *different* PM anchor: the
    #: stranded row's PM assignment is orphaned upstream (see the deploy sequencing note).
    #: Combined across the legacy + superseded paths. Expected 0 when harvest+migrate run
    #: before the sidecar drains.
    anchors_dropped: int = 0
    #: Superseded 4-part shallow spans (#97) found / retired onto an earlier-start merged span.
    superseded_found: int = 0
    superseded_retired: int = 0


@dataclass
class _Counters:
    """Shared anchor tallies across the legacy + superseded retire paths."""

    transferred: int = 0
    dropped: int = 0


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


def _covering_span(
    spans: Sequence[Assignment], stranded_valid_from: date | None
) -> Assignment | None:
    """The span (among candidates sharing the stranded row's person+role) whose validity
    window contains the stranded biennium's start — so a stranded row collapses into the
    tenure it actually belonged to, not a different (e.g. closed) run of the same role.
    ``None`` if the stranded row falls in a gap between spans (a genuine orphan)."""
    if stranded_valid_from is None:
        return None
    for span in spans:
        upper = span.valid_to or date.max
        if span.valid_from <= stranded_valid_from <= upper:
            return span
    return None


def _superseded_pairs(
    spans_by_key: dict[tuple, list[Assignment]],
) -> list[tuple[Assignment, Assignment]]:
    """``(superseded_row, keeper)`` for every 4-part span covered by an **earlier-start** span
    of the same seat (#97). A dormancy-gap pair — disjoint windows, neither covering the other
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

    **Index-safe (#97, mirroring #91/#95):** the row is deleted (freeing its anchor) *before*
    the anchor is assigned to the keeper, so the #86 one-row-per-PM-anchor partial unique index
    is never transiently violated. A keeper that already carries a *different* anchor can't
    adopt this one — it's dropped + warned (the orphaned-upstream case)."""
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
            "sponsor_span_migrate_anchor_dropped",
            extra={
                "source_id": row.source_id,
                "orphaned_pm_assignment_id": str(anchor),
                "keeper_pm_assignment_id": str(keeper.pm_assignment_id),
            },
        )
        counters.dropped += 1


async def migrate_sponsor_spans(
    session: AsyncSession,
    *,
    current_biennium: str | None = None,
    sponsor_client=None,
) -> MigrationResult:
    """Build the spans, then collapse each stranded legacy per-biennium (#78-3) or superseded
    4-part shallow (#97) party/Senate row onto its successor span (transfer the PM anchor +
    retire the stranded row, index-safe). Idempotent."""
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())
    spans_built = (
        await build_sponsor_spans(session, sponsor_client=sponsor_client, current_biennium=current)
    ).emitted

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
    # Index the span rows by structural key (PM's assignment identity). A member with
    # non-contiguous tenure in the SAME role (a dormancy gap → e.g. two Senate spans on one
    # LD seat, or two party spans after a party round-trip) has multiple spans under one
    # (person, role), so a stranded row must map to the span whose validity window covers ITS
    # biennium — not an arbitrary one.
    spans_by_key: dict[tuple, list[Assignment]] = defaultdict(list)
    for a in live:
        if _is_span_source_id(a.source_id):
            spans_by_key[(a.person_id, a.role_id)].append(a)
    legacy = [a for a in live if _is_legacy_sponsor_source_id(a.source_id)]

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
                "sponsor_span_migrate_no_successor",
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
        spans_built=spans_built,
        legacy_found=len(legacy),
        anchors_transferred=counters.transferred,
        legacy_retired=legacy_retired,
        orphans_no_span=orphans,
        anchors_dropped=counters.dropped,
        superseded_found=len(superseded),
        superseded_retired=superseded_retired,
    )
    logger.info("sponsor_span_migrate_complete", extra=result.__dict__)
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Collapse stranded per-biennium/shallow sponsor Assignments into spans (#97)."
    )
    parser.add_argument("--dry-run", action="store_true", help="migrate but roll back (preview)")
    args = parser.parse_args(argv)

    # Owner role: retiring a legacy row hard-deletes its citations, and the app role is
    # REVOKEd DELETE on the provenance ledger (#54). Like baseline_unbaselined_committees.
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
        f"legacy_retired={result.legacy_retired} "
        f"superseded_found={result.superseded_found} "
        f"superseded_retired={result.superseded_retired} "
        f"anchors_transferred={result.anchors_transferred} "
        f"anchors_dropped={result.anchors_dropped} orphans_no_span={result.orphans_no_span} "
        f"spans_built={result.spans_built} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
