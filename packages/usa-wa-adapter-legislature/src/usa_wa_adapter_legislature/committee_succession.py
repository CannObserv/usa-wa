"""Committee-succession attestation CLI (usa-wa#124 C2) — the live interjection surface.

    python -m usa_wa_adapter_legislature.committee_succession \
        --subject 14294 --linked 28244 --slug succeeded_by --year 2021 \
        --evidence-url https://... [--notes "renamed + re-scoped"] [--entered-by greg]

    python -m usa_wa_adapter_legislature.committee_succession --file links.json   # batch
    python -m usa_wa_adapter_legislature.committee_succession --supersede <id> ... # correction
    python -m usa_wa_adapter_legislature.committee_succession --list               # inspect

App-role DML (writes ``committee_succession_events`` + provenance under
``usa_wa_operator``); shell access is the trust boundary, as with #107. Validates that
**both** ``--subject`` and ``--linked`` resolve to live ``usa_wa_legislature`` committee
Orgs before writing (a typo'd WSL Id would otherwise be a silent no-op link). ``--dry-run``
rolls back. The event producer (C3) emits each as a PM ``succeeded_by`` / ``split_from`` /
``merged_with`` linked-entity event; a re-link correction via ``--supersede`` is applied as
create-new + retract-old (power-map#322).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.committee_succession import (
    SLUGS,
    CommitteeSuccessionEvent,
)
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.queries import live_only
from usa_wa_adapter_legislature.committee_succession_store import (
    INHERIT_YEAR,
    current_events,
    get_or_create_operator_source,
    record_succession_event,
    supersede_event,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction

logger = get_logger(__name__)

#: The producer source of committee Orgs — both ends of a link must be one of these.
_COMMITTEE_SOURCE = "usa_wa_legislature"
_COMMITTEE_ORG_TYPE = "committee"


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


async def _resolve_committee(session: AsyncSession, source_id: str) -> Organization | None:
    """The live ``usa_wa_legislature`` committee Org for a WSL ``Id``, or None."""
    return (
        await session.execute(
            live_only(
                select(Organization).where(
                    Organization.source == _COMMITTEE_SOURCE,
                    Organization.org_type == _COMMITTEE_ORG_TYPE,
                    Organization.source_id == source_id,
                ),
                Organization,
            )
        )
    ).scalar_one_or_none()


async def validate_and_record(
    session: AsyncSession, source, spec: LinkSpec
) -> CommitteeSuccessionEvent:
    """Validate ``spec`` (shape + both ends resolve to committee Orgs) and persist it.

    A ``supersede_id`` records a correction of that prior link (a re-link or year change).
    Raises :class:`SuccessionError` on any validation failure (no partial write)."""
    _validate_shape(spec)
    for role, sid in (("subject", spec.subject_source_id), ("linked", spec.linked_source_id)):
        if await _resolve_committee(session, sid) is None:
            raise SuccessionError(
                f"--{role} {sid!r} resolves to no live usa_wa_legislature committee Org "
                "(typo, or run the committee harvest first)"
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


async def _run(session: AsyncSession, args: argparse.Namespace) -> int:
    if args.list:
        events = await current_events(session)
        for event in events:
            print(_format_event(event))
        print(f"{len(events)} current committee-succession link(s)")
        return 0

    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_operator_source(session, jurisdiction)

    if args.file:
        with open(args.file) as handle:
            specs = load_specs(json.load(handle))
    else:
        specs = [_spec_from_args(args)]

    recorded = [await validate_and_record(session, source, spec) for spec in specs]
    for event in recorded:
        print(_format_event(event))
    print(f"recorded {len(recorded)} committee-succession link(s)")
    return 0


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Record operator committee-succession links (usa-wa#124 C2)."
    )
    parser.add_argument("--subject", help="the subject WSL committee Id (the event host / PM org)")
    parser.add_argument("--linked", help="the linked WSL committee Id (PM linked_entity)")
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
    parser.add_argument("--dry-run", action="store_true", help="validate + write, then roll back")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            try:
                code = await _run(session, args)
            except SuccessionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                await session.rollback()
                return 2
            if args.dry_run and not args.list:
                await session.rollback()
                print("(dry-run, rolled back)")
            else:
                await session.commit()
            return code
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
