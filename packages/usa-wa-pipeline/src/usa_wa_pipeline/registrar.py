"""The registrar (#308): matching proposals → registry writes.

    python -m usa_wa_pipeline.registrar [--db PATH] [--json]

Consumes the matching tier's ``proposed_links`` pairs from the built pipeline
duckdb (``USA_WA_PIPELINE_DB``) plus a singleton pair per staged WSL sponsor
(#403), builds connected components (union-find), and runs each cluster
through the registry decision table
(:func:`clearinghouse_core.registry.decide`):

- all-new component → **mint** a fresh ULID;
- component touching exactly one registered entity → **append** the new keys;
- component touching ≥2 entities → **conflict**: no write, counted and logged —
  a triage item for :mod:`usa_wa_pipeline.adjudicate`, and an input to the
  publish gate (#311). The registry is sticky: a matching-rule change can
  re-propose the world and move nothing.

Orgs and roles have no matching problem, so they register from the built
models as singleton clusters instead — mint once, no-op every run after.

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
    KIND_ORG,
    KIND_PERSON,
    KIND_ROLE,
    apply_decision,
    decide,
    registered_view,
)
from usa_wa_common.orgs import STRUCTURAL_ORGS
from usa_wa_pipeline.conformed.roles import SOURCE as ROLE_SOURCE

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "registrar"

_DEFAULT_DB = "data/pipeline.duckdb"

#: The source every org key is asserted under — committee ids and the
#: structural orgs alike, the namespace the seed carried across from canonical.
ORG_SOURCE = "usa_wa_legislature"

#: The source a WSL sponsor's person key is asserted under — the right-hand key
#: of every matching rule, so a sponsor's singleton lands in its pair's cluster.
PERSON_SOURCE = "usa_wa_legislature"


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


def singleton_pairs(natural_keys: Iterable[str]) -> list[tuple[str, str]]:
    """Natural keys → singleton clusters for :func:`run_registrar` (roles #313, orgs).

    A role or an org has no matching problem, so every cluster is one key
    paired with itself: the decision table then only ever mints (new seat, new
    committee) or no-ops (every subsequent run). Reusing that table rather than
    writing a second registration path is the point — one ledger, one set of
    rules. WSL sponsors (#403) ride the same pairs beside the matched ones,
    where union-find folds a paired sponsor's singleton into its component.
    """
    return [(key, key) for key in natural_keys]


def load_sponsor_keys(db_path: str) -> list[str]:
    """Person natural keys for every staged WSL sponsor, one per member id.

    ``proposed_links`` holds only matched pairs, so before #403 a legislator no
    rule paired — an appointee with no ``stg_pdc_winners`` row, a member newer
    than the roster PDF's last edition — never reached the registrar: 111 of
    640 sponsors on 2026-09-22, registered only by the one-shot seed.

    WSL sponsors only (decided 2026-09-23). A member id is a numeric upstream
    id and covers every future legislator. A roster key is built by us from a
    name, so after a parser or fold change a roster-only person must surface as
    ``missing`` and be adjudicated, never minted and published as a duplicate;
    a PDC id is a crosswalk key riding a WSL person's pair. Both stay
    pair-only.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute(
            "select distinct cast(member_id as varchar) from stg_wsl_sponsors "
            "where member_id is not null"
        ).fetchall()
    finally:
        con.close()
    return [f"{PERSON_SOURCE}:{row[0]}" for row in sorted(rows)]


def load_org_keys(db_path: str) -> list[str]:
    """Org natural keys: every committee staging attests, plus the structural orgs.

    The same universe the canonical tier holds (220/220 on 2026-09-22):
    CommitteeService committees, the Joint/``Other`` bodies only a meeting ref
    carries, and the synthesized legislature/chambers/parties no wire carries
    at all. Before this pass existed only the one-shot seed registered orgs, so
    the first committee born after it — Joint committee 36500, 2026-09-22 —
    stayed unregistered and failed ``parity-registry``.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute(
            "select committee_id from stg_wsl_committees "
            "union select committee_id from stg_wsl_meetings"
        ).fetchall()
    finally:
        con.close()
    source_ids = {str(row[0]) for row in rows if row[0] is not None} | set(STRUCTURAL_ORGS)
    return [f"{ORG_SOURCE}:{source_id}" for source_id in sorted(source_ids)]


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
    # Every staged WSL sponsor rides in as a singleton beside the matched pairs
    # (#403): a legislator no rule pairs still registers, and a paired one's
    # singleton joins its component — the clusters are otherwise unchanged.
    pairs = load_pairs(db_path) + singleton_pairs(load_sponsor_keys(db_path))
    summary = await run_registrar(session, KIND_PERSON, pairs=pairs)
    # Orgs and roles (#313) register from the built models rather than
    # proposed_links — see `load_org_keys` / `load_role_keys`. Counters are
    # namespaced so an org or role mint is never read as a person mint;
    # conflicts fold into the one triage signal.
    for prefix, kind, keys in (
        ("org", KIND_ORG, load_org_keys(db_path)),
        ("role", KIND_ROLE, load_role_keys(db_path)),
    ):
        kind_summary = await run_registrar(session, kind, pairs=singleton_pairs(keys))
        summary.update({f"{prefix}_{name}": value for name, value in kind_summary.items()})
        summary["conflicts"] += kind_summary["conflicts"]
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
        description=(
            "Cluster proposed_links + WSL sponsor singletons and apply the registry "
            "decision table (#308, #403)."
        ),
        extra_args=_add_args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
