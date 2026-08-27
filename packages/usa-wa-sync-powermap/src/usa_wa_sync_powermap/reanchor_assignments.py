"""Re-resolve assignment anchors PM reminted, by natural key (#283).

An org merge in PM (power-map#467) migrated a role's assignments by *copy-and-delete*:
the rows survived, every ULID changed, and no old→new mapping was recorded. The restore
(power-map#469) brought the org and role back under their original ids but could not
recover the assignment ids. So a local ``pm_assignment_id`` can point at a PM row that
404s while the assignment itself is alive upstream under a new id.

That is invisible to the sidecar. An anchored row is addressed PM-natively by id, so it
never re-matches; ``_heal_dead_anchor`` leaves it alone because assignments cannot
identifier-rematch (``supports_rematch`` is False) — it just logs ``dead_anchor_unhealed``
once per row per process, with no aggregate. 138 rows sat in that state for two days.

**Clear-and-re-produce is the wrong repair.** The rows exist in PM, so an unanchored
CREATE would mint a duplicate beside each one. The anchor has to be re-resolved on PM's
own uniqueness key for an assignment, ``(person, role, start_date)`` — which is exactly
what PM's assignment observation dedups on, so a match is exact rather than heuristic.

For each role (optionally scoped), this fetches PM's assignments for that role once and
re-anchors every local row whose id is absent from PM's set but whose ``(person,
start_date)`` matches a returned record. A dead anchor with **no** natural-key match is
left untouched and counted: clearing it would hand the row to the CREATE path, which is
the duplicate we are avoiding. Every re-anchor writes an :class:`AnchorReanchor` ledger
row — the same durable old→new record the #108 in-place overwrite writes, and the only
handle on the id PM dropped.

Anchor + clock writes on a canonical table → app role. Read-only against PM. No operator
token (the shell is the trust boundary, as for the other one-shots). ``--dry-run``
previews; exit ``0`` clean · ``2`` auth · ``3`` empty-cohort abort.

    python -m usa_wa_sync_powermap.reanchor_assignments --dry-run
    python -m usa_wa_sync_powermap.reanchor_assignments --role-source-id committee-member-role:3532
"""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from clearinghouse_domain_legislative.queries import live_only
from clearinghouse_sync_powermap.descriptors import as_ulid
from clearinghouse_sync_powermap.models import AnchorReanchor
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import AssignmentDescriptor
from usa_wa_sync_powermap.jobs import never, run_pm_job
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "pm-assignment-reanchor"

#: Exit code for a guardrail abort (empty cohort).
EXIT_ABORTED = 3

#: ``disposition`` recorded on the ledger row. Distinguishes this operator-driven
#: recovery from the ``new``/``updated`` dispositions a delivered observation writes.
DISPOSITION_NATURAL_KEY = "natural_key_reanchor"


def _parse_start(value: Any) -> date | None:
    """PM's ``start_date`` as a ``date``. PM serialises ISO ``YYYY-MM-DD``; anything else
    is treated as unmatchable rather than raising mid-sweep."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _natural_key_index(records: list[dict]) -> dict[tuple[str, date], dict]:
    """``{(person_pm_id, start_date): record}`` for one role's PM assignments.

    PM enforces uniqueness on ``(person, role, start_date)`` and we index within a single
    role, so a collision cannot occur upstream; if one somehow appears, the first record
    wins and the duplicate is logged rather than silently overwriting the match.
    """
    index: dict[tuple[str, date], dict] = {}
    for record in records:
        person_id, start = record.get("person_id"), _parse_start(record.get("start_date"))
        if not person_id or start is None:
            continue
        key = (str(person_id), start)
        if key in index:
            logger.warning(
                "reanchor_duplicate_natural_key",
                extra={"person_id": str(person_id), "start_date": start.isoformat()},
            )
            continue
        index[key] = record
    return index


async def _roles_in_scope(session: AsyncSession, role_source_ids: list[str] | None) -> list[Role]:
    """Live, PM-anchored roles to sweep — all of them, or just the named ones."""
    stmt = select(Role).where(Role.pm_role_id.is_not(None))
    if role_source_ids:
        stmt = stmt.where(Role.source_id.in_(role_source_ids))
    return list((await session.execute(live_only(stmt, Role))).scalars().all())


async def _anchored_on_role(session: AsyncSession, role: Role) -> list[Assignment]:
    """Live assignments on one role that already carry a PM anchor."""
    stmt = select(Assignment).where(
        Assignment.role_id == role.id, Assignment.pm_assignment_id.is_not(None)
    )
    return list((await session.execute(live_only(stmt, Assignment))).scalars().all())


async def reanchor_assignments(
    session: AsyncSession,
    descriptor: Any,
    client: Any,
    *,
    role_source_ids: list[str] | None = None,
) -> dict:
    """Re-resolve dead assignment anchors against PM's ``(person, start_date)`` key.

    Per role: read PM's assignments once, then for each anchored local row — anchor still
    served by PM → healthy, nothing to do; anchor absent but ``(person, start_date)``
    matches → adopt the new id and ledger the change; anchor absent with no match → leave
    it alone (never guess, never clear). Empty cohort aborts. Executes in the caller's
    transaction; does not commit.
    """
    roles = await _roles_in_scope(session, role_source_ids)
    if not roles:
        return {
            "roles_scanned": 0,
            "checked": 0,
            "healthy": 0,
            "reanchored": 0,
            "unresolved": 0,
            "skipped_no_person_anchor": 0,
            "clock_adopted": 0,
            "aborted": "empty_cohort",
        }

    checked = healthy = reanchored = unresolved = skipped = adopted = 0
    for role in roles:
        rows = await _anchored_on_role(session, role)
        if not rows:
            continue
        records = await client.list_assignments_for_role(role.pm_role_id)
        live_ids = {str(record.get("id")) for record in records}
        index = _natural_key_index(records)

        for row in rows:
            checked += 1
            if str(row.pm_assignment_id) in live_ids:
                healthy += 1
                continue
            person = await session.get(Person, row.person_id) if row.person_id else None
            if person is None or person.pm_person_id is None:
                # No PM person id → no natural key to match on. Never guessed.
                skipped += 1
                logger.warning("reanchor_no_person_anchor", extra={"source_id": row.source_id})
                continue
            record = index.get((str(person.pm_person_id), row.valid_from))
            if record is None:
                # PM serves this role but holds nothing at our (person, start). Leave the
                # dead anchor in place: clearing it would route the row to the CREATE
                # path, which is precisely the duplicate this heal exists to avoid.
                unresolved += 1
                logger.warning(
                    "reanchor_unresolved",
                    extra={
                        "source_id": row.source_id,
                        "anchor": str(row.pm_assignment_id),
                        "role_source_id": role.source_id,
                    },
                )
                continue

            old_id, new_id = row.pm_assignment_id, as_ulid(record["id"])
            session.add(
                AnchorReanchor(
                    entity_type=descriptor.entity_type,
                    local_id=row.id,
                    source_id=row.source_id,
                    old_pm_id=old_id,
                    new_pm_id=new_id,
                    disposition=DISPOSITION_NATURAL_KEY,
                )
            )
            row.pm_assignment_id = new_id
            reanchored += 1
            logger.warning(
                "reanchor_natural_key",
                extra={
                    "source_id": row.source_id,
                    "old_pm_id": str(old_id),
                    "new_pm_id": str(new_id),
                },
            )
            # Writing the anchor fires ``onupdate`` and leaves local newer than PM, so the
            # reconcile would re-POST this row every cycle (the #102 churn). Adopt PM's
            # clock when the observation would not change PM. When it *would* — a genuine
            # pending delta — keep the bumped clock so the reconcile still pushes it.
            await session.flush()
            if await descriptor.local_newer_is_noop(session, row, record):
                lu_pm = descriptor.last_updated(record)
                if lu_pm is not None:
                    descriptor.set_last_updated(row, lu_pm)
                    adopted += 1
            else:
                logger.info("reanchor_pending_change_kept", extra={"source_id": row.source_id})

    await session.flush()
    return {
        "roles_scanned": len(roles),
        "checked": checked,
        "healthy": healthy,
        "reanchored": reanchored,
        "unresolved": unresolved,
        "skipped_no_person_anchor": skipped,
        "clock_adopted": adopted,
        "aborted": None,
    }


async def _run(dry_run: bool, factory: Any, role_source_ids: list[str] | None) -> dict:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — cannot read from Power Map.")
    async with factory() as session:
        client = build_pm_client(settings)
        try:
            result = await reanchor_assignments(
                session, AssignmentDescriptor(), client, role_source_ids=role_source_ids
            )
            if dry_run:
                await session.rollback()
                result = {**result, "dry_run": True}
            else:
                await session.commit()
            return result
        finally:
            await client.aclose()


async def _reanchor_job(ctx: JobContext) -> JobResult:
    """Harness handler. ``commit=False`` and ``_run`` keeps its own session/commit, as for
    the other PM producer CLIs (they interleave PM reads with local writes)."""
    factory = ctx.require_session_factory()
    scope = getattr(ctx.args, "role_source_id", None) or None
    return await run_pm_job(lambda: _run(ctx.dry_run, factory, scope), failed_when=never)


def _add_arguments(parser: Any) -> None:
    parser.add_argument(
        "--role-source-id",
        action="append",
        metavar="SOURCE_ID",
        help=(
            "Limit the sweep to this role's source_id (repeatable). Omit to sweep every "
            "PM-anchored role — one paged PM read per role."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """Re-resolve dead assignment anchors by natural key.

    Exit codes: ``0`` clean or dry-run; ``2`` a global auth block; ``3`` a guardrail
    abort (empty cohort), ledgered as ``degraded``.
    """
    return run_job(
        JOB_SLUG,
        _reanchor_job,
        argv=argv,
        prog="python -m usa_wa_sync_powermap.reanchor_assignments",
        description=(
            "Re-resolve assignment anchors PM reminted during a merge, matching on PM's "
            "(person, role, start_date) uniqueness key rather than re-producing (#283)."
        ),
        commit=False,
        extra_args=_add_arguments,
    )


if __name__ == "__main__":
    raise SystemExit(main())
