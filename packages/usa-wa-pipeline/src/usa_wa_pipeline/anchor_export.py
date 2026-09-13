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
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

import duckdb
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import KIND_ORG, KIND_PERSON, KIND_ROLE, merge_map
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from clearinghouse_domain_legislative.span_emit import span_key_parts
from usa_wa_pipeline.publish import pipeline_db_path

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "pm-anchor-export"

#: The table the publisher reads the crosswalk from, and its columns. The
#: publisher is the ONE sink since #357 retired `write_export` (the `anchor_rows`
#: docstring records that); a row is built positionally against this tuple, which
#: is what `materialize_anchors` length-checks rather than trusts.
ANCHOR_TABLE = "pm_anchors"
ANCHOR_COLUMNS = ("kind", "usa_wa_id", "pm_id", "span_key")

#: The conformed table an assignment anchor takes its `span_key` from, and the
#: columns the join reads. Derivable on BOTH sides from what each already holds:
#: canonical carries `source` and a `source_id` that right-splits into the last
#: three (#259), and the published row carries all five as columns.
ASSIGNMENTS_TABLE = "assignments"
ASSIGNMENT_JOIN = (
    "source",
    "member_id",
    "span_kind",
    "span_discriminator",
    "span_start_biennium",
)

_KINDS = (
    ("person", Person, Person.pm_person_id),
    ("organization", Organization, Organization.pm_organization_id),
    ("role", Role, Role.pm_role_id),
    ("assignment", Assignment, Assignment.pm_assignment_id),
)


def _anchored_and_live(model, anchor_col):
    """The predicate both readers below share: anchored, and not locally retired.

    One definition, because two copies of it could disagree — and the whole
    point of the counter is that it names the same rows the export withheld.
    """
    return (anchor_col.isnot(None), model.archived_at.is_(None), model.deleted_at.is_(None))


async def _merged_entity_ids(session: AsyncSession) -> set[str]:
    """Every tombstoned registry entity id, across all three kinds."""
    merged: set[str] = set()
    for registry_kind in (KIND_PERSON, KIND_ORG, KIND_ROLE):
        merged |= set(await merge_map(session, registry_kind))
    return merged


async def withheld_for_tombstones(session: AsyncSession) -> int:
    """How many otherwise-exportable anchors :func:`anchor_rows` withholds (CR 17).

    Withholding is otherwise invisible: the counters name what shipped, and a
    dataset quietly getting smaller is the one shape the publisher reacts to
    without explaining. A bulk adjudication shrinks this export, a shrink past
    ``max_shrink`` refuses the publish, and a refused publish mints NOTHING —
    every other dataset included. The refusal does name this dataset and its
    before/after counts, so the cause is findable; this counter is what makes it
    attributable to merges rather than to the #356 archival screen, without
    anyone re-deriving it by hand mid-incident.
    """
    merged = await _merged_entity_ids(session)
    if not merged:
        return 0
    total = 0
    for _, model, anchor_col in _KINDS:
        ids = (
            (await session.execute(select(model.id).where(*_anchored_and_live(model, anchor_col))))
            .scalars()
            .all()
        )
        total += sum(1 for local_id in ids if str(local_id) in merged)
    return total


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

    **And no tombstoned entity** (#368). A registry merge is a THIRD retraction
    signal, and this export saw neither of the first two in it: the loser's
    canonical row is not archived and not deleted — nothing happened to it
    locally — so its anchor kept shipping. The #366 Heck merge was the first
    case, and the export pointed at a PM row power-map#514 then deleted. Re-
    seeding PM's crosswalk from that blocks twice: two usa-wa ids landing on one
    PM row reads as "PM merged what the producer holds apart", and once PM's
    tombstone retention lapses the id resolves as `missing`, which is
    unresolvable. Same rule the conformed tier settled in #366 — a retired id
    stops being addressable.

    Only the ENTITY retires. The loser's ASSIGNMENT anchors stay, because PM's
    merge keeps an assignment's own id and changes only whose it is, so those
    still resolve; dropping them would retract a mapping both sides believe.
    That falls out of the filter rather than being special-cased: a registry
    entity is a canonical person/org/role ULID (the seed preserved them, which
    is the only reason these two stores can be joined by id at all), and an
    assignment's id is from a different table, so it is never in the set.

    The order rows come back in is
    incidental — a by-product of walking :data:`_KINDS` — and nothing depends on
    it: :func:`materialize_anchors` is order-indifferent, and the publisher
    sorts on export, so the bytes are stable whatever order arrives here. (This
    sentence used to credit `write_export`, the second writer #357 retired —
    the publisher's sort is what actually does the work.) Do not build a
    coupling on it.
    """
    merged = await _merged_entity_ids(session)
    rows: list[tuple[str, str, str]] = []
    for kind, model, anchor_col in _KINDS:
        result = (
            await session.execute(
                select(model.id, anchor_col)
                .where(*_anchored_and_live(model, anchor_col))
                .order_by(model.id)
            )
        ).all()
        rows.extend(
            (kind, str(local_id), str(pm_id))
            for local_id, pm_id in result
            if str(local_id) not in merged
        )
    return rows


async def assignment_join_keys(
    session: AsyncSession,
) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    """Local assignment id → the tuple that finds its published row.

    ``(source, member_id, span_kind, span_discriminator, span_start_biennium)``,
    unique across the conformed set (8,395 / 8,395 on 2026-09-11) and derivable
    from canonical without touching the registry: the source is a column and the
    other four right-split out of the span ``source_id`` (:func:`span_key_parts`,
    right-split since #259 — the roster family's member ids contain colons, so a
    left-to-right split silently excludes that entire source).

    Deliberately NOT the published key itself. Deriving that here would fork the
    registry lookup the conformed tier does — including its merge-tombstone
    resolution — into a second implementation, which is the failure power-map#490
    asked us to make impossible rather than unlikely.

    Returns the map **and the ids whose ``source_id`` would not parse** (CR 13).
    Those cannot be joined either, but for a different reason, and collapsing the
    two would report a local key defect as dataset absence — the one number PM
    sizes its archive threshold from.
    """
    result = (
        await session.execute(
            select(Assignment.id, Assignment.source, Assignment.source_id).where(
                *_anchored_and_live(Assignment, Assignment.pm_assignment_id)
            )
        )
    ).all()
    keys: dict[str, tuple[str, ...]] = {}
    unparseable: set[str] = set()
    for local_id, source, source_id in result:
        parts = span_key_parts(str(source_id))
        if parts is None:
            unparseable.add(str(local_id))
            logger.warning(
                "anchor_span_key_unparseable_source_id",
                extra={"usa_wa_id": str(local_id), "source_id": str(source_id)},
            )
            continue
        member_id, span_kind, discriminator, start_biennium = parts
        keys[str(local_id)] = (str(source), member_id, span_kind, discriminator, start_biennium)
    return keys, unparseable


def attach_span_keys(
    rows: Sequence[tuple[str, str, str]],
    join_keys: Mapping[str, tuple[str, ...]],
    db_path: Path | str,
    *,
    unparseable: Collection[str] = (),
) -> tuple[list[tuple[str, str, str, str]], dict[str, int]]:
    """``rows`` with each assignment anchor's published ``span_key`` appended.

    The key is **copied from the built ``assignments`` table**, never recomputed
    (usa-wa#370). power-map#490 needs one producer-serialized string that both
    sinks agree on; a join makes disagreement unrepresentable, where a second
    serializer would only make it unlikely.

    An anchor with no published row gets ``""``. That is not a gap, it is the
    signal: PM keeps such anchors on purpose, because an unanchored PM row falls
    outside its applier's row scope and could never be retired. 384 of them on
    2026-09-11, #289's collapsed party tails among them; an empty key is the
    producer saying "absent", and PM archives on absence.

    Non-assignment kinds carry ``""`` too — the ``kind`` column is what
    distinguishes "not an assignment" from "an assignment with no published row".

    A missing database or ``assignments`` table **raises**: a keyless crosswalk is
    a seed PM cannot re-key from, and exporting one silently would surface the
    failure on their side at cutover instead of here. The database is opened
    read-only, and its absence is checked BEFORE connecting (CR 14) — duckdb
    creates the file it is asked to open, so a typo'd ``--db`` used to mint an
    empty database at that path on the way to reporting the error.

    Returns the keyed rows and the counts that describe them, computed once
    (CR 15): ``matched``, ``absent``, and ``unparseable`` — an id whose
    ``source_id`` would not parse cannot be joined either, but that is a local key
    defect and not the absence signal, so it is counted apart from it.
    """
    path = Path(db_path)
    if not path.exists():
        raise RuntimeError(
            f"{path} does not exist — the anchor export reads each assignment's span_key "
            "from the built conformed table and will not create a database to find it "
            "missing. Run `dbt build` (the nightly chain does) before exporting."
        )
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = {name for (name,) in con.execute("show tables").fetchall()}
        if ASSIGNMENTS_TABLE not in tables:
            raise RuntimeError(
                f"{ASSIGNMENTS_TABLE!r} is not in {db_path} — the anchor export takes each "
                "assignment's span_key from the built conformed table and will not derive "
                "one. Run `dbt build` (the nightly chain does) before exporting."
            )
        columns = ", ".join(f'"{c}"' for c in (*ASSIGNMENT_JOIN, "span_key"))
        published = {
            tuple(str(v) for v in row[:-1]): str(row[-1])
            for row in con.execute(
                f'select {columns} from "{ASSIGNMENTS_TABLE}"'  # noqa: S608
            ).fetchall()
        }
    finally:
        con.close()
    unresolved = set(unparseable)
    keyed = [(*row, published.get(join_keys.get(row[1], ()), "")) for row in rows]
    counts = {"matched": 0, "absent": 0, "unparseable": 0}
    for kind, usa_wa_id, _pm_id, key in keyed:
        if kind != "assignment":
            continue
        if key:
            counts["matched"] += 1
        elif usa_wa_id in unresolved:
            counts["unparseable"] += 1
        else:
            counts["absent"] += 1
    logger.info(
        "anchor_span_keys_attached",
        extra={"published_assignments": len(published), **counts},
    )
    return keyed, counts


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


def kind_counts(rows: Sequence[tuple[str, ...]]) -> dict[str, int]:
    """Per-kind totals, every kind present even at zero.

    The catalog carries one ``rows`` total like every other dataset, so this is
    where the split PM verifies against now lives: the job's counters, and the
    published ``kind`` column.
    """
    counts: dict[str, int] = {kind: 0 for kind, _, _ in _KINDS}
    for kind, *_ in rows:
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
    session = ctx.require_session()
    rows = await anchor_rows(session)
    counts = kind_counts(rows)
    withheld = await withheld_for_tombstones(session)
    db_path = pipeline_db_path(ctx.args.db)
    join_keys, unparseable = await assignment_join_keys(session)
    keyed, span_key_counts = attach_span_keys(rows, join_keys, db_path, unparseable=unparseable)
    # The db path is logged, not counted: the run ledger's counters are published
    # verbatim by `GET /api/v1/health/jobs`, and a filesystem path is neither a
    # counter nor something to put on a read surface (CR 113).
    written = materialize_anchors(keyed, db_path)
    return JobResult.ok(
        {
            **counts,
            "pm_anchors_rows": written,
            "withheld_tombstoned": withheld,
            # The producer's own absence signal, sized for PM's archive threshold
            # (power-map#490): assignment anchors whose row the pipeline no longer
            # publishes. Counted here because an empty column is otherwise silent —
            # and `span_key_unparseable` is counted BESIDE it, never inside it, so a
            # local key defect can never read as ordinary cutover absence (CR 13).
            "span_key_absent": span_key_counts["absent"],
            "span_key_unparseable": span_key_counts["unparseable"],
        }
    )


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
