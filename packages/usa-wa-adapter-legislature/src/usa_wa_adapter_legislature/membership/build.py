"""Phase B committee-membership span builder (#82) — archive → merged Assignment spans.

Reads every archived ``committee-members-hist:<biennium>:<id>:…`` roster **offline** (via
:class:`~usa_wa_adapter_legislature.membership.cohort.CommitteeMemberCohortProvider`,
no WSL re-pull), projects the rows to membership observations
(:mod:`membership.projector`), merges contiguous biennia into
:class:`~clearinghouse_domain_legislative.tenure_spans.TenureSpan`s, and emits one
:class:`Assignment` per committee tenure with a Citation per (biennium, committee) roster
(:mod:`membership.emit`).

Derives entirely from the local archive — re-runnable / re-tunable without touching WSL.
Depends on the Phase A harvest (:mod:`membership.harvest`) having archived the
rosters, and on the Persons (#77) + committee Orgs (sub-project 3) existing.

    python -m usa_wa_adapter_legislature.membership.build [--dry-run]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.operator_overlay import apply_operator_events, from_rows
from clearinghouse_domain_legislative.span_emit import (
    MAX_CLOSE_FRACTION_DEFAULT,
    SOURCE,
    SpanBuildResult,
    close_fraction,
    close_stale_spans,
)
from clearinghouse_domain_legislative.tenure_spans import build_tenure_spans
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.membership.cohort import CommitteeMemberCohortProvider
from usa_wa_adapter_legislature.membership.emit import emit_committee_spans
from usa_wa_adapter_legislature.membership.projector import (
    KIND_COMMITTEE,
    build_committee_membership_observations,
)
from usa_wa_adapter_legislature.operators.store import (
    cite_operator_events,
    current_events,
    get_or_create_operator_source,
)
from usa_wa_adapter_legislature.provisioning import get_or_create_source
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "wsl-committee-member-span-build"


async def build_committee_member_spans(
    session: AsyncSession,
    *,
    member_client: WSLClient | None = None,
    member_cohort: CommitteeMemberCohortProvider | None = None,
    current_biennium: str | None = None,
    restrict_to_biennium: str | None = None,
    max_close_fraction: float = MAX_CLOSE_FRACTION_DEFAULT,
) -> SpanBuildResult:
    """Build + emit merged committee-membership Assignment spans from the archive.

    ``current_biennium`` decides which spans stay open (defaults to the date-current one).
    ``member_cohort`` (#105 CR-1) is an optional shared, memoized cohort provider so a caller
    running several builders per cycle scans the archive once.

    ``restrict_to_biennium`` scopes the rebuild to the **(member, committee) pairs observed
    in that biennium's rosters** — the daily refresh passes the current biennium so it
    re-asserts only today's memberships (each with its *full* cross-biennium history), rather
    than rebuilding every member's whole committee archive every day. ``None`` (the harvest
    path) rebuilds all.

    Either way, memberships the rebuilt set no longer asserts are **closed** (#83,
    :func:`~clearinghouse_domain_legislative.span_emit.close_stale_spans`) — a member who left the
    committee (or the legislature) must not keep an ``is_active`` row forever."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())

    provider = member_cohort or CommitteeMemberCohortProvider(
        member_client or WSLClient("CommitteeService"), session=session, source_id=source.id
    )
    rosters = await provider.archived_rosters()
    if not rosters:
        logger.warning("committee_member_span_build_no_archive")
        return SpanBuildResult(emitted=0)

    observations = build_committee_membership_observations(rosters)
    if restrict_to_biennium is not None:
        scoped = {
            (o.member_id, o.discriminator)
            for o in observations
            if o.biennium == restrict_to_biennium
        }
        observations = [o for o in observations if (o.member_id, o.discriminator) in scoped]

    built_spans = build_tenure_spans(observations, current_biennium=current)
    # Operator-succession overlay (#107): departed closes every committee membership at the death
    # date; vacated/seated adjust one committee tenure. Synthesized spans skip the roster citation.
    event_rows = list(await current_events(session))
    events = from_rows(event_rows)
    spans = apply_operator_events(
        built_spans, events, current_biennium=current, owned_kinds={KIND_COMMITTEE}
    )
    synthesized_ids = {s.source_id for s in spans} - {s.source_id for s in built_spans}
    fetch_events = await provider.fetch_event_map()
    emitted = await emit_committee_spans(
        session,
        spans,
        reliability=source.reliability,
        fetch_events=fetch_events,
        skip_citation_ids=synthesized_ids,
    )
    if event_rows:
        operator_source = await get_or_create_operator_source(session, jurisdiction)
        await cite_operator_events(
            session,
            event_rows,
            spans,
            owned_kinds={KIND_COMMITTEE},
            assignment_source=SOURCE,
            confidence=operator_source.reliability,
        )
    sweep = await close_stale_spans(
        session,
        assignment_source=SOURCE,
        kinds={KIND_COMMITTEE},
        asserted_source_ids={s.source_id for s in spans},
        current_biennium=current,
        max_close_fraction=max_close_fraction,
    )
    logger.info(
        "committee_member_span_build_complete",
        extra={
            "rosters": len(rosters),
            "spans": len(spans),
            "emitted": emitted,
            "operator_events": len(events),
            "closed_stale": sweep.closed,
            "sweep_aborted": sweep.aborted,
            "restricted": restrict_to_biennium,
        },
    )
    return SpanBuildResult(emitted=emitted, closed_stale=sweep.closed, sweep_aborted=sweep.aborted)


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the builder's own guard flag to the harness's shared parser."""
    parser.add_argument(
        "--max-close-fraction",
        type=close_fraction,
        default=MAX_CLOSE_FRACTION_DEFAULT,
        help="mass-close guard ceiling in (0, 1] (#83); 1.0 disables the guard for a "
        "deliberate mass close (e.g. a wholesale WSL committee-Id re-key)",
    )


async def _build_job(ctx: JobContext):
    """Harness handler: build the spans and hand the result back as counters."""
    return await build_committee_member_spans(
        ctx.require_session(), max_close_fraction=ctx.args.max_close_fraction
    )


def main(argv: list[str] | None = None) -> int:
    """Run the Phase B membership span build. Exit ``0`` clean · ``1`` failed · ``2`` config."""
    return run_job(
        JOB_SLUG,
        _build_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.membership.build",
        description="Build merged committee-membership spans from the roster archive (#82).",
        extra_args=_add_args,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
