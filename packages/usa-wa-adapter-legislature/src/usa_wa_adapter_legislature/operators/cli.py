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
    python -m usa_wa_adapter_legislature.operators.cli --retract <id> \
        --evidence-url https://...                                          # withdrawal
    python -m usa_wa_adapter_legislature.operators.cli --list               # inspect

App-role DML (writes ``registry.operator_events``; the attestation lands in the raw store under
``usa_wa_operator`` once the transaction commits); shell access is the trust boundary. Validates
that ``member_id`` is a registered WSL member, and a committee seat's id a registered committee,
before writing (a typo would otherwise be a silent no-op overlay). ``--dry-run`` rolls back. The
nightly pipeline applies each event as an authoritative overlay on its next build; provenance is
append-only, corrections via ``--supersede``, and an event with nothing to correct it to — a
boundary the member never crossed — withdrawn via ``--retract`` (#468).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from clearinghouse_core.job import (
    EXIT_CONFIG,
    JobContext,
    JobResult,
    load_json_batch,
    run_job,
)
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import KIND_PERSON, RegistryKey
from clearinghouse_domain_legislative.operator_events import (
    DEPARTED_REASONS,
    ENDING_KINDS,
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
from clearinghouse_domain_legislative.span_kinds import KIND_COMMITTEE
from usa_wa_adapter_legislature.committees.succession_store import is_registered_committee
from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG
from usa_wa_adapter_legislature.operators.raw import PendingAttestations, archive_after_commit
from usa_wa_adapter_legislature.operators.store import (
    current_events,
    record_operator_event,
    retract_event,
    supersede_event,
)

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


async def _is_registered_member(session: AsyncSession, member_id: str) -> bool:
    """Whether the registry knows ``member_id`` as a WSL member — the identity authority
    since #412 PR F dropped the canonical persons table. The nightly registrar registers
    a new member the night their sponsor record is first harvested."""
    key = await session.scalar(
        select(RegistryKey.id).where(
            RegistryKey.kind == KIND_PERSON,
            RegistryKey.natural_key == f"{WSL_SOURCE_SLUG}:{member_id}",
        )
    )
    return key is not None


async def validate_and_record(
    session: AsyncSession, spec: EventSpec, *, raw: PendingAttestations
) -> OperatorEvent:
    """Validate ``spec`` (shape, member, committee seat) and persist it; return the row.

    A ``supersede_id`` records a correction of that prior event — a new date, or a
    reclassification within endings (``departed`` <-> ``vacated``, #363). Raises
    :class:`OperatorEventError` on any validation failure (no partial write)."""
    _validate(spec)
    if not await _is_registered_member(session, spec.member_id):
        raise OperatorEventError(
            f"member_id {spec.member_id!r} resolves to no registered {WSL_SOURCE_SLUG} person "
            "(typo, or a member the nightly has not registered yet)"
        )
    if spec.seat_kind == KIND_COMMITTEE and not await is_registered_committee(
        session, spec.seat_discriminator
    ):
        # The committee-succession CLI's check (#445), for the same reason as the member's:
        # an unregistered committee id is an overlay no committee span ever meets.
        raise OperatorEventError(
            f"seat_discriminator {spec.seat_discriminator!r} is no registered "
            f"{WSL_SOURCE_SLUG} committee org (typo, a structural org, or not yet registered)"
        )
    if spec.supersede_id is not None:
        prior = (
            await session.execute(
                select(OperatorEvent).where(OperatorEvent.id == spec.supersede_id)
            )
        ).scalar_one_or_none()
        if prior is None:
            raise OperatorEventError(f"--supersede id {spec.supersede_id!r} not found")
        # The kind MAY change, within endings only (usa-wa#363): `departed` and
        # `vacated` are two readings of one boundary, and provenance is append-only,
        # so a supersede is the only way a projection can say it changed its mind.
        # Turning an ending into a beginning is a different fact, not a better
        # reading. `supersede_event` refuses that for every caller; it is checked
        # here as well (CR 139) so the refusal takes the CLI's own error path —
        # the `error:` line, the rollback, EXIT_CONFIG — rather than escaping as a
        # bare ValueError the handler below does not catch.
        if spec.kind != prior.kind and not {spec.kind, prior.kind} <= ENDING_KINDS:
            raise OperatorEventError(
                f"--supersede: cannot change kind {prior.kind!r} -> {spec.kind!r}: a "
                "correction may restate which ending a boundary was, never turn an ending "
                "into a beginning"
            )
        #
        # The spec's kind and seat are passed through rather than derived from
        # `prior`, which is what keeps the kind↔reason pairing intact: the CLI
        # validates `reason` against `spec.kind`, so writing it under `prior.kind`
        # would break a pairing no DB constraint enforces.
        if spec.member_id != prior.member_id:
            raise OperatorEventError(
                f"--supersede: member_id {spec.member_id!r} differs from the prior event's "
                f"{prior.member_id!r}"
            )
        if spec.kind == prior.kind and (
            spec.seat_kind != prior.seat_kind or spec.seat_discriminator != prior.seat_discriminator
        ):
            raise OperatorEventError(
                "--supersede: seat differs from the prior event's "
                f"{prior.seat_kind}:{prior.seat_discriminator}"
            )
        if prior.retracted_at is not None:
            raise OperatorEventError(
                f"--supersede: event {prior.id} was retracted; there is nothing standing to correct"
            )
        try:
            return await supersede_event(
                session,
                prior,
                raw=raw,
                kind=spec.kind,
                seat_kind=spec.seat_kind,
                seat_discriminator=spec.seat_discriminator,
                reason=spec.reason,
                effective_date=spec.effective_date,
                evidence_url=spec.evidence_url,
                entered_by=_entered_by(),
            )
        except ValueError as exc:
            # CR 3: the correction's own key can be a retracted event's, which the store
            # refuses below every check above — that refusal takes the error path too.
            raise OperatorEventError(f"--supersede: {exc}") from exc
    try:
        return await record_operator_event(
            session,
            raw=raw,
            member_id=spec.member_id,
            kind=spec.kind,
            reason=spec.reason,
            effective_date=spec.effective_date,
            evidence_url=spec.evidence_url,
            seat_kind=spec.seat_kind,
            seat_discriminator=spec.seat_discriminator,
            entered_by=_entered_by(),
        )
    except ValueError as exc:
        # The store's refusal (a retracted natural key) takes the CLI's error path.
        raise OperatorEventError(str(exc)) from exc


def _parse_event_id(event_id: str) -> ULID:
    """The row id as ``--list`` prints it (a ULID, in either case — Crockford base32 is
    case-insensitive) or as psql prints it (a UUID)."""
    try:
        return ULID.from_str(event_id.upper())
    except ValueError:
        pass
    try:
        return ULID.from_uuid(UUID(event_id))
    except ValueError as exc:
        raise OperatorEventError(f"--retract id {event_id!r} is neither a ULID nor a UUID") from exc


async def retract_by_id(
    session: AsyncSession, event_id: str, *, evidence_url: str, raw: PendingAttestations
) -> OperatorEvent:
    """Retract the event ``event_id`` (#468); return the row.

    For an event that was never true — a boundary projected onto a member who never
    crossed it — not one recorded wrong, which ``--supersede`` corrects. Raises
    :class:`OperatorEventError` on an unknown or superseded id (no partial write)."""
    key = _parse_event_id(event_id)
    event = (
        await session.execute(select(OperatorEvent).where(OperatorEvent.id == key))
    ).scalar_one_or_none()
    if event is None:
        raise OperatorEventError(f"--retract id {event_id!r} not found")
    try:
        return await retract_event(
            session, event, raw=raw, evidence_url=evidence_url, retracted_by=_entered_by()
        )
    except ValueError as exc:
        raise OperatorEventError(f"--retract: {exc}") from exc


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


#: Every flag that describes an event to record — none of which a retraction takes.
_EVENT_FLAGS = (
    "member_id",
    "kind",
    "reason",
    "effective_date",
    "seat_kind",
    "seat_discriminator",
    "supersede",
    "file",
    "list",
)


def _check_retract_args(args: argparse.Namespace) -> None:
    """A retraction names a row and why; anything else is a half-typed other command."""
    if not args.evidence_url:
        raise OperatorEventError("--retract needs --evidence-url: why the event never held")
    extra = [f"--{name.replace('_', '-')}" for name in _EVENT_FLAGS if getattr(args, name)]
    if extra:
        raise OperatorEventError(f"--retract takes only --evidence-url; also got {extra}")


def _format_event(event: OperatorEvent) -> str:
    seat = f" seat={event.seat_kind}:{event.seat_discriminator}" if event.seat_kind else ""
    return (
        f"{event.id}  {event.member_id}  {event.kind}/{event.reason}  "
        f"{event.effective_date.isoformat()}{seat}  {event.evidence_url}"
    )


async def _run(session: AsyncSession, args: argparse.Namespace, raw: PendingAttestations) -> int:
    # Before --list (CR 2): a --list beside it would otherwise win and exit 0 having
    # retracted nothing; the arg check refuses the pair instead.
    if args.retract:
        _check_retract_args(args)
        event = await retract_by_id(session, args.retract, evidence_url=args.evidence_url, raw=raw)
        print(_format_event(event))
        print("retracted 1 operator event")
        return 0

    if args.list:
        events = await current_events(session)
        for event in events:
            print(_format_event(event))
        print(f"{len(events)} current operator event(s)")
        return 0

    if args.file:
        specs = await load_json_batch(args.file, load_specs)
    else:
        specs = [_spec_from_args(args)]

    recorded = [await validate_and_record(session, spec, raw=raw) for spec in specs]
    for event in recorded:
        print(_format_event(event))
    print(f"recorded {len(recorded)} operator event(s)")
    return 0


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the recorder's own flags to the harness's shared parser."""
    parser.add_argument("--member-id", help="the WSL member Id")
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
    parser.add_argument(
        "--supersede",
        help="prior event id to correct: a date change, or a reclassification within "
        "endings (departed <-> vacated); never an ending into a beginning",
    )
    parser.add_argument(
        "--retract",
        help="event id to withdraw with nothing in its place — a boundary the member never "
        "crossed (#468); takes only --evidence-url, the reason it never held",
    )
    parser.add_argument("--file", help="JSON array of event objects (batch)")
    parser.add_argument("--list", action="store_true", help="list current operator events")


async def _record_job(ctx: JobContext) -> JobResult:
    """Harness handler: validate + record, mapping a validation failure onto exit 2.

    ``commit=False`` and the transaction stays here, because the pre-#179b rule is not
    "commit unless dry-run": ``--list`` is read-only and committed even under
    ``--dry-run``.
    """
    session = ctx.require_session()
    raw = PendingAttestations.for_operator()
    try:
        await _run(session, ctx.args, raw)
    except OperatorEventError as exc:
        print(f"error: {exc}", file=sys.stderr)
        await session.rollback()
        return JobResult.failed({"error": str(exc)}, exit_code=EXIT_CONFIG)
    if ctx.dry_run and not ctx.args.list:
        await session.rollback()
        print("(dry-run, rolled back)")
    else:
        await session.commit()
        # After the commit, never before: a manifest must not describe a rolled-back write.
        if not await archive_after_commit(raw):
            return JobResult.degraded({"recorded": True, "raw_archived": False})
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
