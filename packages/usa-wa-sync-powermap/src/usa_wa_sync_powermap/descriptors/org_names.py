"""Dated org-name sub-resource sync (usa-wa#45).

Org names are not a standalone entity — they are a list embedded in PM's
``OrgDetail`` (``names: list[OrgName]``, power-map#239). The org descriptor's
``fetch_record`` already pulls the full ``OrgDetail``, so the names ride along
with no extra round-trip; ``upsert_from_pm`` mirrors them into
``canonical.organization_names`` via :func:`sync_org_names`.

Only the **read/mirror** direction is wired: usa-wa does not produce org names as
a local writer (the rename producer, usa-wa#46, emits to PM and the mirror brings
it back). ``Organization.name`` stays the resolved current scalar; this table is
the history/association surface.

**Skip-and-log robustness** (committee-backfill redesign, model A): the natural key
``(source, source_id)`` is *global*, so a ``pm_org_name_id`` surfacing under two
local orgs (a PM merge, or a cross-Id over-match) would raise a UniqueViolation on
flush and crash the whole sidecar cycle. The guarded ``pm_match`` prevents that
match; :func:`sync_org_names` makes it *non-fatal* too — a name id already claimed
by a different org is skipped-and-logged, not inserted.
"""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import OrganizationName
from clearinghouse_sync_powermap.descriptors import as_ulid

logger = get_logger(__name__)

#: ``source`` stamped on every PM-originated name row. The natural key is
#: ``(source, source_id)``; ``source_id`` is PM's ``OrgName`` id, so it equals
#: ``pm_org_name_id`` for mirrored rows.
NAME_SOURCE = "powermap"


def _parse_date(value: Any) -> date | None:
    """Coerce a PM ``effective_*`` value (ISO ``YYYY-MM-DD`` str, ``date``, or null)
    to a ``date`` or ``None``. The generated client may hand back either a parsed
    ``date`` or the raw string depending on the payload path, so accept both."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def map_pm_org_name(record: dict, *, organization_id: Any) -> dict:
    """Map a PM read ``OrgName`` dict onto local ``OrganizationName`` column values.

    ``organization_id`` comes from the parent context (the local org row), not from
    PM. ``effective_start``/``effective_end`` are parsed from ISO dates;
    ``name_type`` is mirrored verbatim (open PM vocab — no CHECK, see the model).
    """
    return {
        "source": NAME_SOURCE,
        "source_id": record["id"],
        "organization_id": organization_id,
        "name": record["name"],
        "name_type": record.get("name_type") or "legal",
        "is_canonical": bool(record.get("is_canonical")),
        "effective_start": _parse_date(record.get("effective_start")),
        "effective_end": _parse_date(record.get("effective_end")),
        "pm_org_name_id": as_ulid(record["id"]),
    }


async def _claimed_by_other_org(session: AsyncSession, mapped: dict, organization_id: Any) -> bool:
    """Whether the mapped name's natural key ``(source, source_id)`` already exists
    under a *different* org — the global-uniqueness collision the mirror must not
    crash on."""
    other = (
        await session.execute(
            select(OrganizationName.organization_id).where(
                OrganizationName.source == mapped["source"],
                OrganizationName.source_id == mapped["source_id"],
                OrganizationName.organization_id != organization_id,
            )
        )
    ).first()
    return other is not None


async def sync_org_names(
    session: AsyncSession, *, organization_id: Any, pm_names: list[dict]
) -> None:
    """Reconcile an org's local name mirror against PM's current ``names[]`` set.

    Insert names new to us (by ``pm_org_name_id`` anchor), update existing rows in
    place, and prune locally-anchored rows that PM no longer reports for this org.
    Touches only ``organization_names`` — never the parent ``Organization`` — so it
    cannot trigger a spurious LWW write-back of the org.
    """
    existing = (
        (
            await session.execute(
                select(OrganizationName).where(OrganizationName.organization_id == organization_id)
            )
        )
        .scalars()
        .all()
    )
    by_anchor = {row.pm_org_name_id: row for row in existing if row.pm_org_name_id}

    seen: set[Any] = set()
    for record in pm_names:
        mapped = map_pm_org_name(record, organization_id=organization_id)
        anchor = mapped["pm_org_name_id"]
        seen.add(anchor)
        row = by_anchor.get(anchor)
        if row is None:
            # Defense-in-depth (redesign): the natural key ``(source, source_id)`` is
            # **global**, so an ``OrgName`` id already mirrored under a *different* org
            # (e.g. a PM merge surfacing one name under two orgs) would raise a
            # UniqueViolation on flush and crash the whole sidecar cycle. The guarded
            # pm_match makes this not happen; here we make it non-fatal — skip-and-log.
            if await _claimed_by_other_org(session, mapped, organization_id):
                logger.warning(
                    "org_name_mirror_skip_claimed",
                    extra={
                        "pm_org_name_id": mapped["source_id"],
                        "organization_id": str(organization_id),
                    },
                )
                continue
            session.add(OrganizationName(**mapped))
        else:
            for column, value in mapped.items():
                setattr(row, column, value)

    for anchor, row in by_anchor.items():
        if anchor not in seen:
            await session.delete(row)

    await session.flush()
