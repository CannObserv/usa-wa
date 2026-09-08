"""PM anchor export (#312): the one-time crosswalk seed for power-map cutover.

    python -m usa_wa_pipeline.anchor_export [--out DIR] [--db PATH] [--json]

Exports every local ``pm_*`` anchor as ``(kind, usa_wa_id, pm_id)`` — both ids
in 26-char Crockford **base32** (the ``::text`` UUID-hex form 404s at PM;
project memory, spec § publication) — for PM's transition steps 2–4: resolve
each ``pm_id`` through ``merged_into`` chains to the live survivor, and treat
unresolvable ids as a blocking report on their side (power-map design doc
safeguard 1). One CSV (``anchors.csv``) plus ``manifest.json`` carrying
per-kind counts and the CSV's sha256, so the file's integrity and coverage are
checkable on arrival. Read-only on Postgres; re-run replaces the output.

**Delivery is the catalog (#354, power-map#495).** The export is a dataset in
all but name — immutable, deterministically ordered, hash-carrying,
regenerable — so it reaches PM the way every other dataset does rather than as
a second ad-hoc path they fetch and integrity-check differently. The bridge is
:func:`materialize_anchors`: the CSV lands in the pipeline duckdb as
``pm_anchors``, and :mod:`usa_wa_pipeline.publish` picks it up from there with
no special-casing at all. The local ``--out`` tree keeps being written
alongside it — per-kind counts live only in ``manifest.json``, and the old path
stays until PM confirms its puller reads the catalog entry (#314 sweeps it).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "pm-anchor-export"

#: The table the publisher reads the crosswalk from, and its columns. Named
#: here rather than inline so the test that pins the published schema and the
#: writer cannot drift apart.
ANCHOR_TABLE = "pm_anchors"
ANCHOR_COLUMNS = ("kind", "usa_wa_id", "pm_id")

#: Where the built pipeline duckdb lives, matching `publish`'s own default.
PIPELINE_DB_ENV = "USA_WA_PIPELINE_DB"
_DEFAULT_PIPELINE_DB = "data/pipeline.duckdb"

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


def pipeline_db_path(explicit: str | Path | None) -> Path:
    """Resolve the duckdb to materialize into: flag, then env, then the default.

    Deliberately the same resolution :mod:`usa_wa_pipeline.publish` uses. If the
    two ever drift the export writes a table the publisher never reads, and the
    catalog quietly stops carrying the crosswalk while both jobs report success.
    """
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get(PIPELINE_DB_ENV, _DEFAULT_PIPELINE_DB))


def materialize_anchors(csv_path: Path | str, db_path: Path | str) -> int:
    """Load ``anchors.csv`` into the pipeline duckdb as ``pm_anchors``. Returns rows.

    ``create or replace``: a version of this dataset *is* the whole live
    crosswalk, so a re-export replaces rather than accumulates — the same
    retraction-as-absence rule the published contract runs on.

    Every column is pinned to ``VARCHAR`` instead of being sniffed. A ULID is
    Crockford base32 and may be all digits; left to infer, duckdb would type
    such a column numerically and hand PM an id that no longer resolves — the
    same encoding trap as the ``::text`` UUID-hex form this export exists to
    avoid.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    spec = ", ".join(f"'{column}': 'VARCHAR'" for column in ANCHOR_COLUMNS)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            f'create or replace table "{ANCHOR_TABLE}" as '  # noqa: S608
            f"select * from read_csv(?, header = true, columns = {{{spec}}})",
            [str(csv_path)],
        )
        rows = con.execute(f'select count(*) from "{ANCHOR_TABLE}"').fetchone()[0]  # noqa: S608
    finally:
        con.close()
    logger.info("anchor_materialize_complete", extra={"table": ANCHOR_TABLE, "rows": rows})
    return int(rows)


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out", default="data/anchor-export", help="Output directory (default data/anchor-export)."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Pipeline duckdb to materialize pm_anchors into (default USA_WA_PIPELINE_DB).",
    )


async def _export_job(ctx: JobContext) -> JobResult:
    out_dir = Path(ctx.args.out)
    counts = await export_anchors(ctx.require_session(), out_dir)
    db_path = pipeline_db_path(ctx.args.db)
    rows = materialize_anchors(out_dir / "anchors.csv", db_path)
    return JobResult.ok({**counts, "pm_anchors_rows": rows, "pipeline_db": str(db_path)})


def main(argv: list[str] | None = None) -> int:
    """Export the PM anchor crosswalk seed. Read-only on the database."""
    return run_job(
        JOB_SLUG,
        _export_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.anchor_export",
        description=(
            "Export pm_* anchors as the base32 crosswalk seed for PM cutover "
            "(#312) and materialize them for publication (#354)."
        ),
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
