"""Roster succession backfill (#226 write half, epic #219 Phase 2) — the only writing module.

Closes the loop the source was built for. :mod:`.succession` reads dated boundaries out of the
roster's prose, :mod:`.resolve` attaches a member and a seat to them, and this module records
the survivors as :class:`~clearinghouse_domain_legislative.operator_events.OperatorEvent`\\s —
the one mechanism that can move a biennium-quantized span boundary to the day it really
happened.

    python -m usa_wa_adapter_legislature.roster_pdf.backfill [--dry-run] [--limit N]

**Deference to the operator, everywhere the two overlap.** The store already holds 124
hand-entered attestations, and the roster independently reproduces 81 of them to the day —
strong corroboration, and also the hazard. Two rules follow, and both are refusals:

* An **already-attested** boundary is skipped. :func:`record_operator_event` is idempotent on
  the natural key but still refreshes ``reason``/``evidence_url``/``entered_by``, so writing
  one would replace a human's attestation with the machine's in a store whose premise is that
  provenance is never mutated (#54).
* A boundary that **disagrees** with an attestation on the same tenure is skipped and
  *reported*. Those 17 disagreements run from 1 to 41 days; writing them would leave the same
  seat carrying two live, non-superseded boundaries, and the overlay would apply whichever it
  saw last. The roster is authoritative about a great deal, but it does not get to silently
  overrule a human here — an operator adjudicates, and supersedes deliberately.

Conflict scope is the **tenure**, not the seat: same member, same kind, same seat, same
biennium. A gap-and-return member really does hold one seat twice, and a chamber mover really
is seated twice in one biennium; neither is a conflict.

**Deploy: sidecar-paused.** Every event written here moves a span boundary on the next builder
re-drive, which re-anchors the corresponding PM Assignment. Pause the PM sidecar, run this,
re-drive the span builders, then resume — the sequence the #101 House builder documents. Do not
merge and let the timer run.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person
from clearinghouse_domain_legislative.operator_events import (
    OPERATOR_SOURCE_SLUG,
    OperatorEvent,
    event_source_id,
)
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.adapter import SPONSORS_RESOURCE_PREFIX
from usa_wa_adapter_legislature.operators.store import (
    get_or_create_operator_source,
    record_operator_event,
)
from usa_wa_adapter_legislature.provisioning import get_or_create_source as get_or_create_wsl
from usa_wa_adapter_legislature.roster_pdf.cohort import RosterCohortProvider
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source
from usa_wa_adapter_legislature.roster_pdf.resolve import (
    PositionTenure,
    ResolutionOutcome,
    ResolvedEvent,
    Seating,
    SuccessionResolver,
    resolution_summary,
)
from usa_wa_adapter_legislature.roster_pdf.succession import propose_events, summarize
from usa_wa_adapter_legislature.roster_pdf.transport import DEFAULT_ROSTER_URL
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_common.jurisdiction import resolve_jurisdiction
from usa_wa_common.seats import district_number

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "roster-succession-backfill"

#: Stamped on every event this module writes, so a derived boundary is distinguishable from a
#: human attestation without re-deriving anything. Never reuse an operator's name here.
BACKFILL_ENTERED_BY = "roster-pdf-backfill"

#: Why a resolved event was not written. Both are deference to an existing attestation.
SKIP_ALREADY_ATTESTED = "already_attested"
SKIP_CONFLICTS_WITH_ATTESTATION = "conflicts_with_attestation"

#: The House Position span discriminator, as the corpus writes it.
_POSITION = re.compile(r"^ld-(?P<district>\d+)-position-(?P<position>\d+)$")

#: The span key's ``kind`` field sits in position 2 of ``{member}:{kind}:{disc}:{biennium}``.
_HOUSE_SPAN_KIND = "chamber-house"


def roster_evidence_url(page_number: int) -> str:
    """A citation an operator can actually follow: the roster, opened at the source page.

    The document is 233 pages, so a bare document URL is not a citation."""
    return f"{DEFAULT_ROSTER_URL}#page={page_number}"


@dataclass(frozen=True)
class AttestationConflict:
    """A roster boundary that disagrees with a human attestation on the same tenure.

    Carries both dates and the attesting operator, because a conflict reported as a bare count
    cannot be adjudicated.
    """

    member_id: str
    member_name: str
    kind: str
    seat_kind: str | None
    seat_discriminator: str | None
    roster_date: date
    attested_date: date
    attested_by: str | None
    evidence: str

    @property
    def delta_days(self) -> int:
        """Signed distance from the attested date to the roster's."""
        return (self.roster_date - self.attested_date).days


@dataclass(frozen=True)
class WriteSummary:
    """What one write pass did."""

    written: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    conflicts: tuple[AttestationConflict, ...] = ()


@dataclass(frozen=True)
class BackfillSummary:
    """The whole run: what the roster stated, what resolved, and what was written."""

    records: int
    proposed: dict[str, int]
    resolution: dict[str, int]
    written: int
    skipped: dict[str, int]
    conflicts: tuple[AttestationConflict, ...]

    @property
    def counters(self) -> dict[str, object]:
        """The ledger view — conflicts as a **count**, not as 17 nested records.

        The run ledger and the journald line both carry whatever is returned here, and
        inlining the conflict bodies produced a single log record several kilobytes long that
        buried every other counter. The bodies are emitted as their own records instead
        (:func:`_log_conflicts`), which is also what makes them greppable one per line.
        """
        return {
            "records": self.records,
            "proposed": self.proposed,
            "resolution": self.resolution,
            "written": self.written,
            "skipped": self.skipped,
            "conflicts": len(self.conflicts),
        }


#: One tenure: same member, same kind, same seat. Two boundaries of the same kind on one
#: tenure is the conflict; the same member seated twice on *different* seats is not.
Scope = tuple[str, str, str | None, str | None]


@dataclass(frozen=True)
class _Attested:
    """The two facts a conflict report needs from a prior attestation."""

    effective_date: date
    entered_by: str | None


def _scope(event: ResolvedEvent | OperatorEvent) -> Scope:
    """The tenure a boundary belongs to, for conflict detection."""
    return (event.member_id, event.kind, event.seat_kind, event.seat_discriminator)


async def _live_attestations(
    session: AsyncSession,
) -> tuple[set[str], dict[Scope, list[_Attested]]]:
    """Non-superseded attestations, indexed by natural key and by tenure scope.

    Superseded rows are deliberately excluded: a retracted boundary must not permanently block
    the roster from supplying the right one.
    """
    rows = (
        (
            await session.execute(
                select(OperatorEvent).where(OperatorEvent.superseded_by_id.is_(None))
            )
        )
        .scalars()
        .all()
    )
    by_scope: dict[Scope, list[_Attested]] = {}
    for row in rows:
        by_scope.setdefault(_scope(row), []).append(
            _Attested(effective_date=row.effective_date, entered_by=row.entered_by)
        )
    return {r.source_id for r in rows if r.source == OPERATOR_SOURCE_SLUG}, by_scope


async def write_events(
    session: AsyncSession,
    source: Source,
    events: Iterable[ResolvedEvent],
    *,
    entered_by: str = BACKFILL_ENTERED_BY,
) -> WriteSummary:
    """Record resolved events, deferring to every existing attestation. Idempotent.

    Operates in the **caller's** transaction — the CLI commits, or the job harness rolls back on
    ``dry_run``. Rolling back here as well would give one transaction two owners.
    """
    keys, by_scope = await _live_attestations(session)
    written = 0
    skipped: Counter[str] = Counter()
    conflicts: list[AttestationConflict] = []

    for event in events:
        source_id = event_source_id(
            event.member_id,
            event.kind,
            event.effective_date,
            seat_kind=event.seat_kind,
            seat_discriminator=event.seat_discriminator,
        )
        if source_id in keys:
            skipped[SKIP_ALREADY_ATTESTED] += 1
            continue
        biennium = biennium_for_date(event.effective_date)
        prior = [
            row
            for row in by_scope.get(_scope(event), ())
            if biennium_for_date(row.effective_date) == biennium
        ]
        if prior:
            skipped[SKIP_CONFLICTS_WITH_ATTESTATION] += 1
            conflicts.append(
                AttestationConflict(
                    member_id=event.member_id,
                    member_name=event.proposal.member_name,
                    kind=event.kind,
                    seat_kind=event.seat_kind,
                    seat_discriminator=event.seat_discriminator,
                    roster_date=event.effective_date,
                    attested_date=prior[0].effective_date,
                    attested_by=prior[0].entered_by,
                    evidence=event.evidence,
                )
            )
            continue
        await record_operator_event(
            session,
            source,
            member_id=event.member_id,
            kind=event.kind,
            reason=event.reason,
            effective_date=event.effective_date,
            evidence_url=roster_evidence_url(event.proposal.page_number),
            seat_kind=event.seat_kind,
            seat_discriminator=event.seat_discriminator,
            entered_by=entered_by,
        )
        keys.add(source_id)
        # Register what we just wrote, so a *second* roster boundary on the same tenure in the
        # same batch collides with it exactly as it would with a pre-existing attestation.
        by_scope.setdefault(_scope(event), []).append(
            _Attested(effective_date=event.effective_date, entered_by=entered_by)
        )
        written += 1

    return WriteSummary(written=written, skipped=skipped, conflicts=tuple(conflicts))


async def load_seatings(session: AsyncSession, *, source_id: _ULID) -> list[Seating]:
    """The identity index: every archived WSL sponsor roster, expanded to per-year seatings.

    Read from the archive, never pulled — the only network cost is the one-time WSDL load the
    shared binding needs to re-deserialize a stored envelope (the #56 cache path).
    """
    rows = (
        await session.execute(
            select(FetchEvent.resource_id, RawPayload.body)
            .join(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
            .where(
                FetchEvent.source_id == source_id,
                FetchEvent.resource_id.like(f"{SPONSORS_RESOURCE_PREFIX}%"),
            )
        )
    ).all()
    client = WSLClient("SponsorService")
    seatings: list[Seating] = []
    for resource_id, body in rows:
        biennium = resource_id[len(SPONSORS_RESOURCE_PREFIX) :]
        start = int(biennium.split("-")[0])
        for member in await client.parse_sponsors(body):
            district = district_number(member.get("District"))
            if district is None:
                continue
            chamber = "senate" if member.get("Agency") == "Senate" else "house"
            # A biennium's roster attests to both its years; a boundary is dated in one of them.
            for year in (start, start + 1):
                seatings.append(
                    Seating(
                        member_id=str(member["Id"]),
                        chamber=chamber,
                        district=district,
                        year=year,
                        surname=member.get("LastName") or "",
                    )
                )
    return seatings


async def load_positions(session: AsyncSession) -> list[PositionTenure]:
    """The House Position index, read off the existing span corpus (#101/#118, 2003→present)."""
    rows = (
        await session.execute(
            select(
                Person.source_id,
                Assignment.source_id,
                Assignment.valid_from,
                Assignment.valid_to,
            )
            .join(Person, Person.id == Assignment.person_id)
            .where(Assignment.source == "usa_wa_legislature")
        )
    ).all()
    positions: list[PositionTenure] = []
    for member_id, span_key, valid_from, valid_to in rows:
        parts = span_key.split(":")
        if len(parts) < 3 or parts[1] != _HOUSE_SPAN_KIND:
            continue
        match = _POSITION.match(parts[2])
        if match is None:
            continue
        positions.append(
            PositionTenure(
                member_id=member_id,
                district=int(match.group("district")),
                position=match.group("position"),
                first_year=valid_from.year,
                # An open span reaches the present; its last year is today's.
                last_year=(valid_to or date.today()).year,
            )
        )
    return positions


def _log_conflicts(conflicts: Iterable[AttestationConflict]) -> None:
    """One record per conflict — the shape an operator greps when adjudicating.

    ``delta_days`` is carried because it is what separates a semantic disagreement (the roster
    dating a seating to the swearing-in where the operator used the appointment, typically ±1
    day) from a substantive one (Darneille: 41 days).
    """
    for conflict in conflicts:
        logger.warning(
            "roster_backfill_attestation_conflict",
            extra={
                "member_id": conflict.member_id,
                "member_name": conflict.member_name,
                "event_kind": conflict.kind,
                "seat_discriminator": conflict.seat_discriminator,
                "roster_date": conflict.roster_date.isoformat(),
                "attested_date": conflict.attested_date.isoformat(),
                "attested_by": conflict.attested_by,
                "delta_days": conflict.delta_days,
                "evidence": conflict.evidence,
            },
        )


async def resolve_roster_events(
    session: AsyncSession,
) -> tuple[int, dict[str, int], ResolutionOutcome]:
    """Parse the archived roster, propose boundaries, and resolve them. No writes."""
    jurisdiction = await resolve_jurisdiction(session)
    roster_source = await get_or_create_roster_source(session, jurisdiction)
    wsl_source = await get_or_create_wsl(session, jurisdiction)
    records = await RosterCohortProvider(session=session, source_id=roster_source.id).records()
    report = propose_events(records)
    resolver = SuccessionResolver(
        seatings=await load_seatings(session, source_id=wsl_source.id),
        positions=await load_positions(session),
    )
    # ``unseated`` proposals are dated House boundaries awaiting a Position — exactly what the
    # resolver exists to supply, so they enter resolution alongside the ready ones.
    outcome = resolver.resolve_all(report.proposals + report.unseated)
    return len(records), summarize(report), outcome


async def backfill_succession(
    session: AsyncSession, *, dry_run: bool = False, limit: int | None = None
) -> BackfillSummary:
    """Parse → resolve → write. Idempotent; defers to every existing attestation."""
    records, proposed, outcome = await resolve_roster_events(session)
    resolved: Sequence[ResolvedEvent] = outcome.resolved
    if limit is not None:
        resolved = resolved[:limit]
    source = await get_or_create_operator_source(session, await resolve_jurisdiction(session))
    write = await write_events(session, source, resolved)
    _log_conflicts(write.conflicts)
    logger.info(
        "roster_backfill_complete",
        extra={
            "records": records,
            "resolved": len(outcome.resolved),
            "unresolved": len(outcome.unresolved),
            "written": write.written,
            "conflicts": len(write.conflicts),
            "dry_run": dry_run,
        },
    )
    return BackfillSummary(
        records=records,
        proposed=proposed,
        resolution=resolution_summary(outcome),
        written=write.written,
        skipped=dict(write.skipped),
        conflicts=write.conflicts,
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit", type=int, default=None, help="Write at most N events (a staged first run)."
    )


async def _backfill_job(ctx: JobContext) -> JobResult:
    """Write the resolved boundaries. A run that resolves nothing is **degraded** — the roster
    always states boundaries, so an empty resolution means the archive or the sponsor index is
    missing rather than that there was no work."""
    summary = await backfill_succession(
        ctx.require_session(), dry_run=ctx.dry_run, limit=ctx.args.limit
    )
    if not summary.resolution:
        return JobResult.degraded(summary.counters)
    return JobResult.ok(summary.counters)


def main(argv: list[str] | None = None) -> int:
    """Back-fill operator events from the archived roster.

    Exit ``0`` clean · ``1`` failed · ``2`` config · ``4``
    (:data:`~clearinghouse_core.job.EXIT_DEGRADED`) nothing resolved at all.
    """
    return run_job(
        JOB_SLUG,
        _backfill_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.roster_pdf.backfill",
        description="Back-fill operator succession events from the roster PDF (#226).",
        extra_args=_add_args,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
