"""PM anchor export (#312): the crosswalk seed for power-map cutover.

    python -m usa_wa_pipeline.anchor_export [--db PATH] [--json]

Exports every local ``pm_*`` anchor as ``(kind, usa_wa_id, pm_id)`` — both ids
in 26-char Crockford **base32** (the ``::text`` UUID-hex form 404s at PM;
project memory, spec § publication) — for PM's transition steps 2–4: resolve
each ``pm_id`` through ``merged_into`` chains to the live survivor, and treat
unresolvable ids as a blocking report on their side (power-map design doc
safeguard 1).

**Delivery is the catalog, and only the catalog (#354, power-map#495).**
:func:`anchor_rows` reads the anchors; :func:`materialize_anchors` builds
``pm_anchors`` in the pipeline duckdb; :mod:`usa_wa_pipeline.publish` publishes
it with no special-casing at all, which is what makes its hash, its dialect and
its version the same contract every other dataset gets.

The local ``data/anchor-export/`` tree this job used to write is **retired**.
It was never HTTP-reachable — it moved by manual copy — and running it beside
the publisher meant two writers for one dataset: they disagreed on line endings
and row order, giving identical content two sha256 values and a consumer no way
to tell a serialisation difference from corruption (#357). One writer per
dataset is now the rule (``docs/ARCHITECTURE.md``), and this module keeps it.
Per-kind counts moved to the job's counters, and stay derivable from the
published ``kind`` column.

**This job writes.** Read-only on Postgres, but it replaces ``pm_anchors`` in
the duckdb named by ``--db``, which defaults to the production pipeline
database.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
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

_KINDS = (
    ("person", Person, Person.pm_person_id),
    ("organization", Organization, Organization.pm_organization_id),
    ("role", Role, Role.pm_role_id),
    ("assignment", Assignment, Assignment.pm_assignment_id),
)


async def anchor_rows(session: AsyncSession) -> list[tuple[str, str, str]]:
    """Every anchored entity as ``(kind, usa_wa_id, pm_id)``, both ids base32.

    **Live rows only** (#356). A locally archived or deleted row is one this
    deployment has stopped asserting, so a crosswalk that still names it hands
    PM a mapping to a row nothing should write to — retraction-as-absence, the
    #302 publication contract, which this dataset was quietly exempt from.

    It leaked 34: 32 narrow tenure spans that PM's own newer anchors already
    supersede (the deepened spans the registry minted in August), plus the two
    John Wynne LD-39 claims both sides archived on 2026-08-05. Every one of them
    put a row on a human's worklist that neither side believes.

    The order rows come back in is
    incidental — a by-product of walking :data:`_KINDS` — and nothing depends on
    it: :func:`materialize_anchors` is order-indifferent, and the publisher
    sorts on export, so the bytes are stable whatever order arrives here. (This
    sentence used to credit `write_export`, the second writer #357 retired —
    the publisher's sort is what actually does the work.) Do not build a
    coupling on it.
    """
    rows: list[tuple[str, str, str]] = []
    for kind, model, anchor_col in _KINDS:
        result = (
            await session.execute(
                select(model.id, anchor_col)
                .where(
                    anchor_col.isnot(None),
                    model.archived_at.is_(None),
                    model.deleted_at.is_(None),
                )
                .order_by(model.id)
            )
        ).all()
        rows.extend((kind, str(local_id), str(pm_id)) for local_id, pm_id in result)
    return rows


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


def kind_counts(rows: Sequence[tuple[str, str, str]]) -> dict[str, int]:
    """Per-kind totals, every kind present even at zero.

    The catalog carries one ``rows`` total like every other dataset, so this is
    where the split PM verifies against now lives: the job's counters, and the
    published ``kind`` column.
    """
    counts: dict[str, int] = {kind: 0 for kind, _, _ in _KINDS}
    for kind, _, _ in rows:
        if kind not in counts:
            raise ValueError(f"unknown anchor kind {kind!r}; expected one of {sorted(counts)}")
        counts[kind] += 1
    return counts


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="Pipeline duckdb whose pm_anchors table is REPLACED (default "
        "USA_WA_PIPELINE_DB, else data/pipeline.duckdb — i.e. production). Point "
        "this at a scratch file whenever --out is a scratch directory.",
    )


async def _export_job(ctx: JobContext) -> JobResult:
    rows = await anchor_rows(ctx.require_session())
    counts = kind_counts(rows)
    # The db path is logged, not counted: the run ledger's counters are published
    # verbatim by `GET /api/v1/health/jobs`, and a filesystem path is neither a
    # counter nor something to put on a read surface (CR 113).
    written = materialize_anchors(rows, pipeline_db_path(ctx.args.db))
    return JobResult.ok({**counts, "pm_anchors_rows": written})


def main(argv: list[str] | None = None) -> int:
    """Export the PM anchor crosswalk seed into the pipeline duckdb.

    Read-only on Postgres; REPLACES ``pm_anchors`` in the duckdb named by
    ``--db``, which defaults to production.
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
