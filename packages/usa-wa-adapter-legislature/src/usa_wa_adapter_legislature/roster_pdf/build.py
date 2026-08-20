"""Phase B pre-1991 builder (#228, epic #219 Phase 3b) — the write side, oracle-gated.

    python -m usa_wa_adapter_legislature.roster_pdf.build [--dry-run]

Archive → identities → Persons + spans, in one gated pass:

1. Re-parse the archived edition offline (:class:`RosterCohortProvider` — never a re-pull).
2. Resolve identities against the WSL seating index (:mod:`identity`).
3. **Run the acceptance oracle** (:func:`verify_pre1991`) *before any write*: the partition
   must be exact (every pre-1991 record in exactly one identity or refusal), the party
   vocabulary must recognize every token, and no member may cover two Senate seats in one
   biennium (the person-side simultaneity nothing downstream checks — spec oracle item 3).
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
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.tenure_spans import build_tenure_spans
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.provisioning import get_or_create_source as get_or_create_wsl
from usa_wa_adapter_legislature.roster_pdf.backfill import load_seatings
from usa_wa_adapter_legislature.roster_pdf.cohort import RosterCohortProvider
from usa_wa_adapter_legislature.roster_pdf.emit import emit_roster_spans
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_WSL,
    IdentityReport,
    RosterIdentity,
    resolve_identities,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.persons import mint_roster_persons
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
    assignments_emitted: int
    deepened_emitted: int
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
            assignments_emitted=0,
            deepened_emitted=0,
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

    deepened = 0
    if joined_obs:
        result = await build_spans(
            session,
            current_biennium=current,
            extra_observations=joined_obs,
            fallback_citation=citation,
        )
        deepened = result.emitted

    summary = Pre1991BuildSummary(
        records_pre1991=len(pre),
        identities_minted=sum(1 for i in report.identities if i.disposition != IDENTITY_WSL),
        identities_joined=len(joined_members),
        refusals=_refusal_counts(report),
        persons_created=minted["created"],
        persons_existing=minted["existing"],
        assignments_emitted=emitted,
        deepened_emitted=deepened,
        declined_parties=len(projection.declined_parties),
        uncovered_rows=len(projection.uncovered_rows),
        seat_overlaps=len(projection.seat_overlaps),
        counters={
            "records_pre1991": len(pre),
            "persons_created": minted["created"],
            "assignments_emitted": emitted,
            "deepened_emitted": deepened,
        },
    )
    logger.info(
        "pre1991_build_complete",
        extra={
            "records_pre1991": summary.records_pre1991,
            "minted": summary.identities_minted,
            "joined": summary.identities_joined,
            "refusals": summary.refusals,
            "persons_created": summary.persons_created,
            "assignments_emitted": summary.assignments_emitted,
            "deepened_emitted": summary.deepened_emitted,
            "declined_parties": summary.declined_parties,
            "uncovered_rows": summary.uncovered_rows,
            "seat_overlaps": summary.seat_overlaps,
        },
    )
    return summary


async def _build_job(ctx: JobContext) -> JobResult:
    """Run the gated build. Zero pre-1991 records is degraded — the archive is missing."""
    summary = await build_pre1991(ctx.require_session())
    if summary.records_pre1991 == 0:
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
