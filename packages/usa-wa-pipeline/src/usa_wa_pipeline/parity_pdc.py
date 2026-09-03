"""PDC staging-parity probe (#307): canonical `wa_pdc` links ⊆ staging winners.

    python -m usa_wa_pipeline.parity_pdc [--root PATH] [--json]

Write-free, one-directional (:func:`~usa_wa_pipeline.parity.subset_parity`):
every canonical `PersonIdentifier(scheme='wa_pdc')` value — the PDC person ids
the old pipeline linked onto matched members (#79) — must appear among the
staging winner rows' ``person_id``s. Staging legitimately holds more (canonical
links only matched members); loss is the failure.

SOS carries no per-source parity probe on purpose: results/filings never mint
canonical entities — they corroborate spans built in the conformed tier, where
#309's span parity covers them.

Exit ``0`` clean · ``1`` canonical ids missing from staging · ``4`` no store
or empty canonical oracle (a subset probe must never pass vacuously).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import RawStore, get_raw_root
from clearinghouse_domain_legislative.identity import PersonIdentifier
from usa_wa_pipeline.parity import subset_parity
from usa_wa_pipeline.staging import pdc

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "parity-pdc-staging"

SOURCE = "usa_wa_pdc"
SCHEME = "wa_pdc"


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")


async def _parity_job(ctx: JobContext) -> JobResult:
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    store = RawStore(root, SOURCE)
    if not store.latest():
        logger.warning("parity_pdc_empty_store", extra={"root": str(root)})
        return JobResult.degraded({"empty_store": True})
    staging = {r["person_id"] for r in pdc.winner_rows(store) if r["person_id"]}
    session = ctx.require_session()
    canonical = set(
        (
            await session.execute(
                select(PersonIdentifier.value).where(PersonIdentifier.scheme == SCHEME)
            )
        ).scalars()
    )
    if not canonical:
        # A subset probe with an empty oracle passes vacuously — and an empty
        # oracle means a misconfigured DSN or scheme, never "nothing to check"
        # (prod carries hundreds of wa_pdc links). Degrade like an empty store.
        logger.warning("parity_pdc_empty_canonical", extra={"scheme": SCHEME})
        return JobResult.degraded({"empty_canonical": True, "staging": len(staging)})
    report = subset_parity("pdc_winners", staging, canonical)
    counters = {
        "staging": report.staging_total,
        "canonical": report.canonical_total,
        "only_canonical": len(report.only_canonical),
    }
    log = logger.info if report.clean else logger.error
    log("parity_pdc_report", extra={"report": report.render()})
    if report.clean:
        return JobResult.ok(counters)
    return JobResult.failed(counters, exit_code=1)


def main(argv: list[str] | None = None) -> int:
    """Subset parity: canonical wa_pdc ids must all exist among staging winners."""
    return run_job(
        JOB_SLUG,
        _parity_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.parity_pdc",
        description="Write-free parity probe: canonical wa_pdc links ⊆ staging PDC winners (#307).",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
