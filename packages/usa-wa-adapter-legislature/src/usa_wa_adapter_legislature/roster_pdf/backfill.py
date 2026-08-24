"""Roster succession backfill (#226 write half, epic #219 Phase 2) — the only writing module.

Closes the loop the source was built for. :mod:`.succession` reads dated boundaries out of the
roster's prose, :mod:`.resolve` attaches a member and a seat to them, and this module records
the survivors as :class:`~clearinghouse_domain_legislative.operator_events.OperatorEvent`\\s —
the one mechanism that can move a biennium-quantized span boundary to the day it really
happened.

    python -m usa_wa_adapter_legislature.roster_pdf.backfill \\
        [--dry-run] [--limit N] [--supersede-conflicts]

**Deference to the operator, everywhere the two overlap.** The store already holds 124
attestations, and the roster independently reproduces 81 of them to the day — strong
corroboration, and also the hazard. Two rules follow:

* An **already-attested** boundary is skipped, always. :func:`record_operator_event` is
  idempotent on the natural key but still refreshes ``reason``/``evidence_url``/``entered_by``,
  so writing one would replace the existing attestation's provenance with the machine's, in a
  store whose premise is that provenance is never mutated (#54). There is nothing to correct
  and something to lose.
* A boundary that **disagrees** with an attestation on the same tenure is reported, and by
  default skipped. Writing it alongside would leave one seat carrying two live, non-superseded
  boundaries for the overlay to pick between.

``--supersede-conflicts`` opts into resolving that second case in the roster's favour — but
only against a **machine-entered** attestation (:data:`MACHINE_ENTERED_BY`). It is off by
default because the safe reading of a disagreement is that someone knew something the roster
does not, and that reading should be overridden deliberately rather than assumed. It was
overridden once, on evidence: all 17 live conflicts were agent-entered rows citing
Wikipedia/Ballotpedia, and **5 of the 9 conflicting departures had been dated to the
successor's seating date** — collapsing "incumbent departed" and "successor seated" into one
date and asserting a zero-day vacancy where 1-29 days actually elapsed (Scott Barr's row cited
his successor Bob Morton's page). Superseding *appends* the correction and stamps the prior
row's ``superseded_by_id``, so the retracted attestation stays auditable.

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
from datetime import UTC, date, datetime

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
from clearinghouse_domain_legislative.span_kinds import KIND_HOUSE
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.adapter import SPONSORS_RESOURCE_PREFIX
from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG
from usa_wa_adapter_legislature.operators.store import (
    get_or_create_operator_source,
    record_operator_event,
    supersede_event,
)
from usa_wa_adapter_legislature.provisioning import get_or_create_source as get_or_create_wsl
from usa_wa_adapter_legislature.roster_pdf.cohort import RosterCohortProvider
from usa_wa_adapter_legislature.roster_pdf.identity import (
    identity_seatings,
    resolve_identities,
)
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

#: ``entered_by`` values ``--supersede-conflicts`` is allowed to overrule — the machine-entered
#: ones. This allowlist **is** the safety property of that flag.
#:
#: The rule the backfill defaults to (never overrule an attestation) was written for a human's
#: judgement. The live conflicts were not that: all 17 were agent-entered rows citing
#: Wikipedia/Ballotpedia, and 5 of the 9 conflicting departures had been dated to the
#: **successor's seating date** — collapsing "incumbent departed" and "successor seated" into a
#: single date and asserting a zero-day vacancy where 1-29 days actually elapsed (Barr's row
#: even cited his successor Bob Morton's page). Against the Legislature's own publication that
#: is a defect. A *named operator's* attestation is still never the backfill's to overrule, and
#: the only thing keeping that true is this set — so add to it deliberately.
MACHINE_ENTERED_BY = frozenset({"exedev", BACKFILL_ENTERED_BY})

#: The House Position span discriminator, as the corpus writes it.
_POSITION = re.compile(r"^ld-(?P<district>\d+)-position-(?P<position>\d+)$")


def roster_evidence_url(page_number: int, *, base_url: str = DEFAULT_ROSTER_URL) -> str:
    """A citation an operator can actually follow: the roster, opened at the source page.

    The document is 233 pages, so a bare document URL is not a citation.

    ``base_url`` comes from the **archived** ``FetchEvent`` rather than the module default
    (CR-4 finding 28): ``s4gf4suc`` is a CMS-minted media key the transport already expects to
    rotate — it re-discovers the href on a 404 — so a citation pinned to the compiled-in URL is
    dead the moment that happens, while the archived bytes remain perfectly good."""
    return f"{base_url}#page={page_number}"


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
    """What one write pass did.

    ``written`` and ``superseded`` are disjoint per boundary: it either had no prior
    attestation on its tenure or replaced every one there was (a single supersession may
    therefore retract more than one row).

    ``conflicts`` carries the per-row detail and lists every disagreement found, whether or not
    it was acted on — so a default run and a superseding run report the same adjudication set.
    """

    written: int = 0
    superseded: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    conflicts: tuple[AttestationConflict, ...] = ()


@dataclass(frozen=True)
class BackfillSummary:
    """The whole run: what the roster stated, what resolved, and what was written."""

    records: int
    #: Size of each seating index the resolver consulted (CR-4 finding 25).
    seatings: dict[str, int]
    proposed: dict[str, int]
    resolution: dict[str, int]
    written: int
    superseded: int
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
            "seatings": self.seatings,
            "proposed": self.proposed,
            "resolution": self.resolution,
            "written": self.written,
            "superseded": self.superseded,
            "skipped": self.skipped,
            "conflicts": len(self.conflicts),
        }


#: One tenure: same member, same kind, same seat. Two boundaries of the same kind on one
#: tenure is the conflict; the same member seated twice on *different* seats is not.
Scope = tuple[str, str, str | None, str | None]


@dataclass(frozen=True)
class _Attested:
    """A prior attestation, as much of it as the conflict path needs.

    ``row`` is the ORM object when the attestation came from the database, and ``None`` for one
    this batch just wrote — an in-batch collision is reported, never superseded, because the
    roster does not get to overrule itself silently.
    """

    effective_date: date
    entered_by: str | None
    row: OperatorEvent | None = None


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
            _Attested(effective_date=row.effective_date, entered_by=row.entered_by, row=row)
        )
    return {r.source_id for r in rows if r.source == OPERATOR_SOURCE_SLUG}, by_scope


async def write_events(
    session: AsyncSession,
    source: Source,
    events: Iterable[ResolvedEvent],
    *,
    entered_by: str = BACKFILL_ENTERED_BY,
    supersede_conflicts: bool = False,
    evidence_base: str = DEFAULT_ROSTER_URL,
) -> WriteSummary:
    """Record resolved events, deferring to every existing attestation. Idempotent.

    ``supersede_conflicts`` opts into letting the roster **replace** a disagreeing attestation
    — but only a machine-entered one (:data:`MACHINE_ENTERED_BY`). It is off by default because
    the safe reading of a disagreement is that someone knew something the roster does not, and
    that reading has to be actively overridden rather than assumed. Superseding *appends* the
    correction and stamps the prior row's ``superseded_by_id``; nothing is mutated (#54), so the
    retracted attestation stays on record pointing at what replaced it.

    Operates in the **caller's** transaction — the CLI commits, or the job harness rolls back on
    ``dry_run``. Rolling back here as well would give one transaction two owners.
    """
    keys, by_scope = await _live_attestations(session)
    written = 0
    superseded = 0
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
            # One conflict per disagreeing *row*, not per event: a tenure can hold more than
            # one live attestation, and an operator adjudicating needs to see each of them
            # (CR-4 finding 24).
            conflicts.extend(
                AttestationConflict(
                    member_id=event.member_id,
                    member_name=event.proposal.member_name,
                    kind=event.kind,
                    seat_kind=event.seat_kind,
                    seat_discriminator=event.seat_discriminator,
                    roster_date=event.effective_date,
                    attested_date=attested.effective_date,
                    attested_by=attested.entered_by,
                    evidence=event.evidence,
                )
                for attested in prior
            )
            # **All or nothing.** Superseding the machine rows while leaving a human's would
            # still leave the tenure carrying two live boundaries — the state this refusal
            # exists to prevent — having discarded the machine rows' evidence for nothing.
            replaceable = supersede_conflicts and all(
                attested.row is not None and attested.entered_by in MACHINE_ENTERED_BY
                for attested in prior
            )
            if not replaceable:
                skipped[SKIP_CONFLICTS_WITH_ATTESTATION] += 1
                continue
            for attested in prior:
                await supersede_event(
                    session,
                    source,
                    attested.row,
                    reason=event.reason,
                    effective_date=event.effective_date,
                    evidence_url=roster_evidence_url(
                        event.proposal.page_number, base_url=evidence_base
                    ),
                    entered_by=entered_by,
                )
            keys.add(source_id)
            # The tenure's live attestation is now ours; a further roster boundary on it in
            # this same batch must collide with that, not with a row we just retracted. Every
            # prior is dropped, not just the first — leaving the rest produced a phantom
            # conflict against an already-superseded row (CR-5 finding 31).
            by_scope[_scope(event)] = [
                a for a in by_scope.get(_scope(event), ()) if a not in prior
            ] + [_Attested(effective_date=event.effective_date, entered_by=entered_by)]
            superseded += 1
            continue
        await record_operator_event(
            session,
            source,
            member_id=event.member_id,
            kind=event.kind,
            reason=event.reason,
            effective_date=event.effective_date,
            evidence_url=roster_evidence_url(event.proposal.page_number, base_url=evidence_base),
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

    return WriteSummary(
        written=written,
        superseded=superseded,
        skipped=skipped,
        conflicts=tuple(conflicts),
    )


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
                        given_name=member.get("FirstName") or "",
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
            .where(Assignment.source == WSL_SOURCE_SLUG)
        )
    ).all()
    positions: list[PositionTenure] = []
    for member_id, span_key, valid_from, valid_to in rows:
        parts = span_key.split(":")
        # The span key's ``kind`` sits in position 2 of ``{member}:{kind}:{disc}:{biennium}``.
        if len(parts) < 3 or parts[1] != KIND_HOUSE:
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
                # An open span reaches the present; its last year is today's — read in
                # **UTC**, since `date.today()` is local wall-clock and would report the
                # prior year for the first hours of Jan 1 on a host behind UTC, silently
                # failing to position a January boundary (CR-4 finding 23).
                last_year=(valid_to or datetime.now(UTC).date()).year,
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
) -> tuple[int, dict[str, int], ResolutionOutcome, str, dict[str, int]]:
    """Parse the archived roster, propose boundaries, and resolve them. No writes.

    Returns the citation base URL alongside the outcome so the caller neither re-derives the
    latest-edition rule nor get-or-creates the roster ``Source`` a second time (CR-5 34/36).
    """
    jurisdiction = await resolve_jurisdiction(session)
    roster_source = await get_or_create_roster_source(session, jurisdiction)
    wsl_source = await get_or_create_wsl(session, jurisdiction)
    provider = RosterCohortProvider(session=session, source_id=roster_source.id)
    records = await provider.records()
    # `None` only when nothing is archived — in which case there is nothing to cite either.
    evidence_base = await provider.archived_url() or DEFAULT_ROSTER_URL
    report = propose_events(records)
    # The WSL index floors at 1991, so on its own it resolves every pre-floor boundary
    # ``no_member`` — 363 of them — however many Persons exist locally. #228's identity
    # resolution supplies the missing half: the same records, keyed by the identity each one
    # was minted or joined under. The WSL seatings go in first and are also the join evidence
    # the identity resolution runs on, so a crosser is keyed by its WSL member id in both
    # halves rather than appearing twice under two ids (#226).
    wsl_seatings = await load_seatings(session, source_id=wsl_source.id)
    identities = resolve_identities(records, seatings=wsl_seatings)
    roster_seatings = identity_seatings(identities)
    # A refused identity contributes no seating, so its boundaries land in ``no_member``
    # looking exactly like a record with no identity at all. Name them (CR-4 finding 24):
    # a refusal is the case #228 asks a human to adjudicate, not a coverage gap.
    for refusal in identities.refused:
        logger.info(
            "roster_backfill_identity_refused",
            extra={"reason": refusal.reason, "fold": refusal.fold, "detail": refusal.detail},
        )
    resolver = SuccessionResolver(
        seatings=[*wsl_seatings, *roster_seatings],
        positions=await load_positions(session),
    )
    # ``unseated`` proposals are dated House boundaries awaiting a Position — exactly what the
    # resolver exists to supply, so they enter resolution alongside the ready ones.
    outcome = resolver.resolve_all(report.proposals + report.unseated)
    # Index sizes ride the counters (CR-4 finding 25). Without them a future revision that
    # broke identity resolution would push ``no_member`` back toward 363 and read as a data
    # problem rather than an index one — i.e. exactly like the defect this seam fixed.
    seatings = {"wsl": len(wsl_seatings), "roster": len(roster_seatings)}
    return len(records), summarize(report), outcome, evidence_base, seatings


async def backfill_succession(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    supersede_conflicts: bool = False,
) -> BackfillSummary:
    """Parse → resolve → write. Idempotent; defers to every existing attestation."""
    records, proposed, outcome, evidence_base, seatings = await resolve_roster_events(session)
    resolved: Sequence[ResolvedEvent] = outcome.resolved
    if limit is not None:
        resolved = resolved[:limit]
    source = await get_or_create_operator_source(session, await resolve_jurisdiction(session))
    write = await write_events(session, source, resolved, supersede_conflicts=supersede_conflicts)
    _log_conflicts(write.conflicts)
    logger.info(
        "roster_backfill_complete",
        extra={
            "records": records,
            "resolved": len(outcome.resolved),
            "unresolved": len(outcome.unresolved),
            "written": write.written,
            "superseded": write.superseded,
            "conflicts": len(write.conflicts),
            "dry_run": dry_run,
        },
    )
    return BackfillSummary(
        records=records,
        seatings=seatings,
        proposed=proposed,
        resolution=resolution_summary(outcome),
        written=write.written,
        superseded=write.superseded,
        skipped=dict(write.skipped),
        conflicts=write.conflicts,
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit", type=int, default=None, help="Write at most N events (a staged first run)."
    )
    parser.add_argument(
        "--supersede-conflicts",
        action="store_true",
        help=(
            "Let the roster replace a disagreeing attestation, but only a machine-entered "
            "one. A named operator's attestation is never superseded."
        ),
    )


async def _backfill_job(ctx: JobContext) -> JobResult:
    """Write the resolved boundaries. A run that resolves nothing is **degraded** — the roster
    always states boundaries, so an empty resolution means the archive or the sponsor index is
    missing rather than that there was no work."""
    summary = await backfill_succession(
        ctx.require_session(),
        dry_run=ctx.dry_run,
        limit=ctx.args.limit,
        supersede_conflicts=ctx.args.supersede_conflicts,
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
