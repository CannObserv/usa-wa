"""The registrar (#308): matching proposals → registry writes.

    python -m usa_wa_pipeline.registrar [--db PATH] [--json]

Consumes the matching tier's ``proposed_links`` pairs from the built pipeline
duckdb (``USA_WA_PIPELINE_DB``), builds connected components (union-find), and
runs each cluster through the registry decision table
(:func:`clearinghouse_core.registry.decide`):

- all-new component → **mint** a fresh ULID;
- component touching exactly one registered entity → **append** the new keys;
- component touching ≥2 entities → **conflict**: no write, counted and logged —
  a triage item for :mod:`usa_wa_pipeline.adjudicate`, and an input to the
  publish gate (#311). The registry is sticky: a matching-rule change can
  re-propose the world and move nothing.

Runs after ``dbt build`` in the nightly chain; idempotent (a re-run of the
same proposals no-ops through the decision table).
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterable

import duckdb
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import KIND_PERSON, apply_decision, decide, registered_view

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "registrar"

_DEFAULT_DB = "data/pipeline.duckdb"


def cluster_pairs(pairs: Iterable[tuple[str, str]]) -> list[set[str]]:
    """Connected components over link pairs (union-find, path-halving)."""
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for left, right in pairs:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[left_root] = right_root

    components: dict[str, set[str]] = {}
    for key in parent:
        components.setdefault(find(key), set()).add(key)
    return list(components.values())


async def run_registrar(
    session: AsyncSession, kind: str, *, pairs: Iterable[tuple[str, str]]
) -> dict[str, int]:
    """Cluster the pairs and apply the decision table. Returns counters."""
    summary = {"clusters": 0, "minted": 0, "appended_clusters": 0, "noops": 0, "conflicts": 0}
    view = await registered_view(session, kind)
    for component in cluster_pairs(pairs):
        summary["clusters"] += 1
        decision = decide(frozenset(component), view)
        if decision.action == "conflict":
            summary["conflicts"] += 1
            logger.error(
                "registrar_conflict",
                extra={
                    "kind": kind,
                    "cluster": sorted(component),
                    "entities": sorted(decision.entity_ids),
                },
            )
            continue
        resolved = await apply_decision(session, kind, decision, registered_by="registrar")
        if decision.action == "mint":
            summary["minted"] += 1
        elif decision.action == "append":
            summary["appended_clusters"] += 1
        else:
            summary["noops"] += 1
        for key in decision.keys_to_register:
            view[key] = resolved
    logger.info("registrar_complete", extra={"kind": kind, **summary})
    return summary


def load_pairs(db_path: str) -> list[tuple[str, str]]:
    """Read the matching tier's pairs from the built pipeline database."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        return [
            (left, right)
            for left, right in con.execute(
                "select left_key, right_key from proposed_links"
            ).fetchall()
        ]
    finally:
        con.close()


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="Built pipeline duckdb (default: USA_WA_PIPELINE_DB, else data/pipeline.duckdb).",
    )


async def _registrar_job(ctx: JobContext) -> JobResult:
    db_path = ctx.args.db or os.environ.get("USA_WA_PIPELINE_DB", _DEFAULT_DB)
    pairs = load_pairs(db_path)
    summary = await run_registrar(ctx.require_session(), KIND_PERSON, pairs=pairs)
    if summary["conflicts"]:
        return JobResult.degraded(summary)
    return JobResult.ok(summary)


def main(argv: list[str] | None = None) -> int:
    """Apply matching proposals to the registry. Exit ``4`` = conflicts to triage."""
    return run_job(
        JOB_SLUG,
        _registrar_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.registrar",
        description="Cluster proposed_links and apply the registry decision table (#308).",
        extra_args=_add_args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
