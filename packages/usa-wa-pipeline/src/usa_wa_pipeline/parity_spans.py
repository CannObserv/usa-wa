"""Span parity probe (#309): conformed spans vs. canonical assignments.

    python -m usa_wa_pipeline.parity_spans [--root PATH] [--json]

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

Exit ``0`` at/below baseline · ``1`` above it · ``4`` no store.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import RawStore, get_raw_root
from clearinghouse_domain_legislative.identity import Assignment
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_pipeline.conformed.spans import SOURCE, SpanInputs, build_all_spans
from usa_wa_pipeline.operator_read import operator_event_rows
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


async def _parity_job(ctx: JobContext) -> JobResult:
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    store = RawStore(root, SOURCE)
    if not store.latest():
        logger.warning("parity_spans_empty_store", extra={"root": str(root)})
        return JobResult.degraded({"empty_store": True})

    session = ctx.require_session()
    current = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    spans = build_all_spans(
        SpanInputs(
            sponsors=wsl.sponsor_rows(store),
            committee_members=wsl.committee_member_rows(store),
            roster=roster_staging.roster_rows(RawStore(root, ROSTER_SOURCE)),
            events=await operator_event_rows(session),
        ),
        current_biennium=current,
    )
    ours = {s.source_id: (s.valid_from, s.valid_to, s.is_active) for s in spans}
    canonical = {
        row[0]: (row[1], row[2], row[3])
        for row in (
            await session.execute(
                select(
                    Assignment.source_id,
                    Assignment.valid_from,
                    Assignment.valid_to,
                    Assignment.is_active,
                ).where(Assignment.source == SOURCE)
            )
        ).all()
        if row[0].split(":")[1] in OWNED_KINDS
    }
    if not canonical:
        # An empty oracle makes every comparison vacuously clean (#302 CR 6).
        logger.warning("parity_spans_empty_canonical", extra={"source": SOURCE})
        return JobResult.degraded({"empty_canonical": True, "spans": len(spans)})

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
        "baseline": ctx.args.baseline,
    }
    log = logger.info if divergence <= ctx.args.baseline else logger.error
    log(
        "parity_spans_report",
        extra={
            "summary": counters,
            "missing_sample": missing[:20],
            "extra_sample": extra[:20],
            "dated_sample": dated[:20],
        },
    )
    if divergence > ctx.args.baseline:
        return JobResult.failed(counters, exit_code=1)
    return JobResult.ok(counters)


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
