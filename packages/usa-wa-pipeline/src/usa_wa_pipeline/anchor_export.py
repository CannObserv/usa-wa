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
a second ad-hoc path they fetch and integrity-check differently.

One query, two independent sinks. :func:`anchor_rows` reads the anchors once;
:func:`write_export` writes the local ``--out`` tree and :func:`materialize_anchors`
builds ``pm_anchors`` in the pipeline duckdb, which :mod:`usa_wa_pipeline.publish`
then picks up with no special-casing at all. Neither sink reads the other's
output, which buys two things (CR round 12): retiring the local tree once PM's
puller reads the catalog entry stays a deletion rather than a rewrite (#314
sweeps it), and the two cannot disagree about which id is which — they take
their column order from :data:`ANCHOR_COLUMNS`, and duckdb maps an explicit
``columns=`` spec **positionally**, so a header the loader merely trusted would
have swapped ``usa_wa_id`` and ``pm_id`` silently. Both are 26-char base32:
every downstream shape check would still have passed.

Per-kind counts live only in ``manifest.json`` — the catalog carries a single
``rows`` total, the way every other entry does — but stay derivable from the
published ``kind`` column.

**This job writes.** Read-only on Postgres, but it replaces ``pm_anchors`` in
the duckdb named by ``--db``, which defaults to the production pipeline
database independently of ``--out``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from usa_wa_pipeline.publish import pipeline_db_path

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "pm-anchor-export"

#: The table the publisher reads the crosswalk from, and its columns. BOTH
#: sinks take their column order from here — see the module docstring for what
#: a positional mismatch would cost.
ANCHOR_TABLE = "pm_anchors"
ANCHOR_COLUMNS = ("kind", "usa_wa_id", "pm_id")

#: Filename of the local CSV artifact, named once so the writer and the job agree.
CSV_NAME = "anchors.csv"

_KINDS = (
    ("person", Person, Person.pm_person_id),
    ("organization", Organization, Organization.pm_organization_id),
    ("role", Role, Role.pm_role_id),
    ("assignment", Assignment, Assignment.pm_assignment_id),
)


async def anchor_rows(session: AsyncSession) -> list[tuple[str, str, str]]:
    """Every anchored entity as ``(kind, usa_wa_id, pm_id)``, both ids base32.

    The single read both sinks are built from. Ordered by kind (in
    :data:`_KINDS` order) then local id, so the export is deterministic across
    runs — which is what lets the publisher's skip-if-unchanged hash mean
    "nothing moved" rather than "the rows came back in a different order".
    """
    rows: list[tuple[str, str, str]] = []
    for kind, model, anchor_col in _KINDS:
        result = (
            await session.execute(
                select(model.id, anchor_col).where(anchor_col.isnot(None)).order_by(model.id)
            )
        ).all()
        rows.extend((kind, str(local_id), str(pm_id)) for local_id, pm_id in result)
    return rows


def write_export(rows: Sequence[tuple[str, str, str]], out_dir: Path | str) -> dict[str, int]:
    """Write ``anchors.csv`` + ``manifest.json`` under ``out_dir``. Returns per-kind counts.

    The manifest is the only place the per-kind split survives: a catalog entry
    carries one ``rows`` total, like every other dataset.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / CSV_NAME
    counts: dict[str, int] = {kind: 0 for kind, _, _ in _KINDS}
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(ANCHOR_COLUMNS))
        for kind, local_id, pm_id in rows:
            if kind not in counts:
                raise ValueError(f"unknown anchor kind {kind!r}; expected one of {sorted(counts)}")
            writer.writerow([kind, local_id, pm_id])
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


async def export_anchors(session: AsyncSession, out_dir: Path | str) -> dict[str, int]:
    """Read the anchors and write the local export tree. Returns per-kind counts."""
    return write_export(await anchor_rows(session), out_dir)


def materialize_anchors(rows: Sequence[tuple[str, str, str]], db_path: Path | str) -> int:
    """Build ``pm_anchors`` in the pipeline duckdb from ``rows``. Returns rows written.

    ``create or replace``: a version of this dataset *is* the whole live
    crosswalk, so a re-export replaces rather than accumulates — the same
    retraction-as-absence rule the published contract runs on.

    Every column is carried as pandas ``string`` dtype, which duckdb lands as
    ``VARCHAR``. A ULID is Crockford base32 and may be all digits; typed
    numerically, the id handed to PM no longer resolves — the same encoding trap
    as the ``::text`` UUID-hex form this export exists to avoid. An empty frame
    still declares the columns, so a wiped crosswalk is an empty table the
    publisher's shrink gate can refuse rather than a missing one that reads as a
    build failure.

    Loaded as one registered frame rather than row-by-row: ``executemany`` of
    12k inserts costs ~49s against ~0.02s here, and this runs inside the nightly
    chain.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    values = [tuple(row) for row in rows]
    bad = next((row for row in values if len(row) != len(ANCHOR_COLUMNS)), None)
    if bad is not None:
        raise ValueError(f"row {bad!r} does not match columns {ANCHOR_COLUMNS}")
    frame = pd.DataFrame(values, columns=list(ANCHOR_COLUMNS), dtype="string")
    con = duckdb.connect(str(db_path))
    try:
        con.register("anchor_frame", frame)
        try:
            con.execute(
                # ANCHOR_TABLE is a module constant, not caller input.
                f'create or replace table "{ANCHOR_TABLE}" as select * from anchor_frame'  # noqa: S608
            )
        finally:
            con.unregister("anchor_frame")
        written = con.execute(f'select count(*) from "{ANCHOR_TABLE}"').fetchone()[0]  # noqa: S608
    finally:
        con.close()
    logger.info(
        "anchor_materialize_complete",
        extra={"table": ANCHOR_TABLE, "rows": written, "db": str(db_path)},
    )
    return int(written)


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out",
        default="data/anchor-export",
        help="Local CSV/manifest directory (default data/anchor-export). Does NOT "
        "redirect the duckdb write — see --db.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Pipeline duckdb whose pm_anchors table is REPLACED (default "
        "USA_WA_PIPELINE_DB, else data/pipeline.duckdb — i.e. production). Point "
        "this at a scratch file whenever --out is a scratch directory.",
    )


async def _export_job(ctx: JobContext) -> JobResult:
    rows = await anchor_rows(ctx.require_session())
    counts = write_export(rows, Path(ctx.args.out))
    # The db path is logged, not counted: the run ledger's counters are published
    # verbatim by `GET /api/v1/health/jobs`, and a filesystem path is neither a
    # counter nor something to put on a read surface (CR 113).
    written = materialize_anchors(rows, pipeline_db_path(ctx.args.db))
    return JobResult.ok({**counts, "pm_anchors_rows": written})


def main(argv: list[str] | None = None) -> int:
    """Export the PM anchor crosswalk seed.

    Read-only on Postgres, but it REPLACES ``pm_anchors`` in the pipeline duckdb
    (``--db``, defaulting to production independently of ``--out``).
    """
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
