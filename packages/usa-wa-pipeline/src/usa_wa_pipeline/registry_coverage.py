"""Registry coverage probe (#412 PR B): the registry binds every identity the build derives.

    python -m usa_wa_pipeline.registry_coverage [--db PATH]

Write-free. Rebuilds both span families from the built duckdb's own staging
tables (the inputs the ``assignments`` model read) plus the curated operator
events, then joins them against the registry **as the registrar has just left
it**, and gates three counters at zero:

- ``unregistered_spans`` — spans whose member has no person key. The
  ``assignments`` model inner-joins the crosswalk, so these drop from the
  published table silently; nothing in the build can see them (#403).
- ``unregistered_orgs`` / ``unregistered_roles`` — role-dimension rows whose
  org or role key is unbound. ``roles.org_entity_id`` is untested and
  ``roles.entity_id`` is only ``unique``, so a null publishes.

**Split out of ``parity_spans``,** which compares against the canonical oracle
and retires with it in PR E. These three never needed that oracle, and they are
the only nightly alarm for data silently falling out of the published tables.

**Post-registrar, never in-build.** The nightly runs ``dbt build → registrar →
publish``: a new legislator, seat or committee is unregistered in the first
build that sees it, by design, and bound by the registrar straight after. As a
dbt test this would fail that build, the registrar would never run, and every
night after would fail the same way. Here it runs where the parity probes run,
so that one-run lag reads as zero and only a gap the registrar did NOT close
alarms — which is the state tomorrow's build publishes from.

``seat_overlaps_unclipped`` (#360) rides along, reported and not gated: the
``assignments`` model computes it on every build and a dbt model cannot report.

Exit ``0`` clean · ``1`` any gated counter nonzero · ``4`` degraded — the build
holds no sponsors, no roster rows, or sponsors with no ballot rows. Each would
read as zero unregistered identities having compared nothing.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from typing import Any

import duckdb

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import KIND_ORG, KIND_PERSON, KIND_ROLE
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_pipeline.conformed.roles import SOURCE as ROLE_SOURCE
from usa_wa_pipeline.conformed.roles import role_rows
from usa_wa_pipeline.conformed.spans import (
    SpanInputs,
    assignment_rows,
    build_families,
    entity_index,
)
from usa_wa_pipeline.operator_read import operator_event_rows
from usa_wa_pipeline.registry_read import crosswalk_rows

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "registry-coverage"

_DEFAULT_DB = "data/pipeline.duckdb"

#: Counters that must be ZERO. The exit code is the alert (#49): a counter that
#: only reaches journald tells nobody.
GATED_COUNTERS = ("unregistered_spans", "unregistered_orgs", "unregistered_roles")

#: ``SpanInputs`` field → the staging model it is read from: the same tables the
#: ``assignments`` model refs, so the probe rebuilds what the build built.
STAGING_INPUTS = {
    "sponsors": "stg_wsl_sponsors",
    "committee_members": "stg_wsl_committee_members",
    "roster": "stg_roster_members",
    "sos_results": "stg_sos_results",
}

#: How many unbound keys of each kind a report names.
_SAMPLE = 20


def load_staging(db_path: str) -> dict[str, list[dict[str, Any]]]:
    """The span build's staging inputs, read back from the built duckdb.

    Converted exactly as the ``assignments`` model converts them
    (``.df().to_dict("records")``), so a null reaches the builders in the shape
    it reached them during the build.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        return {
            field: con.table(model).df().to_dict("records")
            for field, model in STAGING_INPUTS.items()
        }
    finally:
        con.close()


def coverage_counters(
    families: dict[str, list[TenureSpan]],
    *,
    persons: dict[str, str],
    orgs: dict[str, str],
    roles: dict[str, str],
) -> dict[str, Any]:
    """Both families ⨝ the three crosswalks → the counters, plus a sample of each
    unbound key so the report says what to register or adjudicate.

    The roles are derived from every span, not only the registered ones —
    ``parity_spans``' rule (CR 86): from the joined rows, a person gap would hide
    the roles only that person's spans name.
    """
    _rows, join = assignment_rows(families, persons)
    derived_roles, role_join = role_rows(
        [
            {"span_kind": span.kind, "span_discriminator": span.discriminator}
            for family in families.values()
            for span in family
        ],
        orgs,
        roles,
    )
    return {
        "spans": join["spans"],
        "registered_spans": join["published"],
        "unregistered_spans": join["unregistered_spans"],
        "seat_overlaps_unclipped": join["seat_overlaps_unclipped"],
        "roles": role_join["roles"],
        "unregistered_orgs": role_join["unregistered_orgs"],
        "unregistered_roles": role_join["unregistered_roles"],
        "unregistered_sample": {
            "persons": sorted(
                {
                    f"{source}:{span.member_id}"
                    for source, family in families.items()
                    for span in family
                    if f"{source}:{span.member_id}" not in persons
                }
            )[:_SAMPLE],
            "orgs": sorted(
                {
                    f"{ROLE_SOURCE}:{row['org_source_id']}"
                    for row in derived_roles
                    if row["org_entity_id"] is None
                }
            )[:_SAMPLE],
            "roles": sorted(
                f"{ROLE_SOURCE}:{row['role_key']}"
                for row in derived_roles
                if row["entity_id"] is None
            )[:_SAMPLE],
        },
    }


def run_coverage(
    staging: dict[str, list[dict[str, Any]]],
    *,
    events: list[Any],
    persons: dict[str, str],
    orgs: dict[str, str],
    roles: dict[str, str],
    current_biennium: str,
) -> JobResult:
    """Rebuild the families and gate the join. Pure: the job does the reads."""
    # Each guard names what is missing (CR 69's rule): the builders would either
    # raise through the harness, losing the counters, or build a family short
    # and report the missing identities as none.
    if not staging["sponsors"]:
        logger.warning("registry_coverage_empty_sponsors")
        return JobResult.degraded({"empty_sponsors": True})
    if not staging["roster"]:
        logger.warning("registry_coverage_empty_roster_rows")
        return JobResult.degraded({"empty_roster_rows": True})
    if not staging["sos_results"]:
        logger.warning("registry_coverage_empty_sos_rows")
        return JobResult.degraded({"empty_sos_rows": True})

    families = build_families(
        SpanInputs(**staging, events=events), current_biennium=current_biennium
    )
    counters = coverage_counters(families, persons=persons, orgs=orgs, roles=roles)
    counters["integrity_failures"] = [name for name in GATED_COUNTERS if counters[name]]
    clean = not counters["integrity_failures"]
    (logger.info if clean else logger.error)(
        "registry_coverage_report", extra={"summary": counters}
    )
    if not clean:
        return JobResult.failed(counters, exit_code=1)
    return JobResult.ok(counters)


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="Built pipeline duckdb (default: USA_WA_PIPELINE_DB, else data/pipeline.duckdb).",
    )


async def _coverage_job(ctx: JobContext) -> JobResult:
    """Read the build and the registry, then gate."""
    session = ctx.require_session()
    db_path = ctx.args.db or os.environ.get("USA_WA_PIPELINE_DB", _DEFAULT_DB)
    return run_coverage(
        load_staging(db_path),
        events=await operator_event_rows(session),
        persons=entity_index(await crosswalk_rows(session, KIND_PERSON)),
        orgs=entity_index(await crosswalk_rows(session, KIND_ORG)),
        roles=entity_index(await crosswalk_rows(session, KIND_ROLE)),
        current_biennium=(
            os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """Gate the registry's coverage of the build at zero unbound identities."""
    return run_job(
        JOB_SLUG,
        _coverage_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.registry_coverage",
        description="Write-free: the registry binds every person, org and role the build derives.",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
