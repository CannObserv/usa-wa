"""C4 committee lineage/lifecycle coherence invariants (usa-wa#124).

Read-only anti-drift backstop (the committee-lineage analog of #107
``operators.invariants``): a *missing* deactivation or succession attestation is silent —
a defunct committee stays ``active=true`` or keeps live members — so this asserts the
coherence the objective + judgment layers should produce, and exits 1 on any violation
(→ the ``OnFailure=`` handler emails the operator). App role, no writes.

Two invariants, both keyed on the ``active`` flag + the operator-attested links:

- **INV1 (assignment coherence):** no ``active=false`` committee Org carries a live
  (``is_active``, un-tombstoned) membership Assignment — a dissolved committee must not
  read as having current members.
- **INV2 (succession coherence):** a committee that is the *subject* of a non-superseded
  ``succeeded_by`` or ``merged_with`` link (it was succeeded / merged away) must be
  ``active=false`` — the predecessor is retired once its successor exists. ``split_from``
  does **not** constrain the subject (a split child stays live), so a split's two active
  heads are permitted (the OQ3 case).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.committee_succession import (
    SLUG_MERGED_WITH,
    SLUG_SUCCEEDED_BY,
    CommitteeSuccessionEvent,
)
from clearinghouse_domain_legislative.identity import Assignment, Organization, Role

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "committee-lineage-invariants"

_SOURCE = "usa_wa_legislature"
_ORG_TYPE = "committee"
#: The succession slugs that retire their subject (predecessor). ``split_from`` does not.
_RETIRING_SLUGS = (SLUG_SUCCEEDED_BY, SLUG_MERGED_WITH)


@dataclass
class LineageInvariantResult:
    """The check outcome. ``ok`` is the exit gate (0 iff True)."""

    #: (committee source_id, live-assignment count) for INV1 violations.
    inactive_with_live_members: list[tuple[str, int]] = field(default_factory=list)
    #: committee source_ids still active despite being a succeeded/merged predecessor (INV2).
    active_predecessors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.inactive_with_live_members and not self.active_predecessors


async def check_committee_lineage_invariants(
    session: AsyncSession,
) -> LineageInvariantResult:
    """Compute INV1 + INV2 violations (read-only)."""
    result = LineageInvariantResult()

    # INV1: inactive committee Orgs carrying a live membership Assignment.
    inv1 = (
        await session.execute(
            select(Organization.source_id, func.count(Assignment.id))
            .join(Role, Role.organization_id == Organization.id)
            .join(Assignment, Assignment.role_id == Role.id)
            .where(
                Organization.source == _SOURCE,
                Organization.org_type == _ORG_TYPE,
                Organization.active.is_(False),
                Assignment.is_active.is_(True),
                Assignment.deleted_at.is_(None),
                Assignment.archived_at.is_(None),
            )
            .group_by(Organization.source_id)
        )
    ).all()
    result.inactive_with_live_members = [(sid, n) for sid, n in inv1]

    # INV2: subjects of a non-superseded retiring link that are still active.
    active_ids = set(
        (
            await session.execute(
                select(Organization.source_id).where(
                    Organization.source == _SOURCE,
                    Organization.org_type == _ORG_TYPE,
                    Organization.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    retiring_subjects = set(
        (
            await session.execute(
                select(CommitteeSuccessionEvent.subject_source_id).where(
                    CommitteeSuccessionEvent.superseded_by_id.is_(None),
                    CommitteeSuccessionEvent.slug.in_(_RETIRING_SLUGS),
                )
            )
        )
        .scalars()
        .all()
    )
    result.active_predecessors = sorted(retiring_subjects & active_ids)
    return result


def _log(result: LineageInvariantResult) -> None:
    if result.ok:
        logger.info("committee_lineage_invariants_ok")
        return
    logger.error(
        "committee_lineage_invariants_violation",
        extra={
            "inactive_with_live_members": result.inactive_with_live_members,
            "active_predecessors": result.active_predecessors,
        },
    )


async def _invariants_job(ctx: JobContext) -> JobResult:
    """Harness handler: check, log, and map the outcome onto the ledger.

    A violation is ``failed`` (exit 1), not ``degraded``: the check ran fine and found
    real drift, which is what the daily unit's ``OnFailure=`` exists to email on.
    """
    result = await check_committee_lineage_invariants(ctx.require_session())
    _log(result)
    counters = {
        "inactive_with_live_members": result.inactive_with_live_members,
        "active_predecessors": result.active_predecessors,
    }
    return JobResult.ok(counters) if result.ok else JobResult.failed(counters)


def main(argv: list[str] | None = None) -> int:
    """Assert lineage coherence. Exit ``0`` clean · ``1`` drift · ``2`` config.

    Read-only, so ``commit=False``: there is nothing to commit, and the pre-#179b CLI
    committed nothing either.
    """
    return run_job(
        JOB_SLUG,
        _invariants_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.committees.lineage_invariants",
        description=("Assert the committee lineage/lifecycle coherence invariants (usa-wa#124)."),
        commit=False,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
