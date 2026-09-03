"""Span parity probe (#309): conformed spans vs. canonical assignments.

    python -m usa_wa_pipeline.parity_spans [--root PATH] [--baseline N] [--json]

Write-free. Rebuilds the conformed spans from the raw store + the curated
operator events and diffs them against ``canonical.assignments`` for the kinds
this tier owns (``party``, ``chamber-senate``, ``committee``), keyed on the
span's 4-part identity with ``valid_from``/``valid_to``/``is_active`` compared
exactly.

**The oracle is a snapshot, and it is stale.** Measured 2026-09-03: the stored
assignments diverge from the port by :data:`BASELINE_DIVERGENCE` rows — and
running the *Postgres-tier adapter's own pipeline* fresh that day produced the
**identical** divergence (4,851 spans; 42 missing / 2 extra / 1 dated
differently), because the stored rows predate the current identity-resolve
(the #277/#281 candidate-splitting work). The port and the adapter agreed
exactly with each other: 4,851 = 4,851, zero key and zero value differences.

So this probe's gate is a **ratchet, not equality**: divergence at or below the
recorded baseline is the known staleness, and any growth is a regression in
the port. A Postgres-tier span rebuild would drop the baseline to zero, at
which point lower it here — the number dying is the point.

Exit ``0`` at/below baseline · ``1`` above it · ``4`` degraded — no WSL store,
no roster store (the #228 deepening's input, whose absence would read as a port
regression), or an empty oracle. A degraded probe compared nothing and says so.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import RawStore, get_raw_root
from clearinghouse_core.registry import KIND_PERSON
from clearinghouse_domain_legislative.identity import Assignment
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_pipeline.conformed.spans import (
    SOURCE,
    SpanInputs,
    assignment_rows,
    build_all_spans,
    entity_index,
)
from usa_wa_pipeline.operator_read import operator_event_rows
from usa_wa_pipeline.registry_read import crosswalk_rows
from usa_wa_pipeline.staging import roster as roster_staging
from usa_wa_pipeline.staging import wsl

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "parity-spans"

ROSTER_SOURCE = "usa_wa_legislature_roster"

#: The span kinds the conformed tier owns today. `chamber-house` arrives with
#: the facts-seats port (PDC Position inference, #229) and is excluded until
#: then so its absence cannot read as a regression.
OWNED_KINDS = ("party", "chamber-senate", "committee")

#: Known stale-oracle rows, measured 2026-09-03 (see the module docstring).
#: Lower this the moment a Postgres-tier rebuild lands.
BASELINE_DIVERGENCE = 45


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")
    parser.add_argument(
        "--baseline",
        type=int,
        default=BASELINE_DIVERGENCE,
        help="Max tolerated divergence from the (stale) canonical snapshot.",
    )


def owned_kind(source_id: str) -> str | None:
    """The span ``kind`` encoded in an assignment's ``source_id``, or ``None``.

    A span key is ``<member_id>:<kind>:<discriminator>:<start_biennium>`` — but
    the **member id is not colon-free**: the roster family mints ``<fold>:<year>``
    ids, so its keys carry five segments and counting from the left lands on the
    mint year (CR 58). Only the last three parts are structural, so the read is
    right-anchored. A key too short to carry one returns ``None`` — a probe
    degrades on a malformed row rather than crashing the nightly chain.
    """
    parts = source_id.rsplit(":", 3)
    return parts[1] if len(parts) == 4 else None


async def run_parity(
    session: AsyncSession,
    store: RawStore,
    roster_store: RawStore,
    *,
    baseline: int,
    current_biennium: str,
    sponsor_rows: Callable[[RawStore], list[dict[str, Any]]] = wsl.sponsor_rows,
    committee_member_rows: Callable[[RawStore], list[dict[str, Any]]] = wsl.committee_member_rows,
    roster_rows: Callable[[RawStore], list[dict[str, Any]]] = roster_staging.roster_rows,
) -> JobResult:
    """Diff the rebuilt spans against the canonical snapshot. Write-free.

    The staging readers are injected so the comparator and its exit gate are
    testable without a raw corpus (the shape :mod:`parity_wsl` uses).
    """
    if not store.latest():
        logger.warning("parity_spans_empty_store", extra={"source": SOURCE})
        return JobResult.degraded({"empty_store": True})
    if not roster_store.latest():
        # Without it the #228 deepening is lost and divergence explodes — which
        # would read as a regression in the port rather than a missing input.
        logger.warning("parity_spans_empty_roster_store", extra={"source": ROSTER_SOURCE})
        return JobResult.degraded({"empty_roster_store": True})

    # The guard belongs on the ROWS, not the store (CR 69): a store holding
    # wires that yield nothing must degrade with a named reason here, rather
    # than raising out of build_all_spans through the harness's exception
    # route — which loses the counters (#331).
    roster = roster_rows(roster_store)
    if not roster:
        logger.warning("parity_spans_empty_roster_rows", extra={"source": ROSTER_SOURCE})
        return JobResult.degraded({"empty_roster_rows": True})

    spans = build_all_spans(
        SpanInputs(
            sponsors=sponsor_rows(store),
            committee_members=committee_member_rows(store),
            roster=roster,
            events=await operator_event_rows(session),
        ),
        current_biennium=current_biennium,
    )
    ours = {s.source_id: (s.valid_from, s.valid_to, s.is_active) for s in spans}
    canonical: dict[str, tuple[Any, Any, Any]] = {}
    unparsable = 0
    for row in (
        await session.execute(
            select(
                Assignment.source_id,
                Assignment.valid_from,
                Assignment.valid_to,
                Assignment.is_active,
            ).where(Assignment.source == SOURCE)
        )
    ).all():
        kind = owned_kind(row[0])
        if kind is None:
            # Excluding a key that cannot carry a kind is right; excluding it
            # SILENTLY shrinks the oracle unnoticed (CR 70).
            unparsable += 1
        elif kind in OWNED_KINDS:
            canonical[row[0]] = (row[1], row[2], row[3])
    if not canonical:
        # An empty oracle makes every comparison vacuously clean (#302 CR 6).
        logger.warning("parity_spans_empty_canonical", extra={"source": SOURCE})
        return JobResult.degraded({"empty_canonical": True, "spans": len(spans)})

    # The crosswalk join the `assignments` model performs, reported HERE
    # because the model cannot report it (CR 68): a `dbt build` never calls
    # `configure_logging`, so a logger inside a Python model emits nothing —
    # the info path is dropped outright and the warning path reaches
    # `logging.lastResort`, which prints the message and discards `extra`.
    # This probe runs under the job harness, so its counters are real JSON.
    _rows, join = assignment_rows(spans, entity_index(await crosswalk_rows(session, KIND_PERSON)))

    missing = sorted(set(canonical) - set(ours))
    extra = sorted(set(ours) - set(canonical))
    dated = sorted(k for k in set(ours) & set(canonical) if ours[k] != canonical[k])
    divergence = len(missing) + len(extra) + len(dated)
    counters = {
        "spans": len(spans),
        "canonical": len(canonical),
        "missing": len(missing),
        "extra": len(extra),
        "dated_differently": len(dated),
        "divergence": divergence,
        "baseline": baseline,
        "registered_spans": join["published"],
        "unregistered_spans": join["unregistered_spans"],
        "unparsable_canonical_keys": unparsable,
    }
    log = logger.info if divergence <= baseline else logger.error
    log(
        "parity_spans_report",
        extra={
            "summary": counters,
            "missing_sample": missing[:20],
            "extra_sample": extra[:20],
            "dated_sample": dated[:20],
        },
    )
    if divergence > baseline:
        return JobResult.failed(counters, exit_code=1)
    return JobResult.ok(counters)


async def _parity_job(ctx: JobContext) -> JobResult:
    """Bind the probe to the real raw root and the current biennium."""
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    return await run_parity(
        ctx.require_session(),
        RawStore(root, SOURCE),
        RawStore(root, ROSTER_SOURCE),
        baseline=ctx.args.baseline,
        current_biennium=(
            os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """Diff conformed spans against the canonical assignment snapshot."""
    return run_job(
        JOB_SLUG,
        _parity_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.parity_spans",
        description="Write-free parity: conformed tenure spans vs canonical assignments (#309).",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
