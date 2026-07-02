"""Provenance integrity sweep (#54) — at-rest tamper / corruption detection.

Re-hashes every stored :class:`~clearinghouse_core.provenance.RawPayload` body
against the :class:`~clearinghouse_core.provenance.FetchEvent.content_hash`
baseline written at fetch time (item 1). A mismatch means the archived bytes no
longer hash to what was recorded — corruption or tampering. NULL baselines are
the pre-#54 legacy tail: "unbaselined", counted apart and never treated as a
mismatch (a NULL is not a verified zero, and an all-zeros sentinel would be a
collision target — see the ``content_hash`` docstring).

``content_hash`` is defined over *exactly* ``RawPayload.body``, so the sweep
hashes the stored bytes directly — the same canonical form the writer used.

Run as a CLI (jurisdiction-agnostic; siblings reuse it)::

    python -m clearinghouse_core.integrity                 # rolling slice (resumes)
    python -m clearinghouse_core.integrity --full          # whole corpus, ignore cursor
    python -m clearinghouse_core.integrity --limit 500     # partial (surfaced as limited)
    python -m clearinghouse_core.integrity --byte-budget 268435456  # slice size override

**Rolling since-cursor (#55).** The default run verifies a bounded byte-slice of
the archive and persists a ULID watermark (:mod:`clearinghouse_core.sweep_state`)
so the next run resumes past it, wrapping to the beginning once the tail is
reached. Per-run cost stays flat as the #39 docket archive grows unbounded — the
whole corpus is re-verified every ``ceil(bytes / byte_budget)`` runs, never in
one O(all-payloads) pass that would race ``TimeoutStartSec=``. Corruption in an
already-verified body is thus caught within one coverage cycle, not on every run:
size the budget so a cycle spans an acceptable detection latency. ``--full``
forces a single whole-corpus pass (post-incident audit) without touching the
cursor.

Exit codes: ``0`` clean (all baselined rows verified; unbaselined allowed);
``1`` at least one mismatch (the failure the #49 alert path surfaces). The
weekly oneshot's ``OnFailure=`` turns a non-zero exit into an operator email.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import FetchEvent, RawPayload
from clearinghouse_core.sweep_state import SWEEP_SCOPE, IntegritySweepState

logger = get_logger(__name__)

DEFAULT_BYTE_BUDGET = 256 * 1024 * 1024
"""Per-run byte budget for the rolling sweep (#55). 256 MiB detoasts + hashes in
seconds, well inside ``TimeoutStartSec=600``, while covering today's whole KB–MB
archive in a single run. Lower it if a coverage cycle needs to span fewer runs;
raise it to shorten detection latency at the cost of per-run wall-clock."""


@dataclass
class SweepReport:
    """Outcome of one integrity sweep."""

    scanned: int = 0
    verified: int = 0
    unbaselined: int = 0
    mismatched: int = 0
    limited: bool = False
    last_id: str | None = None
    reached_end: bool = False
    coverage_cycle_complete: bool = False
    mismatches: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no body diverged from its baseline. Unbaselined rows are not
        failures — they had no baseline to diverge from."""
        return self.mismatched == 0


async def sweep_payloads(
    session: AsyncSession,
    *,
    limit: int | None = None,
    after_id: str | _ULID | None = None,
    byte_budget: int | None = None,
) -> SweepReport:
    """Re-hash stored payload bodies against their content_hash baseline.

    Streams rows so a large archive doesn't load every body at once. ``after_id``
    resumes past a rolling cursor (only ``RawPayload.id > after_id``, ULID/time
    order); ``byte_budget`` bounds the slice, stopping after the row that crosses
    it (so a single oversized payload still gets scanned — never a stall).
    ``limit`` caps the scan by row count for a quick partial check; the cap is
    surfaced on the report (``limited``) so a bounded run never reads as full
    coverage. ``reached_end`` is set when this slice exhausted the archive tail —
    the wrap point for the rolling cursor.
    """
    report = SweepReport()
    stmt = (
        select(
            RawPayload.id,
            RawPayload.body,
            RawPayload.size_bytes,
            FetchEvent.content_hash,
            FetchEvent.resource_id,
            FetchEvent.id,
        )
        .join(FetchEvent, RawPayload.fetch_event_id == FetchEvent.id)
        .order_by(RawPayload.id)
    )
    if after_id is not None:
        stmt = stmt.where(RawPayload.id > after_id)
    if limit is not None:
        stmt = stmt.limit(limit)
        report.limited = True

    consumed_bytes = 0
    budget_hit = False
    result = await session.stream(stmt)
    async for row_id, body, size_bytes, content_hash, resource_id, fetch_event_id in result:
        report.scanned += 1
        report.last_id = str(row_id)
        if content_hash is None:
            report.unbaselined += 1
        elif hashlib.sha256(body).digest() == content_hash:
            report.verified += 1
        else:
            report.mismatched += 1
            report.mismatches.append(
                {"resource_id": resource_id, "fetch_event_id": str(fetch_event_id)}
            )
            logger.error(
                "integrity_sweep_mismatch",
                extra={"resource_id": resource_id, "fetch_event_id": str(fetch_event_id)},
            )
        consumed_bytes += size_bytes or 0
        if byte_budget is not None and consumed_bytes >= byte_budget:
            budget_hit = True
            await result.close()
            break

    if report.limited:
        report.reached_end = False  # a row-capped partial run is not full coverage
    elif not budget_hit:
        report.reached_end = True  # stream drained: no rows remain after the slice
    else:
        # Budget stopped us mid-archive — only end-of-corpus if nothing follows.
        remaining = await session.scalar(
            select(RawPayload.id).where(RawPayload.id > report.last_id).limit(1)
        )
        report.reached_end = remaining is None
    return report


async def load_cursor(session: AsyncSession) -> _ULID | None:
    """Return the persisted rolling-sweep watermark (a ULID), or None to start a
    fresh coverage cycle from the beginning of the archive (#55)."""
    row = await session.scalar(
        select(IntegritySweepState).where(IntegritySweepState.scope == SWEEP_SCOPE)
    )
    return row.cursor if row is not None else None


async def _save_cursor(session: AsyncSession, cursor: str | _ULID | None) -> None:
    """Upsert the singleton watermark row for the RawPayload sweep stream."""
    row = await session.scalar(
        select(IntegritySweepState).where(IntegritySweepState.scope == SWEEP_SCOPE)
    )
    if row is None:
        session.add(IntegritySweepState(scope=SWEEP_SCOPE, cursor=cursor))
    else:
        row.cursor = cursor


async def rolling_sweep(
    session: AsyncSession, *, byte_budget: int = DEFAULT_BYTE_BUDGET
) -> SweepReport:
    """Verify one bounded slice, resuming from the persisted cursor and wrapping (#55).

    Reads the watermark, sweeps ``byte_budget`` worth of payloads past it, then
    persists the new watermark: the last id scanned, or ``NULL`` to wrap once the
    archive tail is reached (``coverage_cycle_complete``). A stale cursor sitting
    past the tail (e.g. rows GC'd below it) wraps and re-scans from the beginning
    in the same run rather than burning a dead 0-row pass. Commits the cursor over
    exactly the slice verified, so a crash mid-run re-does the slice, never skips
    it.

    Re-alert cadence: the cursor advances past a mismatched payload too, so a
    given corruption raises the #49 exit-1 alert once — on the run that finds it —
    and is not re-reported until the next coverage cycle re-verifies that slice
    (``ceil(bytes / byte_budget)`` runs later), not weekly. "No follow-up email"
    means "not yet re-scanned," not "resolved."
    """
    cursor = await load_cursor(session)
    report = await sweep_payloads(session, after_id=cursor, byte_budget=byte_budget)
    if cursor is not None and report.scanned == 0:
        # Cursor parked past the tail — wrap and verify a fresh slice from the start.
        report = await sweep_payloads(session, after_id=None, byte_budget=byte_budget)
    report.coverage_cycle_complete = report.reached_end
    await _save_cursor(session, None if report.reached_end else report.last_id)
    await session.commit()
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m clearinghouse_core.integrity",
        description="Re-hash stored RawPayload bodies against their content_hash baseline (#54).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of payloads scanned (partial sweep; surfaced as limited).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Verify the whole corpus in one pass, ignoring the rolling cursor (#55).",
    )
    parser.add_argument(
        "--byte-budget",
        type=int,
        default=DEFAULT_BYTE_BUDGET,
        help=(
            "Per-run byte budget for the rolling sweep (#55; default "
            f"{DEFAULT_BYTE_BUDGET}). Ignored with --full or --limit."
        ),
    )
    return parser


async def _run(args: argparse.Namespace) -> SweepReport:
    factory = get_session_factory()
    async with factory() as session:
        if args.full:
            # A whole-corpus pass IS a completed coverage cycle — mirror the
            # rolling run's signal so --full doesn't report the opposite (#55 CR).
            report = await sweep_payloads(session)
            report.coverage_cycle_complete = report.reached_end
            return report
        if args.limit is not None:
            return await sweep_payloads(session, limit=args.limit)
        return await rolling_sweep(session, byte_budget=args.byte_budget)


def main(argv: list[str] | None = None) -> int:
    """Run the sweep, print the report as JSON, and exit non-zero on any mismatch.

    Exit codes: ``0`` clean; ``1`` at least one mismatch (corruption/tamper). The
    one-shot's ``OnFailure=`` (#49) emails the operator on the non-zero exit."""
    configure_logging()
    args = _build_parser().parse_args(argv)
    report = asyncio.run(_run(args))
    json.dump(
        {
            "scanned": report.scanned,
            "verified": report.verified,
            "unbaselined": report.unbaselined,
            "mismatched": report.mismatched,
            "limited": report.limited,
            "last_id": report.last_id,
            "reached_end": report.reached_end,
            "coverage_cycle_complete": report.coverage_cycle_complete,
            "mismatches": report.mismatches,
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    if not report.ok:
        logger.error("integrity_sweep_failed", extra={"mismatched": report.mismatched})
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
