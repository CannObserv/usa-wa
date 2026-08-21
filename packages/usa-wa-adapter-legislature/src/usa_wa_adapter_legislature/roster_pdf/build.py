"""Phase B pre-1991 builder (#228, epic #219 Phase 3b) — the write side, oracle-gated.

    python -m usa_wa_adapter_legislature.roster_pdf.build [--dry-run]

Archive → identities → Persons + spans, in one gated pass:

1. Re-parse the archived edition offline (:class:`RosterCohortProvider` — never a re-pull).
2. Resolve identities against the WSL seating index (:mod:`identity`).
3. **Run the acceptance oracle** (:func:`verify_pre1991`) *before any write*: the partition
   must be exact (every pre-1991 record in exactly one identity or refusal), the party
   vocabulary must recognize every token, and no member may cover two Senate seats in one
   **session year** (the person-side simultaneity nothing downstream checks — spec oracle
   item 3; the roster indexes rows by term-start year, so the year is the granularity the
   check has evidence for).
   A violation aborts; refusals and declines are tallied outcomes, never aborts.
4. Mint roster Persons for the minted identities (:mod:`persons`).
5. Emit the minted identities' spans in the roster source space (:mod:`emit`).
6. **Deepen** the WSL-joined identities' spans through the sponsor builder
   (``build_spans(extra_observations=…)``): a crossing member's tenure emits as one span
   keyed at its roster-era start, pre-archive bienniums citing the roster edition. Skipped
   with a warning when no sponsor archive exists (a fresh database).

**Deploy sidecar-paused**, like every anchor-moving operation: deepening re-keys existing
spans' ``source_id`` to earlier start-bienniums, stranding the shipped 1991-start rows —
run ``sponsors.migrate_spans`` (the #97 collapse) in the same window to transfer their PM
anchors onto the deepened spans, then resume. The full sequence lives in
``docs/COMMANDS-BACKFILL.md``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.span_emit import retire_unasserted_spans
from clearinghouse_domain_legislative.span_kinds import KIND_PARTY, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import build_tenure_spans
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.provisioning import get_or_create_source as get_or_create_wsl
from usa_wa_adapter_legislature.roster_pdf.backfill import load_seatings
from usa_wa_adapter_legislature.roster_pdf.cohort import RosterCohortProvider
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.emit import emit_roster_spans
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_WSL,
    IdentityReport,
    RosterIdentity,
    resolve_identities,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.persons import (
    mint_roster_persons,
    retire_unasserted_roster_persons,
)
from usa_wa_adapter_legislature.roster_pdf.projector import build_pre1991_observations
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source
from usa_wa_adapter_legislature.sponsors.build import build_spans
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "roster-pre1991-build"


class OracleViolation(RuntimeError):
    """A hard acceptance-oracle failure — the build must not write."""


@dataclass(frozen=True)
class Pre1991BuildSummary:
    """What the build did, and every tallied residue — report-don't-drop."""

    records_pre1991: int
    identities_minted: int
    identities_joined: int
    refusals: dict[str, int]
    persons_created: int
    persons_existing: int
    persons_renamed: int
    persons_retired: int
    persons_retired_anchored: int
    assignments_emitted: int
    spans_retired: int
    spans_retired_anchored: int
    retire_aborted: bool
    deepened_emitted: int
    #: The deepening build's #83 stale sweep — deepening re-keys a joined member's span to
    #: its roster-era start, so the shipped 1991-start row goes stale here. ``sweep_aborted``
    #: means the mass-close guard tripped and those rows are still open (CR #84).
    spans_closed: int
    sweep_aborted: bool
    declined_parties: int
    uncovered_rows: int
    seat_overlaps: int
    counters: dict[str, int] = field(default_factory=dict)


def verify_pre1991(
    identities: Sequence[RosterIdentity],
    pre_records: Iterable[RosterRecord],
    *,
    refused_records: int = 0,
) -> None:
    """The hard half of the acceptance oracle. Raises :class:`OracleViolation`.

    * **Partition exactness** (item 1): identities plus refusals account for every pre-1991
      record — zero silent drops, zero double counting.
    * **Person-side Senate simultaneity** (item 3): no member covers two Senate seats in one
      *session year*. The seat side is the projector's reported overlaps (same-biennium
      handoffs are legitimate); the person side is corrupt data nothing downstream checks —
      a member listed under two LDs across a redistricting boundary would trip it.

    Refusals, declines and uncovered rows are *tallied* outcomes the summary reports; they
    never abort.
    """
    total = sum(1 for _ in pre_records)
    placed = sum(len(i.records) for i in identities) + refused_records
    if placed != total:
        raise OracleViolation(
            f"partition mismatch: {total} pre-1991 records, {placed} placed "
            "(identities + refusals) — a record was dropped or double-counted"
        )
    for identity in identities:
        member = identity.wsl_member_id or identity.key or identity.fold
        seats_by_year: dict[int, set[int]] = defaultdict(set)
        for record in identity.records:
            if record.chamber == "senate":
                seats_by_year[record.year].add(record.district)
        doubled = {year: lds for year, lds in seats_by_year.items() if len(lds) > 1}
        if doubled:
            raise OracleViolation(
                f"person-side Senate simultaneity: {member} ({identity.fold}) listed on "
                f"multiple Senate seats in one session year: {sorted(doubled.items())}"
            )


def _refusal_counts(report: IdentityReport) -> dict[str, int]:
    counts: Counter = Counter()
    for refusal in report.refused:
        counts[refusal.reason] += 1
    return dict(counts)


def _counters(summary: Pre1991BuildSummary) -> dict[str, int]:
    """Every scalar tally on the summary, plus one counter per refusal reason.

    **Derived, never mirrored** (CR #96): ``counters`` is what the #178 ledger persists, so
    a hand-written dict beside the dataclass would eventually fall behind it — a field added
    to one and forgotten in the other is a residue nobody can trend. Bools flatten to 0/1.

    Refusal reasons become *log keys* (the completion log passes this dict as ``extra``), so
    a new reason must not collide with a reserved ``LogRecord`` attribute — hence the
    ``refusals_`` prefix (CR #97; the ``created`` collision is the precedent).
    """
    out = {
        name: int(value) for name, value in asdict(summary).items() if isinstance(value, int | bool)
    }
    out.update({f"refusals_{reason}": count for reason, count in summary.refusals.items()})
    return out


async def build_pre1991(
    session: AsyncSession, *, current_biennium: str | None = None
) -> Pre1991BuildSummary:
    """Parse → resolve → oracle → mint → emit → deepen. Idempotent by natural keys."""
    jurisdiction = await resolve_jurisdiction(session)
    roster_source = await get_or_create_roster_source(session, jurisdiction)
    wsl_source = await get_or_create_wsl(session, jurisdiction)
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())

    provider = RosterCohortProvider(session=session, source_id=roster_source.id)
    records = await provider.records()
    citation = await provider.citation_event()
    if not records or citation is None:
        logger.warning("pre1991_build_no_archive")
        return Pre1991BuildSummary(
            records_pre1991=0,
            identities_minted=0,
            identities_joined=0,
            refusals={},
            persons_created=0,
            persons_existing=0,
            persons_renamed=0,
            persons_retired=0,
            persons_retired_anchored=0,
            assignments_emitted=0,
            spans_retired=0,
            spans_retired_anchored=0,
            retire_aborted=False,
            deepened_emitted=0,
            spans_closed=0,
            sweep_aborted=False,
            declined_parties=0,
            uncovered_rows=0,
            seat_overlaps=0,
        )

    seatings = await load_seatings(session, source_id=wsl_source.id)
    report = resolve_identities(records, seatings=seatings)
    pre = [r for r in records if r.year < 1991]
    verify_pre1991(
        report.identities,
        pre,
        refused_records=sum(len(ref.records) for ref in report.refused),
    )
    for refusal in report.refused:
        logger.info(
            "pre1991_identity_refused",
            extra={"reason": refusal.reason, "fold": refusal.fold, "detail": refusal.detail},
        )

    projection = build_pre1991_observations(report.identities, records)
    if projection.unrecognized_parties:
        raise OracleViolation(
            f"unrecognized party tokens: {dict(projection.unrecognized_parties)} — a new "
            "edition introduced an abbreviation nobody has classified"
        )
    for decline in projection.declined_parties:
        logger.info(
            "pre1991_party_declined",
            extra={"member": decline.member, "token": decline.token, "reason": decline.reason},
        )

    minted = await mint_roster_persons(session, report.identities)
    # Retire roster Persons this derivation no longer mints (#259). A fold the boundary
    # probe now JOINS was minted by an earlier run, and minting alone never revisits it —
    # the stale row would go to PM as the duplicate the join exists to prevent.
    retired_persons = await retire_unasserted_roster_persons(
        session,
        asserted_keys={i.key for i in report.identities if i.key is not None},
    )
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=current, jurisdiction_id=jurisdiction.id
    )

    joined_members = {i.wsl_member_id for i in report.identities if i.disposition == IDENTITY_WSL}
    minted_obs = [o for o in projection.observations if o.member_id not in joined_members]
    joined_obs = [o for o in projection.observations if o.member_id in joined_members]

    minted_spans = build_tenure_spans(minted_obs, current_biennium=current)
    emitted = await emit_roster_spans(
        session,
        minted_spans,
        anchors=anchors,
        reliability=roster_source.reliability,
        citation=citation,
    )

    # Retire roster spans this derivation no longer asserts (CR #86): the pre-1991 rows are
    # emitted closed, so the #83 open-row sweep can never see one go stale — what strands
    # them is a re-derivation moving an identity key, and the row must leave live reads and
    # sync rather than linger under its old shape.
    retire = await retire_unasserted_spans(
        session,
        assignment_source=ROSTER_SOURCE_SLUG,
        kinds={KIND_PARTY, KIND_SENATE},
        asserted_source_ids={span.source_id for span in minted_spans},
    )

    deepened = 0
    spans_closed = 0
    sweep_aborted = False
    if joined_obs:
        result = await build_spans(
            session,
            current_biennium=current,
            extra_observations=joined_obs,
            fallback_citation=citation,
        )
        deepened = result.emitted
        spans_closed = result.closed_stale
        sweep_aborted = result.sweep_aborted

    refusals = _refusal_counts(report)
    summary = Pre1991BuildSummary(
        records_pre1991=len(pre),
        identities_minted=sum(1 for i in report.identities if i.disposition != IDENTITY_WSL),
        identities_joined=len(joined_members),
        refusals=refusals,
        persons_created=minted["created"],
        persons_existing=minted["existing"],
        persons_renamed=minted["renamed"],
        persons_retired=retired_persons["retired"],
        persons_retired_anchored=retired_persons["anchored"],
        assignments_emitted=emitted,
        spans_retired=retire.retired,
        spans_retired_anchored=retire.anchored,
        retire_aborted=retire.aborted,
        deepened_emitted=deepened,
        spans_closed=spans_closed,
        sweep_aborted=sweep_aborted,
        declined_parties=len(projection.declined_parties),
        uncovered_rows=len(projection.uncovered_rows),
        seat_overlaps=len(projection.seat_overlaps),
    )
    summary = replace(summary, counters=_counters(summary))
    logger.info("pre1991_build_complete", extra=dict(summary.counters))
    return summary


async def _build_job(ctx: JobContext) -> JobResult:
    """Run the gated build. Degraded on a missing archive **or a guard that tripped**.

    A mass-close/mass-retire abort leaves the stranded rows in place; the build still wrote
    everything else, so it is not a failure — but it must not read as a clean run either,
    or the operator moves on to the #97 collapse with rows the sweep never touched (CR #84).

    ``spans_retired_anchored`` degrades for the same reason from the other direction (CR
    #95): those rows are stranded, still anchored, and deliberately left alive so the
    collapse can move their anchors. The work is unfinished until it has, and a second build
    after it retires them. ``persons_retired_anchored`` is the Person analog (#259) — an
    anchored Person the derivation stopped asserting needs a PM-side merge, not a tombstone.
    """
    summary = await build_pre1991(ctx.require_session())
    if (
        summary.records_pre1991 == 0
        or summary.sweep_aborted
        or summary.retire_aborted
        or summary.spans_retired_anchored
        or summary.persons_retired_anchored
    ):
        return JobResult.degraded(summary.counters)
    return JobResult.ok(summary.counters)


def main(argv: list[str] | None = None) -> int:
    """Build pre-1991 Persons and spans from the archived roster edition.

    Exit ``0`` clean · ``1`` failed (incl. an oracle violation) · ``2`` config · ``4``
    degraded (no archive).
    """
    return run_job(
        JOB_SLUG,
        _build_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.roster_pdf.build",
        description="Build pre-1991 Persons, party spans and Senate seat spans (#228).",
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
