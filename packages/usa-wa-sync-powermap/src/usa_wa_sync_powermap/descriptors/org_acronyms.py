"""Org-acronym sub-resource sync (usa-wa#47).

Org acronyms are not a standalone entity — they are a list embedded in PM's
``OrgDetail`` (``acronyms: list[OrgAcronym]``), distinct from ``names``. The org
descriptor's ``fetch_record`` already pulls the full ``OrgDetail``, so the
acronyms ride along with no extra round-trip; ``upsert_from_pm`` mirrors them into
``canonical.organization_acronyms`` via :func:`sync_org_acronyms`.

Sibling to :mod:`usa_wa_sync_powermap.descriptors.org_names` (#45) but thinner:
PM's ``OrgAcronym`` is ``{id, acronym, is_canonical}`` only — no ``name_type``,
no dated window — so there is no date parsing or type vocab here.

Only the **read/mirror** direction is wired: usa-wa does not produce org acronyms
as a local writer (the rename producer, usa-wa#46, emits to PM and the mirror
brings it back). ``Organization.acronym`` stays the resolved current scalar — the
org descriptor's ``upsert_from_pm`` adopts PM's ``is_canonical`` entry into it
(usa-wa#65), symmetric with the ``name`` adoption; this table is the
history/association surface holding every variant.

**Skip-and-log robustness** (committee-backfill redesign, model A): sibling of the
org-name guard — the global ``(source, source_id)`` key means a ``pm_org_acronym_id``
surfacing under two orgs would raise a UniqueViolation and crash the sidecar cycle,
so :func:`sync_org_acronyms` skips-and-logs an acronym id already claimed by a
different org rather than inserting it.

PM contract (confirmed against the live API, usa-wa#47 CR): an org with **zero**
acronyms serializes ``acronyms: []`` (the key is present, never omitted). So the
org descriptor's ``if "acronyms" in record`` guard passes even for a zero-acronym
org, and :func:`sync_org_acronyms` runs with an empty list — pruning the last
locally-held acronym when PM drops it. (Unlike names, an org can legitimately
reach zero acronyms; this is why the empty-list prune path matters here.)
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import OrganizationAcronym
from clearinghouse_sync_powermap.descriptors import as_ulid

logger = get_logger(__name__)

#: ``source`` stamped on every PM-originated acronym row. The natural key is
#: ``(source, source_id)``; ``source_id`` is PM's ``OrgAcronym`` id, so it equals
#: ``pm_org_acronym_id`` for mirrored rows.
ACRONYM_SOURCE = "powermap"


def map_pm_org_acronym(record: dict, *, organization_id: Any) -> dict:
    """Map a PM read ``OrgAcronym`` dict onto local ``OrganizationAcronym`` columns.

    ``organization_id`` comes from the parent context (the local org row), not from
    PM. PM's ``OrgAcronym`` carries no type or dated window, so this is a flat map.
    """
    return {
        "source": ACRONYM_SOURCE,
        "source_id": record["id"],
        "organization_id": organization_id,
        "acronym": record["acronym"],
        "is_canonical": bool(record.get("is_canonical")),
        "pm_org_acronym_id": as_ulid(record["id"]),
    }


async def _claimed_by_other_org(session: AsyncSession, mapped: dict, organization_id: Any) -> bool:
    """Whether the mapped acronym's natural key ``(source, source_id)`` already exists
    under a *different* org — the global-uniqueness collision the mirror must not
    crash on. Sibling of the org-name guard."""
    other = (
        await session.execute(
            select(OrganizationAcronym.organization_id).where(
                OrganizationAcronym.source == mapped["source"],
                OrganizationAcronym.source_id == mapped["source_id"],
                OrganizationAcronym.organization_id != organization_id,
            )
        )
    ).first()
    return other is not None


async def sync_org_acronyms(
    session: AsyncSession, *, organization_id: Any, pm_acronyms: list[dict]
) -> None:
    """Reconcile an org's local acronym mirror against PM's current ``acronyms[]`` set.

    Insert acronyms new to us (by ``pm_org_acronym_id`` anchor), update existing rows
    in place, and prune locally-anchored rows that PM no longer reports for this org.
    Touches only ``organization_acronyms`` — never the parent ``Organization`` — so it
    cannot trigger a spurious LWW write-back of the org.
    """
    existing = (
        (
            await session.execute(
                select(OrganizationAcronym).where(
                    OrganizationAcronym.organization_id == organization_id
                )
            )
        )
        .scalars()
        .all()
    )
    by_anchor = {row.pm_org_acronym_id: row for row in existing if row.pm_org_acronym_id}

    seen: set[Any] = set()
    for record in pm_acronyms:
        mapped = map_pm_org_acronym(record, organization_id=organization_id)
        anchor = mapped["pm_org_acronym_id"]
        seen.add(anchor)
        row = by_anchor.get(anchor)
        if row is None:
            # Defense-in-depth (redesign): the natural key ``(source, source_id)`` is
            # global, so an ``OrgAcronym`` id already mirrored under a *different* org
            # would raise a UniqueViolation on flush and crash the sidecar cycle. The
            # guarded pm_match makes this not happen; here we make it non-fatal.
            if await _claimed_by_other_org(session, mapped, organization_id):
                logger.warning(
                    "org_acronym_mirror_skip_claimed",
                    extra={
                        "pm_org_acronym_id": mapped["source_id"],
                        "organization_id": str(organization_id),
                    },
                )
                continue
            session.add(OrganizationAcronym(**mapped))
        else:
            for column, value in mapped.items():
                setattr(row, column, value)

    for anchor, row in by_anchor.items():
        if anchor not in seen:
            await session.delete(row)

    await session.flush()
