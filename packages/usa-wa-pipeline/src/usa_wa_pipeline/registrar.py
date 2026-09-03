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
from clearinghouse_core.registry import (
    KIND_PERSON,
    KIND_ROLE,
    apply_decision,
    decide,
    registered_view,
)
from usa_wa_pipeline.conformed.roles import SOURCE as ROLE_SOURCE

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


def load_pairs(db_path: str, kind: str = KIND_PERSON) -> list[tuple[str, str]]:
    """Read one kind's pairs from the built pipeline database.

    Filtered on ``kind`` (#302 CR): the registrar registers each component
    under one entity kind, so an org rule unioned into ``proposed_links``
    must never reach the person registration path."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        return [
            (left, right)
            for left, right in con.execute(
                "select left_key, right_key from proposed_links where kind = ?", [kind]
            ).fetchall()
        ]
    finally:
        con.close()


def role_pairs(natural_keys: Iterable[str]) -> list[tuple[str, str]]:
    """Role natural keys → singleton clusters for :func:`run_registrar` (#313).

    A role has no matching problem, so every cluster is one key paired with
    itself: the decision table then only ever mints (new seat) or no-ops
    (every subsequent run). Reusing that table rather than writing a second
    registration path is the point — one ledger, one set of rules.
    """
    return [(key, key) for key in natural_keys]


def load_role_keys(db_path: str) -> list[str]:
    """Role natural keys from the built pipeline database's role dimension.

    Roles do not come from ``proposed_links``: nothing proposes them, because
    ``role_for_span`` is a pure function of the seat. The conformed ``roles``
    model IS the set of slots that exist, and the natural key is
    ``<source>:<role_key>`` — the same shape persons and orgs use, so the
    ULID-preserving seed and this ongoing pass address identical rows.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute("select distinct role_key from roles order by 1").fetchall()
    finally:
        con.close()
    return [f"{ROLE_SOURCE}:{row[0]}" for row in rows]


def unprocessed_kinds(db_path: str) -> list[str]:
    """Kinds present in ``proposed_links`` that no registration pass consumes.

    The matching tier may legally emit kinds this job does not yet register
    (an org rule, say) — but their pairs must never vanish SILENTLY (CR 40):
    the job degrades and names them, so wiring the pass is forced the day the
    rule lands."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute("select distinct kind from proposed_links").fetchall()
        kinds = {row[0] for row in rows}
    finally:
        con.close()
    # A NULL kind is itself unprocessed, and must not crash the sort (CR 53):
    # the schema's not_null test guards the nightly only by ordering.
    unprocessed = {"<null>" if kind is None else kind for kind in kinds} - {KIND_PERSON}
    return sorted(unprocessed)


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="Built pipeline duckdb (default: USA_WA_PIPELINE_DB, else data/pipeline.duckdb).",
    )


async def _registrar_job(ctx: JobContext) -> JobResult:
    db_path = ctx.args.db or os.environ.get("USA_WA_PIPELINE_DB", _DEFAULT_DB)
    session = ctx.require_session()
    pairs = load_pairs(db_path)
    summary = await run_registrar(session, KIND_PERSON, pairs=pairs)
    # Roles (#313), from the conformed dimension rather than proposed_links —
    # see `load_role_keys`. Counters are namespaced so a role mint is never
    # read as a person mint; conflicts fold into the one triage signal.
    role_summary = await run_registrar(
        session, KIND_ROLE, pairs=role_pairs(load_role_keys(db_path))
    )
    summary.update({f"role_{name}": value for name, value in role_summary.items()})
    summary["conflicts"] += role_summary["conflicts"]
    skipped_kinds = unprocessed_kinds(db_path)
    if skipped_kinds:
        summary["unprocessed_kinds"] = skipped_kinds
        logger.error("registrar_unprocessed_kinds", extra={"kinds": skipped_kinds})
    if summary["conflicts"] or skipped_kinds:
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
