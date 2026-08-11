"""Operator-succession event CLI (#107) — the live interjection surface.

    python -m usa_wa_adapter_legislature.operators.cli \
        --member-id 29091 --kind departed --reason died \
        --effective-date 2025-04-19 --evidence-url https://... [--entered-by greg]

    python -m usa_wa_adapter_legislature.operators.cli \
        --member-id 35410 --kind seated --reason appointed \
        --seat-kind chamber-senate --seat-discriminator 5 \
        --effective-date 2025-06-03 --evidence-url https://...

    python -m usa_wa_adapter_legislature.operators.cli --file events.json   # batch
    python -m usa_wa_adapter_legislature.operators.cli --supersede <id> ... # correction
    python -m usa_wa_adapter_legislature.operators.cli --list               # inspect

App-role DML (writes ``operator_events`` + provenance under ``usa_wa_operator``); shell access
is the trust boundary, as with the redrive CLI. Validates that ``member_id`` resolves to a
:class:`Person` before writing (a typo would otherwise be a silent no-op overlay). ``--dry-run``
rolls back. Each event is applied as an authoritative overlay by the span builders on their next
run (the daily refresh re-drives them); provenance is append-only, corrections via ``--supersede``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import EXIT_CONFIG, JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.operator_events import (
    DEPARTED_REASONS,
    KIND_DEPARTED,
    KIND_SEATED,
    KIND_VACATED,
    KINDS,
    SEAT_KINDS,
    SEAT_SCOPED_KINDS,
    SEATED_REASONS,
    VACATED_REASONS,
    OperatorEvent,
)
from clearinghouse_domain_legislative.span_emit import resolve_person
from usa_wa_adapter_legislature.operators.store import (
    current_events,
    get_or_create_operator_source,
    record_operator_event,
    supersede_event,
)
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "operator-event-record"

_REASONS_BY_KIND = {
    KIND_DEPARTED: set(DEPARTED_REASONS),
    KIND_VACATED: set(VACATED_REASONS),
    KIND_SEATED: set(SEATED_REASONS),
}


class OperatorEventError(ValueError):
    """A validation failure the CLI surfaces (exit 2), not a stack trace."""


@dataclass(frozen=True)
class EventSpec:
    """One operator event's fields, pre-validation (from CLI args or a --file row)."""

    member_id: str
    kind: str
    reason: str
    effective_date: date
    evidence_url: str
    seat_kind: str | None = None
    seat_discriminator: str | None = None
    supersede_id: str | None = None


def _validate(spec: EventSpec) -> None:
    """Shape validation independent of the DB (kind/reason/seat consistency)."""
    if spec.kind not in KINDS:
        raise OperatorEventError(f"unknown kind {spec.kind!r} (expected one of {sorted(KINDS)})")
    if spec.reason not in _REASONS_BY_KIND[spec.kind]:
        raise OperatorEventError(
            f"reason {spec.reason!r} invalid for kind {spec.kind!r} "
            f"(expected one of {sorted(_REASONS_BY_KIND[spec.kind])})"
        )
    seat_scoped = spec.kind in SEAT_SCOPED_KINDS
    has_seat = spec.seat_kind is not None and spec.seat_discriminator is not None
    if seat_scoped and not has_seat:
        raise OperatorEventError(
            f"kind {spec.kind!r} requires --seat-kind and --seat-discriminator"
        )
    if not seat_scoped and (spec.seat_kind is not None or spec.seat_discriminator is not None):
        raise OperatorEventError(f"kind {spec.kind!r} must not carry a seat")
    if seat_scoped and spec.seat_kind not in SEAT_KINDS:
        # A seat_kind no builder owns would record an event the overlay silently no-ops
        # everywhere (the member-id-typo failure mode, for the seat).
        raise OperatorEventError(
            f"seat_kind {spec.seat_kind!r} is not a known seat kind "
            f"(expected one of {sorted(SEAT_KINDS)})"
        )


async def validate_and_record(session: AsyncSession, source, spec: EventSpec) -> OperatorEvent:
    """Validate ``spec`` (shape + member existence) and persist it; return the row.

    A ``supersede_id`` records a date-correction of that prior event. Raises
    :class:`OperatorEventError` on any validation failure (no partial write)."""
    _validate(spec)
    person = await resolve_person(session, spec.member_id)
    if person is None:
        raise OperatorEventError(
            f"member_id {spec.member_id!r} resolves to no usa_wa_legislature Person "
            "(typo, or run the sponsor harvest first)"
        )
    if spec.supersede_id is not None:
        prior = (
            await session.execute(
                select(OperatorEvent).where(OperatorEvent.id == spec.supersede_id)
            )
        ).scalar_one_or_none()
        if prior is None:
            raise OperatorEventError(f"--supersede id {spec.supersede_id!r} not found")
        # supersede_event derives kind/member/seat from `prior` and applies only the new
        # reason/date/url — so a passed --kind/--member-id/--seat-* that disagrees with prior
        # would be silently ignored, and (worse) spec.reason validated against spec.kind would
        # be written under prior.kind, breaking the kind↔reason pairing (no DB constraint on
        # reason). Reject a mismatch rather than silently mis-apply.
        if spec.kind != prior.kind:
            raise OperatorEventError(
                f"--supersede: kind {spec.kind!r} differs from the prior event's {prior.kind!r} "
                "(a supersede corrects the date/reason/url, not the kind)"
            )
        if spec.member_id != prior.member_id:
            raise OperatorEventError(
                f"--supersede: member_id {spec.member_id!r} differs from the prior event's "
                f"{prior.member_id!r}"
            )
        if spec.seat_kind != prior.seat_kind or spec.seat_discriminator != prior.seat_discriminator:
            raise OperatorEventError(
                "--supersede: seat differs from the prior event's "
                f"{prior.seat_kind}:{prior.seat_discriminator}"
            )
        return await supersede_event(
            session,
            source,
            prior,
            reason=spec.reason,
            effective_date=spec.effective_date,
            evidence_url=spec.evidence_url,
            entered_by=_entered_by(),
        )
    return await record_operator_event(
        session,
        source,
        member_id=spec.member_id,
        kind=spec.kind,
        reason=spec.reason,
        effective_date=spec.effective_date,
        evidence_url=spec.evidence_url,
        seat_kind=spec.seat_kind,
        seat_discriminator=spec.seat_discriminator,
        entered_by=_entered_by(),
    )


def _entered_by() -> str | None:
    """The operator, best-effort from the environment (audit; git isn't the trail here)."""
    return os.environ.get("USA_WA_OPERATOR") or os.environ.get("USER")


def load_specs(payload: object) -> list[EventSpec]:
    """Parse a --file JSON body (a list of event objects) into :class:`EventSpec`s."""
    if not isinstance(payload, list):
        raise OperatorEventError("--file must contain a JSON array of event objects")
    specs: list[EventSpec] = []
    for i, row in enumerate(payload):
        if not isinstance(row, dict):
            raise OperatorEventError(f"--file row {i} is not an object")
        try:
            specs.append(
                EventSpec(
                    member_id=str(row["member_id"]),
                    kind=str(row["kind"]),
                    reason=str(row["reason"]),
                    effective_date=date.fromisoformat(str(row["effective_date"])),
                    evidence_url=str(row["evidence_url"]),
                    seat_kind=row.get("seat_kind"),
                    seat_discriminator=(
                        None
                        if row.get("seat_discriminator") is None
                        else str(row["seat_discriminator"])
                    ),
                    supersede_id=row.get("supersede_id"),
                )
            )
        except KeyError as exc:
            raise OperatorEventError(f"--file row {i} missing required field {exc}") from exc
    return specs


def _spec_from_args(args: argparse.Namespace) -> EventSpec:
    if not all([args.member_id, args.kind, args.reason, args.effective_date, args.evidence_url]):
        raise OperatorEventError(
            "a single event needs --member-id --kind --reason --effective-date --evidence-url"
        )
    return EventSpec(
        member_id=args.member_id,
        kind=args.kind,
        reason=args.reason,
        effective_date=date.fromisoformat(args.effective_date),
        evidence_url=args.evidence_url,
        seat_kind=args.seat_kind,
        seat_discriminator=args.seat_discriminator,
        supersede_id=args.supersede,
    )


async def _load_file_specs(path: str) -> list[EventSpec]:
    """Read a ``--file`` batch body without blocking the event loop (#196).

    Only the read goes to a worker thread; parsing and validation stay here, so an
    ``OperatorEventError`` still surfaces from the same place it always did.
    """
    return load_specs(json.loads(await asyncio.to_thread(Path(path).read_text)))


def _format_event(event: OperatorEvent) -> str:
    seat = f" seat={event.seat_kind}:{event.seat_discriminator}" if event.seat_kind else ""
    return (
        f"{event.id}  {event.member_id}  {event.kind}/{event.reason}  "
        f"{event.effective_date.isoformat()}{seat}  {event.evidence_url}"
    )


async def _run(session: AsyncSession, args: argparse.Namespace) -> int:
    if args.list:
        events = await current_events(session)
        for event in events:
            print(_format_event(event))
        print(f"{len(events)} current operator event(s)")
        return 0

    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_operator_source(session, jurisdiction)

    if args.file:
        specs = await _load_file_specs(args.file)
    else:
        specs = [_spec_from_args(args)]

    recorded = [await validate_and_record(session, source, spec) for spec in specs]
    for event in recorded:
        print(_format_event(event))
    print(f"recorded {len(recorded)} operator event(s)")
    return 0


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the recorder's own flags to the harness's shared parser."""
    parser.add_argument("--member-id", help="the WSL member Id (Person.source_id)")
    parser.add_argument("--kind", choices=sorted(KINDS), help="departed | vacated | seated")
    parser.add_argument(
        "--reason", help="died|resigned|expelled | moved|resigned|defeated | appointed|sworn_in"
    )
    parser.add_argument(
        "--seat-kind", help="chamber-senate | chamber-house | committee (seat-scoped)"
    )
    parser.add_argument("--seat-discriminator", help="LD | ld-{n}-position-{p} | committee id")
    parser.add_argument("--effective-date", help="YYYY-MM-DD, the succession boundary")
    parser.add_argument("--evidence-url", help="operator-cited source (news/official)")
    parser.add_argument("--supersede", help="prior event id to correct (a date change)")
    parser.add_argument("--file", help="JSON array of event objects (batch)")
    parser.add_argument("--list", action="store_true", help="list current operator events")


async def _record_job(ctx: JobContext) -> JobResult:
    """Harness handler: validate + record, mapping a validation failure onto exit 2.

    ``commit=False`` and the transaction stays here, because the pre-#179b rule is not
    "commit unless dry-run": ``--list`` is read-only and committed even under
    ``--dry-run``.
    """
    session = ctx.require_session()
    try:
        await _run(session, ctx.args)
    except OperatorEventError as exc:
        print(f"error: {exc}", file=sys.stderr)
        await session.rollback()
        return JobResult.failed({"error": str(exc)}, exit_code=EXIT_CONFIG)
    if ctx.dry_run and not ctx.args.list:
        await session.rollback()
        print("(dry-run, rolled back)")
    else:
        await session.commit()
    return JobResult.ok({"listed" if ctx.args.list else "recorded": True})


def main(argv: list[str] | None = None) -> int:
    """Record operator succession events. Exit ``0`` clean · ``2`` validation/config."""
    return run_job(
        JOB_SLUG,
        _record_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.operators.cli",
        description=("Record operator succession events (#107) — the live interjection surface."),
        extra_args=_add_args,
        commit=False,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
