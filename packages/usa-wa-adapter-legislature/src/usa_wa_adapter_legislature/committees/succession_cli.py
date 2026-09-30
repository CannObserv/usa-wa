"""Committee-succession attestation CLI (usa-wa#124 C2) — the live interjection surface.

    python -m usa_wa_adapter_legislature.committees.succession_cli \
        --subject 14294 --linked 28244 --slug succeeded_by --year 2021 \
        --evidence-url https://... [--notes "renamed + re-scoped"]

    python -m usa_wa_adapter_legislature.committees.succession_cli --file links.json   # batch
    python -m usa_wa_adapter_legislature.committees.succession_cli --supersede <id> ... # correction
    python -m usa_wa_adapter_legislature.committees.succession_cli --list               # inspect

App-role DML (writes ``committee_succession_events`` + provenance under
``usa_wa_operator``); shell access is the trust boundary, as with #107. Validates that
**both** ``--subject`` and ``--linked`` are registered ``usa_wa_legislature`` committee
orgs before writing (a typo'd WSL Id would otherwise be a silent no-op link): an integer
WSL Id (negative for some Other bodies) — standing, Joint or Other, never a structural
org. The registry is the authority, not the canonical tier #412 froze (#445).
``--dry-run`` rolls back. A ``--supersede`` correction is a new row stamping the prior's
``superseded_by_id`` (provenance stays append-only). ``entered_by`` is recorded from
``$USA_WA_OPERATOR``, else ``$USER`` — there is no flag for it.

Links are recorded locally only. The C3 producer that pushed each to PM as a linked-entity
event retired with the sync (#314), and no published dataset carries them yet — a
succession dataset is deferred (``docs/PIPELINE.md`` § Ported from the canonical tier).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import (
    EXIT_CONFIG,
    JobContext,
    JobResult,
    load_json_batch,
    run_job,
)
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import KIND_ORG, RegistryKey
from clearinghouse_domain_legislative.committee_succession import (
    SLUGS,
    CommitteeSuccessionEvent,
)
from usa_wa_adapter_legislature.committees.succession_store import (
    INHERIT_YEAR,
    current_events,
    get_or_create_operator_source,
    record_succession_event,
    supersede_event,
)
from usa_wa_adapter_legislature.operators.raw import PendingAttestations, archive_after_commit
from usa_wa_common.jurisdiction import resolve_jurisdiction
from usa_wa_common.orgs import STRUCTURAL_ORGS

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "committee-succession-record"

#: The namespace of committee org keys — both ends of a link must be registered in it.
_COMMITTEE_SOURCE = "usa_wa_legislature"

#: A WSL committee ``Id`` is an integer — negative for some Other bodies (JLARC is ``-5``).
_WSL_COMMITTEE_ID = re.compile(r"-?\d+")


class SuccessionError(ValueError):
    """A validation failure the CLI surfaces (exit 2), not a stack trace."""


@dataclass(frozen=True)
class LinkSpec:
    """One succession link's fields, pre-validation (from CLI args or a --file row)."""

    subject_source_id: str
    linked_source_id: str
    slug: str
    evidence_url: str
    effective_year: int | None = None
    notes: str | None = None
    supersede_id: str | None = None
    #: Supersede-only: clear the prior link's boundary year (distinct from omitting
    #: ``--year``, which inherits it). Requires ``supersede_id``.
    clear_year: bool = False


def _validate_shape(spec: LinkSpec) -> None:
    """DB-independent validation (slug + distinct ends + clear-year usage)."""
    if spec.slug not in SLUGS:
        raise SuccessionError(f"unknown slug {spec.slug!r} (expected one of {sorted(SLUGS)})")
    if spec.subject_source_id == spec.linked_source_id:
        raise SuccessionError("--subject and --linked must differ (a link joins two orgs)")
    if spec.clear_year and spec.supersede_id is None:
        raise SuccessionError(
            "--clear-year only applies with --supersede (a fresh link has no year)"
        )
    if spec.clear_year and spec.effective_year is not None:
        raise SuccessionError("--clear-year and --year are mutually exclusive")


async def _is_registered_committee(session: AsyncSession, source_id: str) -> bool:
    """Whether a WSL ``Id`` is a registered committee org (#445).

    The registrar binds every staged committee id — standing, Joint and Other alike —
    plus the ``STRUCTURAL_ORGS`` ids under one namespace, so a committee is a registered
    key that is not structural. The integer shape backs the denylist: a key never unbinds,
    so a structural org later dropped from ``STRUCTURAL_ORGS`` keeps its key, and only the
    shape still refuses it (CR 2). A merge chain ends at a live survivor, so registered
    means live.
    """
    if source_id in STRUCTURAL_ORGS or not _WSL_COMMITTEE_ID.fullmatch(source_id):
        return False
    key = await session.scalar(
        select(RegistryKey.id).where(
            RegistryKey.kind == KIND_ORG,
            RegistryKey.natural_key == f"{_COMMITTEE_SOURCE}:{source_id}",
        )
    )
    return key is not None


async def validate_and_record(
    session: AsyncSession, source, spec: LinkSpec, *, raw: PendingAttestations | None = None
) -> CommitteeSuccessionEvent:
    """Validate ``spec`` (shape + both ends registered committee orgs) and persist it.

    A ``supersede_id`` records a correction of that prior link (a re-link or year change).
    Raises :class:`SuccessionError` on any validation failure (no partial write)."""
    _validate_shape(spec)
    for role, sid in (("subject", spec.subject_source_id), ("linked", spec.linked_source_id)):
        if not await _is_registered_committee(session, sid):
            raise SuccessionError(
                f"--{role} {sid!r} is no registered usa_wa_legislature committee org "
                "(typo, a structural org, or not yet registered — the nightly registrar "
                "binds a committee the build after it is first staged)"
            )
    if spec.supersede_id is not None:
        prior = (
            await session.execute(
                select(CommitteeSuccessionEvent).where(
                    CommitteeSuccessionEvent.id == spec.supersede_id
                )
            )
        ).scalar_one_or_none()
        if prior is None:
            raise SuccessionError(f"--supersede id {spec.supersede_id!r} not found")
        if spec.slug != prior.slug:
            raise SuccessionError(
                f"--supersede: slug {spec.slug!r} differs from the prior link's {prior.slug!r} "
                "(a supersede corrects the successor/year/evidence, not the relation type)"
            )
        if spec.subject_source_id != prior.subject_source_id:
            raise SuccessionError(
                f"--supersede: subject {spec.subject_source_id!r} differs from the prior link's "
                f"{prior.subject_source_id!r} (record a new link instead)"
            )
        # Three-state year intent: --clear-year → None (clear); --year N → N (set);
        # neither → INHERIT_YEAR (keep prior's).
        if spec.clear_year:
            year_arg = None
        elif spec.effective_year is not None:
            year_arg = spec.effective_year
        else:
            year_arg = INHERIT_YEAR
        return await supersede_event(
            session,
            source,
            prior,
            linked_source_id=spec.linked_source_id,
            effective_year=year_arg,
            evidence_url=spec.evidence_url,
            notes=spec.notes,
            entered_by=_entered_by(),
            raw=raw,
        )
    return await record_succession_event(
        session,
        source,
        subject_source_id=spec.subject_source_id,
        linked_source_id=spec.linked_source_id,
        slug=spec.slug,
        effective_year=spec.effective_year,
        evidence_url=spec.evidence_url,
        notes=spec.notes,
        entered_by=_entered_by(),
        raw=raw,
    )


def _entered_by() -> str | None:
    """The operator, best-effort from the environment (audit; git isn't the trail here)."""
    return os.environ.get("USA_WA_OPERATOR") or os.environ.get("USER")


def _int_or_none(value: object) -> int | None:
    return None if value is None else int(value)


def load_specs(payload: object) -> list[LinkSpec]:
    """Parse a --file JSON body (a list of link objects) into :class:`LinkSpec`s."""
    if not isinstance(payload, list):
        raise SuccessionError("--file must contain a JSON array of link objects")
    specs: list[LinkSpec] = []
    for i, row in enumerate(payload):
        if not isinstance(row, dict):
            raise SuccessionError(f"--file row {i} is not an object")
        try:
            specs.append(
                LinkSpec(
                    subject_source_id=str(row["subject"]),
                    linked_source_id=str(row["linked"]),
                    slug=str(row["slug"]),
                    evidence_url=str(row["evidence_url"]),
                    effective_year=_int_or_none(row.get("year")),
                    notes=row.get("notes"),
                    supersede_id=row.get("supersede_id"),
                    clear_year=bool(row.get("clear_year", False)),
                )
            )
        except KeyError as exc:
            raise SuccessionError(f"--file row {i} missing required field {exc}") from exc
    return specs


def _spec_from_args(args: argparse.Namespace) -> LinkSpec:
    if not all([args.subject, args.linked, args.slug, args.evidence_url]):
        raise SuccessionError("a single link needs --subject --linked --slug --evidence-url")
    return LinkSpec(
        subject_source_id=args.subject,
        linked_source_id=args.linked,
        slug=args.slug,
        evidence_url=args.evidence_url,
        effective_year=_int_or_none(args.year),
        notes=args.notes,
        supersede_id=args.supersede,
        clear_year=args.clear_year,
    )


def _format_event(event: CommitteeSuccessionEvent) -> str:
    year = f" ({event.effective_year})" if event.effective_year is not None else ""
    return (
        f"{event.id}  {event.subject_source_id} -{event.slug}-> {event.linked_source_id}"
        f"{year}  {event.evidence_url}"
    )


async def _run(session: AsyncSession, args: argparse.Namespace, raw: PendingAttestations) -> int:
    if args.list:
        events = await current_events(session)
        for event in events:
            print(_format_event(event))
        print(f"{len(events)} current committee-succession link(s)")
        return 0

    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_operator_source(session, jurisdiction)

    if args.file:
        specs = await load_json_batch(args.file, load_specs)
    else:
        specs = [_spec_from_args(args)]

    recorded = [await validate_and_record(session, source, spec, raw=raw) for spec in specs]
    for event in recorded:
        print(_format_event(event))
    print(f"recorded {len(recorded)} committee-succession link(s)")
    return 0


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the recorder's own flags to the harness's shared parser."""
    parser.add_argument(
        "--subject", help="the subject WSL committee Id (predecessor / split child / merged body)"
    )
    parser.add_argument(
        "--linked", help="the linked WSL committee Id (successor / parent / surviving body)"
    )
    parser.add_argument(
        "--slug", choices=sorted(SLUGS), help="succeeded_by | split_from | merged_with"
    )
    parser.add_argument("--year", type=int, help="optional boundary year")
    parser.add_argument(
        "--clear-year",
        action="store_true",
        help="supersede-only: clear the prior link's year (vs omitting --year = inherit)",
    )
    parser.add_argument("--evidence-url", help="operator-cited source (news/official)")
    parser.add_argument("--notes", help="free-text note")
    parser.add_argument("--supersede", help="prior link id to correct (re-link or year change)")
    parser.add_argument("--file", help="JSON array of link objects (batch)")
    parser.add_argument("--list", action="store_true", help="list current succession links")


async def _record_job(ctx: JobContext) -> JobResult:
    """Harness handler: validate + record, mapping a validation failure onto exit 2.

    ``commit=False`` and the transaction stays here, because the pre-#179b rule is not
    "commit unless dry-run": ``--list`` is read-only and committed even under
    ``--dry-run``. A ``SuccessionError`` is ``failed`` in the ledger with the documented
    ``2`` on the wire (COMMANDS-SUCCESSION.md).
    """
    session = ctx.require_session()
    raw = PendingAttestations.for_operator()
    try:
        await _run(session, ctx.args, raw)
    except SuccessionError as exc:
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
    """Record operator succession links. Exit ``0`` clean · ``2`` validation/config."""
    return run_job(
        JOB_SLUG,
        _record_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.committees.succession_cli",
        description="Record operator committee-succession links (usa-wa#124 C2).",
        extra_args=_add_args,
        commit=False,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
