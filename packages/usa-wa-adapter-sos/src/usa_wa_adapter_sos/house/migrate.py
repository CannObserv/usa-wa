"""One-shot #101 re-source migration — retire usa_wa_pdc House rows onto usa_wa_legislature spans.

The re-partition makes the House Position seat ``usa_wa_legislature``-sourced (symmetric with the
Senate seat), built by the WSL+SOS builder
(:func:`usa_wa_adapter_sos.house.build.build_house_position_spans`). Existing prod rows were
built by the retired PDC House emission and are ``usa_wa_pdc``-sourced; this migration retires each
onto the ``usa_wa_legislature`` span that covers it, transferring the PM anchor — so the local
cache holds ONE row per PM assignment and the anchor stays valid (PM keys assignments on
``(person, role, start_date)``; the covering span **is** that tenure).

**Why a covering-window collapse, not an exact-``source_id`` re-point.** The span ``source_id`` is
``{member}:chamber-house:{ld}-position-{p}:{start}`` — and the ``{start}`` **diverges** for the
central #101 cohort. PDC's dataset omits the House ``position`` before the 2018 election, so a
member serving **across the 2018 boundary** has a **shallow** existing PDC span starting at their
first PDC-positioned biennium (``…:2019-20``), while the SOS builder (positions back to 2008) emits
a **deeper** span (``…:2017-18``) — a different ``source_id``. A naive in-place ``source`` flip
would leave the shallow row stranded: the deep span supersedes it, the stale sweep closes it (still
holding its PM anchor), and the sidecar then mints a **second** PM assignment for the deep span
(different ``start_date``) — a duplicate the #86 unique index can't catch (the two local rows carry
different anchors). So each PDC row is mapped to the ``usa_wa_legislature`` span whose validity
window contains its ``valid_from`` — the same ``_covering_span``/``_retire_onto`` pattern as
:mod:`usa_wa_adapter_pdc.migrate_pdc_spans` (#91/#97).

**Run order: build BEFORE migrate.** ``build_house_spans`` must run first (full historical, so the
deep ``usa_wa_legislature`` keeper spans exist), then this migration collapses the stranded PDC
rows onto them. A PDC row with **no** covering keeper (the SOS match couldn't position that member,
so the builder emitted no seat) is left in place + counted ``orphans_no_keeper`` (a valid frozen
historical PDC seat; deleting it would orphan its PM assignment).

**Index-safe anchor transfer (#91).** ``_retire_onto`` deletes the stranded row + its citations
(freeing its anchor) **before** assigning the anchor to the keeper, so the #86 one-row-per-PM-anchor
partial unique index is never transiently violated. A keeper already carrying a *different* anchor
drops the stranded one (``anchors_dropped`` + warned — the #80 orphaned-upstream case).

**Within-source superseded pass (#103), run BEFORE the PDC pass.** The elimination inference
deepens some members' tenures (an inferred earlier biennium merges in), so an existing anchored
``usa_wa_legislature`` row can be superseded by a new deeper-start row of the same seat — the
same shape #97 fixed for sponsor spans. Each superseded row collapses onto its earlier-start
covering keeper (``_superseded_pairs``); disjoint tenures (served → left → returned) are two real
runs and stay. It runs first so the PDC pass maps onto **surviving** keepers only — never onto a
row about to be deleted. A keeper that merged in place (the member's earliest-start row extended)
already carries its own anchor, so the superseded row's anchor is dropped + warned (one PM
assignment orphaned upstream, the #80 class).

A **3-part legacy** PDC House row (``{member}:chamber-house:{biennium}``) is
:mod:`usa_wa_adapter_pdc.migrate_pdc_spans`'s job — run that first; here it is left + counted
``skipped_legacy``.

**Owner role.** Deleting citations is REVOKEd from the app role (#54), so this runs under
``DATABASE_URL_OWNER``. **Sidecar paused, same window as the harvest + build**, before the new
builder's spans drain to PM. Idempotent; ``--dry-run`` rolls back.
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
_WSL_SOURCE = "usa_wa_legislature"
_KIND_HOUSE = "chamber-house"
_ASSIGNMENT_CITATION_TYPE = "assignment"


@dataclass(frozen=True)
class MigrationResult:
    """Counts from one re-source pass. ``superseded_retired`` is the #103 within-source pass."""

    pdc_house_found: int
    retired: int
    superseded_retired: int
    anchors_transferred: int
    anchors_dropped: int
    orphans_no_keeper: int
    skipped_legacy: int


@dataclass
class _Counters:
    transferred: int = 0
    dropped: int = 0


def _is_span_house(source_id: str) -> bool:
    """``{member}:chamber-house:{ld}-position-{p}:{start}`` — 4 parts, the span key."""
    parts = source_id.split(":")
    return len(parts) == 4 and parts[1] == _KIND_HOUSE


def _is_legacy_house(source_id: str) -> bool:
    """``{member}:chamber-house:{biennium}`` — 3 parts, the pre-#79 per-biennium key."""
    parts = source_id.split(":")
    return len(parts) == 3 and parts[1] == _KIND_HOUSE


def _covering_span(spans: Sequence[Assignment], stranded_valid_from: date) -> Assignment | None:
    """The ``usa_wa_legislature`` keeper whose validity window contains a stranded row's
    start (``Assignment.valid_from`` is non-nullable, so the start is always present)."""
    for span in spans:
        upper = span.valid_to or date.max
        if span.valid_from <= stranded_valid_from <= upper:
            return span
    return None


def _superseded_pairs(
    seats: dict[tuple, list[Assignment]],
) -> list[tuple[Assignment, Assignment]]:
    """``(superseded_row, keeper)`` for every ``usa_wa_legislature`` House span covered by an
    **earlier-start** span of the same ``(person, role)`` seat (#103 — elimination deepens
    tenures, stranding the shallower-start rows; the #97 within-source pattern). A disjoint pair
    — neither window covering the other's start — yields nothing (two real tenures). The
    earliest-start row is never superseded."""
    pairs: list[tuple[Assignment, Assignment]] = []
    for rows in seats.values():
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
    """Delete a stranded PDC row + its citations, then move its PM anchor to the ``keeper`` span
    (index-safe #91: delete frees the anchor before the keeper adopts it). A keeper already
    carrying a *different* anchor can't adopt this one — it's dropped + warned."""
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
            "house_source_migrate_anchor_dropped",
            extra={
                "source_id": row.source_id,
                "orphaned_pm_assignment_id": str(anchor),
                "keeper_pm_assignment_id": str(keeper.pm_assignment_id),
            },
        )
        counters.dropped += 1


async def migrate_house_source(session: AsyncSession) -> MigrationResult:
    """Retire ``usa_wa_pdc`` House Position span rows onto their covering ``usa_wa_legislature``
    span, transferring the PM anchor (#101), after first collapsing **within-source superseded**
    ``usa_wa_legislature`` rows onto their deeper keepers (#103). Run **after**
    ``build_house_spans``. Idempotent — a second run retires nothing new (``retired=0``,
    ``superseded_retired=0``); any residual ``pdc_house_found`` is the ``orphans_no_keeper`` set
    (PDC rows with no covering keeper), re-left untouched."""
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

    wsl_rows = [
        r
        for r in (
            (
                await session.execute(
                    select(Assignment).where(
                        Assignment.source == _WSL_SOURCE, Assignment.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        if _is_span_house(r.source_id)
    ]
    by_seat: dict[tuple, list[Assignment]] = defaultdict(list)
    for k in wsl_rows:
        by_seat[(k.person_id, k.role_id)].append(k)

    # #103 pass 1 — within-source superseded collapse, BEFORE the PDC pass so PDC rows map onto
    # surviving keepers only.
    counters = _Counters()
    superseded_retired = 0
    retired_ids: set = set()
    for row, keeper in _superseded_pairs(by_seat):
        if keeper.id in retired_ids:
            # A >2-row chain retired this keeper first; the deferred row collapses onto the
            # surviving root on the next (idempotent) run.
            logger.warning("house_superseded_chain_deferred", extra={"source_id": row.source_id})
            continue
        await _retire_onto(session, row, keeper, counters)
        retired_ids.add(row.id)
        superseded_retired += 1

    keepers_by_seat: dict[tuple, list[Assignment]] = defaultdict(list)
    for k in wsl_rows:
        if k.id not in retired_ids:
            keepers_by_seat[(k.person_id, k.role_id)].append(k)

    # Pass 2 — the #101 PDC re-source collapse.
    retired = orphans = 0
    for row in span_rows:
        candidates = keepers_by_seat.get((row.person_id, row.role_id), ())
        keeper = _covering_span(candidates, row.valid_from)
        if keeper is None:
            logger.warning(
                "house_source_migrate_no_keeper",
                extra={"source_id": row.source_id, "person_id": str(row.person_id)},
            )
            orphans += 1
            continue
        await _retire_onto(session, row, keeper, counters)
        retired += 1

    result = MigrationResult(
        pdc_house_found=len(span_rows),
        retired=retired,
        superseded_retired=superseded_retired,
        anchors_transferred=counters.transferred,
        anchors_dropped=counters.dropped,
        orphans_no_keeper=orphans,
        skipped_legacy=skipped_legacy,
    )
    logger.info("house_source_migrate_complete", extra=result.__dict__)
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Retire usa_wa_pdc House Position rows onto usa_wa_legislature spans (#101)."
    )
    parser.add_argument("--dry-run", action="store_true", help="migrate but roll back (preview)")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL_OWNER")
    if not database_url:
        print(
            "DATABASE_URL_OWNER is not set; aborting — retiring a PDC row deletes its citations, "
            "which the app role is REVOKEd (#54); run under the owner role.",
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
        f"retired={result.retired} superseded_retired={result.superseded_retired} "
        f"anchors_transferred={result.anchors_transferred} "
        f"anchors_dropped={result.anchors_dropped} orphans_no_keeper={result.orphans_no_keeper} "
        f"skipped_legacy={result.skipped_legacy} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
