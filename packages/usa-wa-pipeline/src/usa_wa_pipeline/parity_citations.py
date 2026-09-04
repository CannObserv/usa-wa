"""Citations coverage probe (#313): is every published entity still citable?

    python -m usa_wa_pipeline.parity_citations [--db PATH] [--json]

Write-free, and deliberately asked of the **built artifact** rather than by
recomputing the join: the tables are what a consumer reads, so a binder that
dropped an input — the failure mode a pure-function test cannot see — shows up
here and nowhere else. It is also cheap, four aggregate queries over a duckdb
already on disk, which is why it can run every night.

The nightly's other probes compare us against the Postgres tier. This one has
no counterpart to compare against: the Postgres ``Citation`` chain is what it
REPLACES. So it checks the property that chain guaranteed structurally and this
one has to earn — that following a citation lands on bytes, and that nothing
published is unreachable from a citation.

**Gated at zero** (:data:`INTEGRITY_COUNTERS`): a citation naming a resource
``stg_raw_fetches`` does not carry, and any uncited assignment, *registered*
role or sourced organization. **Ratcheted** against a documented baseline:
uncited persons — see :data:`BASELINE_UNCITED_PERSONS`. **Counted only**:
structural organizations, which are definitional and uncitable by construction,
and unregistered roles, which are one build behind the registrar by design.

Exit ``0`` clean · ``1`` a gate fired or the ratchet moved.
"""

from __future__ import annotations

import argparse
import os

import duckdb

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_common.orgs import STRUCTURAL_ORGS

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "parity-citations"

_DEFAULT_DB = "data/pipeline.duckdb"

#: Every table the probe reads. Named so a missing one is a refusal rather than
#: an empty result reading as "nothing uncited" — the one shape a coverage
#: probe must never produce.
REQUIRED_TABLES = (
    "citations",
    "stg_raw_fetches",
    "persons",
    "organizations",
    "roles",
    "assignments",
    "org_crosswalk",
)

#: Counters that must be zero. Each is an integrity break, not a coverage
#: shortfall: a dangling citation, or a published row nothing attests.
#:
#: ``unregistered_roles`` is deliberately NOT here (CR 98). ``roles.entity_id``
#: is null for exactly one build — the nightly runs ``dbt build → registrar →
#: publish``, so a brand-new seat is unregistered in the build that first sees
#: it and bound by the next, which is why ``conformed/schema.yml`` gives that
#: column only a ``unique`` test. Gating it at zero would fail the nightly and
#: email the operator every time a committee is created. The PERSISTENT case —
#: a role the registrar never binds — is already caught by ``parity_spans``,
#: which re-reads the registry rather than the build.
INTEGRITY_COUNTERS = (
    "orphan_citations",
    "uncited_assignments",
    "uncited_roles",
    "uncited_organizations",
)

#: Persons no staging row names, measured 2026-09-04 against the live corpus:
#:
#: - ``usa_wa_legislature:31656`` — registered, but no sponsor or committee
#:   wire in the archive names them;
#: - the two **Elmer E. Johnston** entities (1899 and 1947), which share the
#:   fold ``elmerejohnston``. The citer refuses an ambiguous fold rather than
#:   attributing one man's career to the other — the Jr/Sr guard doing its job.
#:
#: A ceiling, not an expectation: citing more than this is progress. Lower it
#: when the causes are fixed; never raise it without naming the new one.
BASELINE_UNCITED_PERSONS = 3


class ArtifactMissing(RuntimeError):
    """The built pipeline database is missing a table this probe must read."""


def _tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {row[0] for row in con.execute("show tables").fetchall()}


def audit(db_path: str) -> tuple[dict[str, int], list[str]]:
    """``(counters, failures)`` — the names of the checks that did not hold."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        if missing := sorted(set(REQUIRED_TABLES) - _tables(con)):
            raise ArtifactMissing(
                f"the built pipeline database is missing {missing}; a coverage probe over an "
                "unbuilt artifact would report perfect coverage of nothing"
            )
        structural = list(STRUCTURAL_ORGS)
        counters = {
            "citations": con.execute("select count(*) from citations").fetchone()[0],
            "attestations": con.execute("select count(*) from stg_raw_fetches").fetchone()[0],
            "orphan_citations": con.execute(
                "select count(*) from citations c "
                "left join stg_raw_fetches f using (source, resource_id) where f.sha256 is null"
            ).fetchone()[0],
            "uncited_persons": con.execute(
                "select count(*) from persons p where not exists "
                "(select 1 from citations c where c.entity_type = 'person' "
                "and c.entity_id = p.entity_id)"
            ).fetchone()[0],
            # Registered roles only: a null `entity_id` has nothing to cite BY,
            # so it is a registration gap rather than a coverage one (CR 98).
            "unregistered_roles": con.execute(
                "select count(*) from roles where entity_id is null"
            ).fetchone()[0],
            "uncited_roles": con.execute(
                "select count(*) from roles r where r.entity_id is not null "
                "and not exists (select 1 from citations c where c.entity_type = 'role' "
                "and c.entity_id = r.entity_id)"
            ).fetchone()[0],
            # The span's published identity is its 4-part source_id, reassembled
            # exactly as the artifact spells it.
            "uncited_assignments": con.execute(
                "select count(*) from assignments a where not exists "
                "(select 1 from citations c where c.entity_type = 'assignment' and c.entity_id = "
                "a.member_id || ':' || a.span_kind || ':' || a.span_discriminator || ':' "
                "|| a.span_start_biennium)"
            ).fetchone()[0],
            # `unnest(?)` binds the whole vocabulary as ONE list parameter, so
            # the SQL is a constant string — no placeholder arithmetic, and
            # nothing for a growing STRUCTURAL_ORGS to get wrong.
            "structural_organizations": con.execute(
                "select count(distinct entity_id) from org_crosswalk "
                "where key_value in (select unnest(?))",
                [structural],
            ).fetchone()[0],
            "uncited_organizations": con.execute(
                "select count(*) from organizations o where not exists "
                "(select 1 from citations c where c.entity_type = 'organization' "
                "and c.entity_id = o.entity_id) and not exists "
                "(select 1 from org_crosswalk x where x.entity_id = o.entity_id "
                "and x.key_value in (select unnest(?)))",
                [structural],
            ).fetchone()[0],
        }
    finally:
        con.close()

    failures = [name for name in INTEGRITY_COUNTERS if counters[name]]
    if counters["uncited_persons"] > BASELINE_UNCITED_PERSONS:
        failures.append("uncited_persons")
    return counters, failures


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="Built pipeline duckdb (default: USA_WA_PIPELINE_DB, else data/pipeline.duckdb).",
    )


async def _parity_job(ctx: JobContext) -> JobResult:
    db_path = ctx.args.db or os.environ.get("USA_WA_PIPELINE_DB", _DEFAULT_DB)
    counters, failures = audit(db_path)
    if failures:
        # Name which check moved: "citations diverged" sends an operator to read
        # seven numbers, and the one that moved is the whole message.
        logger.error("parity_citations_divergence", extra={**counters, "failures": failures})
        return JobResult.failed({**counters, "failures": failures}, exit_code=1)
    logger.info("parity_citations_clean", extra=dict(counters))
    return JobResult.ok(counters)


def main(argv: list[str] | None = None) -> int:
    """Verify the citations artifact covers what was published. Exit ``1`` = a gap."""
    return run_job(
        JOB_SLUG,
        _parity_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.parity_citations",
        description="Write-free coverage probe over the published citations chain (#313).",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
