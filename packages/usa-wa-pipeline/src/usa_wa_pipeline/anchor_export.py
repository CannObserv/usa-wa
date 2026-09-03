"""PM anchor export (#312): the one-time crosswalk seed for power-map cutover.

    python -m usa_wa_pipeline.anchor_export [--out DIR] [--json]

Exports every local ``pm_*`` anchor as ``(kind, usa_wa_id, pm_id)`` — both ids
in 26-char Crockford **base32** (the ``::text`` UUID-hex form 404s at PM;
project memory, spec § publication) — for PM's transition steps 2–4: resolve
each ``pm_id`` through ``merged_into`` chains to the live survivor, and treat
unresolvable ids as a blocking report on their side (power-map design doc
safeguard 1). One CSV (``anchors.csv``) plus ``manifest.json`` carrying
per-kind counts and the CSV's sha256, so the file's integrity and coverage are
checkable on arrival. Read-only; re-run replaces the output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "pm-anchor-export"

_KINDS = (
    ("person", Person, Person.pm_person_id),
    ("organization", Organization, Organization.pm_organization_id),
    ("role", Role, Role.pm_role_id),
    ("assignment", Assignment, Assignment.pm_assignment_id),
)


async def export_anchors(session: AsyncSession, out_dir: Path | str) -> dict[str, int]:
    """Write ``anchors.csv`` + ``manifest.json`` under ``out_dir``. Returns counts."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "anchors.csv"
    counts: dict[str, int] = {}
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["kind", "usa_wa_id", "pm_id"])
        for kind, model, anchor_col in _KINDS:
            counts[kind] = 0
            rows = (
                await session.execute(
                    select(model.id, anchor_col).where(anchor_col.isnot(None)).order_by(model.id)
                )
            ).all()
            for local_id, pm_id in rows:
                writer.writerow([kind, str(local_id), str(pm_id)])
                counts[kind] += 1
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "counts": counts,
                "sha256": digest,
                "encoding": "ulid-base32",
            },
            indent=2,
        )
        + "\n"
    )
    logger.info("anchor_export_complete", extra={"counts": counts, "sha256": digest})
    return counts


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out", default="data/anchor-export", help="Output directory (default data/anchor-export)."
    )


async def _export_job(ctx: JobContext) -> JobResult:
    counts = await export_anchors(ctx.require_session(), Path(ctx.args.out))
    return JobResult.ok(counts)


def main(argv: list[str] | None = None) -> int:
    """Export the PM anchor crosswalk seed. Read-only on the database."""
    return run_job(
        JOB_SLUG,
        _export_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.anchor_export",
        description="Export pm_* anchors as the base32 crosswalk seed for PM cutover (#312).",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
