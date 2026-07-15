"""Role descriptor — named slots within an Organization.

PM matches a role observation on a **structural key**, so observations auto-attach
to PM's backfilled roles natively; there is no identifier-less-backfill gap to
bridge with a name cascade. There are two role shapes with two match keys:

- **Seat roles** (power-map#261/#263, usa-wa#68) — legislative seats keyed on the
  tuple ``(organization_id, role_type, jurisdiction_id, qualifier)``. usa-wa's local
  ``Role`` carries ``jurisdiction_id`` + ``qualifier`` so a produced seat attaches to
  one of PM's pre-seeded seats. Title is **not** in PM's seat match key — PM discards
  the incoming title on a match and auto-generates it on a create — so a seat
  observation omits title entirely (:meth:`to_observation`). Whether a ``role_type`` is
  a seat is read from the local :class:`RoleType` catalog mirror (``expects_jurisdiction``,
  synced from PM's ``GET /api/v1/role-types`` per power-map#268/#271) — no hardcoded slug list.
- **Non-seat roles** (committee/leadership/staff/…) — keyed on ``(organization_id,
  title)``, the pair we send. Title-variance caveat: PM's match is exact, so a title
  differing from PM's curated form ("Vice Chair" vs "Vice-Chair") would create a new
  role; role titles are a short controlled vocabulary, so the risk is low.

What both require is **ordering**: the observation carries the org's *PM* id (and, for
a seat, the district's PM id); a seat additionally requires its ``role_type`` to be
present in the synced catalog. The :meth:`dependencies_ready` gate defers delivery (no
crash, no duplicate) until all hold, and the engine retries on later cycles.

Read strategy mirrors the org descriptor: ``feed`` but update-only — feed changes to
an already-anchored role are applied (adopt PM's title + seat structure); roles we
never produced are skipped, not mirrored (``local_match`` keys on the anchor).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization, Role
from clearinghouse_domain_legislative.role_types import RoleType
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid, parse_pm_timestamp

logger = get_logger(__name__)


class RoleDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_domain_legislative.identity.Role`` to PM."""

    entity_type = "role"
    model = Role
    anchor_column = "pm_role_id"
    deleted_column = "deleted_at"  # terminal tombstone (#31); no id re-match yet → log-and-skip
    archived_column = "archived_at"  # PM reversible archival mirror (#41/#42)
    natural_key = ("source", "source_id")
    authority = "pm"
    read_path = "/api/v1/roles"
    observe_path = "/api/v1/roles/observations"
    read_source = "feed"
    # Cohort-only producer: feed is the primary read; the bounded anchored-cohort
    # backstop re-fetches only OUR anchored rows to recover dropped feed events (#13).
    reconcile_mode = "anchored_cohort"
    write_enabled = True

    async def dependencies_ready(self, session: Any, row: Any) -> bool:
        """The role's org must be anchored — its PM id is the observation's key.

        A **seat** role (one carrying a district) additionally needs (a) its district
        anchored — ``jurisdiction_id`` is part of the seat match tuple, so without the
        PM jurisdiction id the observation could not attach and would mint a duplicate —
        and (b) its ``role_type`` present in the synced :class:`RoleType` catalog as a
        seat type, so we emit a seat-shaped observation only once we can confirm PM
        regards the type as a seat, and (c) — if the type is ``requires_qualifier``
        (power-map#273, e.g. ``state_representative``) — a non-NULL ``qualifier``, since PM
        REJECTS a positionless districted seat of such a type. Defer (retry next cycle)
        rather than mis-shape the observation; the catalog sync + jurisdiction feed fill the
        gaps. A missing qualifier on a ``requires_qualifier`` seat is a data defect that
        never resolves — the engine surfaces the perma-deferral as a throttled stuck
        WARNING (#15) rather than shipping an observation PM dead-letters."""
        if await self._org_pm_id(session, row) is None:
            return False
        if row.jurisdiction_id is not None:  # a districted row is an intended seat
            if await self._jurisdiction_pm_id(session, row) is None:
                return False
            if not await self._is_seat_role_type(session, row.role_type):
                return False
            # power-map#273 mirror: PM REJECTS a districted seat of a requires_qualifier
            # type (e.g. state_representative — per-position) arriving without a qualifier,
            # minting nothing. Catch it pre-flight — defer rather than ship an observation
            # PM dead-letters as REJECTED. A well-formed House seat carries its Position
            # qualifier (#68/#69), so this only fires on a data defect; the engine surfaces
            # a permanently-deferred entry as a throttled stuck WARNING (#15).
            if row.qualifier is None and await self._requires_qualifier(session, row.role_type):
                logger.debug(
                    "role_seat_qualifier_required_missing",
                    extra={"source_id": row.source_id, "role_type": row.role_type},
                )
                return False
            return True
        # Non-seat role: if it carries a ``role_type`` classifier (e.g. ``member``,
        # power-map#269) that the synced catalog doesn't know yet, **defer** rather than
        # emit a title-only observation that lands with a NULL role_type_id — the enrich
        # path doesn't re-propagate role_type, so the classifier would be lost until the
        # role next changes. The catalog sync (first cycle + hourly) fills it; the next
        # cycle then emits the classifier. A role with no role_type is a plain title role
        # and is ready immediately.
        if row.role_type and not await self._is_catalog_role_type(session, row.role_type):
            return False
        return True

    async def _org_pm_id(self, session: Any, row: Any) -> Any | None:
        org = await session.get(Organization, row.organization_id)
        return org.pm_organization_id if org is not None else None

    async def _jurisdiction_pm_id(self, session: Any, row: Any) -> Any | None:
        if row.jurisdiction_id is None:
            return None
        jur = await session.get(Jurisdiction, row.jurisdiction_id)
        return jur.pm_jurisdiction_id if jur is not None else None

    async def _is_seat_role_type(self, session: Any, slug: str | None) -> bool:
        """True iff ``slug`` is a known **seat** type in the local role_type catalog
        mirror (``expects_jurisdiction``, synced from PM's ``/role-types`` per
        power-map#268/#271). An empty/unsynced catalog yields False — seats defer until
        the sync runs, rather than fall through to a title-shaped observation."""
        if not slug:
            return False
        found = await session.execute(
            select(RoleType.id).where(
                RoleType.slug == slug, RoleType.expects_jurisdiction.is_(True)
            )
        )
        return found.scalar_one_or_none() is not None

    async def _requires_qualifier(self, session: Any, slug: str | None) -> bool:
        """True iff ``slug`` is a role type PM enforces a qualifier on (power-map#273 —
        ``requires_qualifier``, e.g. ``state_representative``). An absent/unknown slug or an
        unsynced catalog yields False (unconstrained) — the guard only refuses a seat when
        PM has positively declared the type qualifier-enforced."""
        if not slug:
            return False
        found = await session.execute(
            select(RoleType.id).where(RoleType.slug == slug, RoleType.requires_qualifier.is_(True))
        )
        return found.scalar_one_or_none() is not None

    async def _is_catalog_role_type(self, session: Any, slug: str | None) -> bool:
        """True iff ``slug`` is present in the local role_type catalog mirror **at all**
        (regardless of ``expects_jurisdiction``). Distinct from :meth:`_is_seat_role_type`:
        a non-seat classifier like ``member`` (power-map#269) is catalog-known but not a
        seat, and a title-shaped observation must still carry its ``role_type`` so PM
        persists the classifier (else the role lands with a NULL ``role_type_id`` and
        "all memberships" can't aggregate). An unsynced/unknown slug yields False — we
        omit ``role_type`` rather than assert one PM's catalog doesn't recognise."""
        if not slug:
            return False
        found = await session.execute(select(RoleType.id).where(RoleType.slug == slug))
        return found.scalar_one_or_none() is not None

    async def to_observation(self, session: Any, row: Any) -> dict:
        # dependencies_ready guarantees the org (and, for a seat, its district +
        # catalog-confirmed role_type) is ready before delivery.
        org_pm_id = await self._org_pm_id(session, row)
        obs: dict = {"organization_id": str(org_pm_id)}
        if row.jurisdiction_id is not None and await self._is_seat_role_type(
            session, row.role_type
        ):
            # PM matches a seat on the structural tuple (org, role_type, jurisdiction,
            # qualifier) — title is NOT in the match key. On a match PM discards the
            # incoming title (returns the pre-seeded seat id); it is consumed only on
            # the create path for an unseeded tuple, where PM auto-generates the seat
            # title (power-map#267). So we omit title entirely and let PM own it. Local
            # ``role_type`` is PM's slug verbatim (the catalog mirror is the vocab).
            # qualifier stays explicit (None for a Senate seat, matching PM's
            # NULL-qualifier seat under NULLS NOT DISTINCT).
            jur_pm_id = await self._jurisdiction_pm_id(session, row)
            obs["role_type"] = row.role_type
            obs["jurisdiction_id"] = str(jur_pm_id)
            obs["qualifier"] = row.qualifier
        else:
            obs["title"] = row.name  # non-seat roles match on (org, title)
            # Carry the classifier when the catalog knows the slug (e.g. ``member``,
            # power-map#269). PM matches on (org, title) and *persists* role_type, so
            # sending it alongside the title makes "all memberships" aggregatable
            # without inventing a title vocabulary. A non-catalog slug is omitted.
            if await self._is_catalog_role_type(session, row.role_type):
                obs["role_type"] = row.role_type
        return obs

    async def local_match(self, session: Any, record: dict) -> Any | None:
        """Map a PM role to its local row by **anchor** (``pm_role_id``).

        PM roles carry no usa-wa natural key; the durable link is the anchor.
        ``None`` for a role we never produced → :meth:`upsert_from_pm` skips it.
        Delegates to the tolerant base helper (usa-wa#86): a duplicate anchor logs
        + returns a deterministic winner rather than raising ``MultipleResultsFound``."""
        return await self._anchor_match(session, record)

    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        """Apply a PM role onto the local cache — **update-only** (see org descriptor)."""
        row = existing if existing is not None else await self.local_match(session, record)
        if row is None:
            return None
        title = record.get("title")
        if title:
            row.name = title  # adopt PM's curated title
        await self._mirror_seat_fields(session, row, record)
        if record.get("id") is not None:
            row.pm_role_id = as_ulid(record["id"])
        self.mirror_archival(row, record)  # PM archived_at → local archived_at mirror (#41/#42)
        await session.flush()
        return row

    async def _mirror_seat_fields(self, session: Any, row: Any, record: dict) -> None:
        """Adopt PM's seat structure (``role_type_slug``/``jurisdiction_id``/``qualifier``)
        onto the local row (power-map#261/usa-wa#68). PM curates the seat; the mirror
        keeps the local cache temporally uniform. Non-seat roles carry NULL seat fields
        in PM, so nothing is overwritten spuriously.

        ``role_type`` adoption is restricted to slugs the synced :class:`RoleType`
        catalog marks as seat types (``expects_jurisdiction``, power-map#268/#271). PM types
        ``role_type_slug`` as a free ``string | null`` with no OpenAPI enum, so an
        unrecognized slug — a role type not yet in the catalog, or one on a non-seat
        role — must not silently overwrite our local ``role_type``; the catalog is the
        vocab, extended by the sync as PM grows it.

        The mirror is **atomic**: if PM references a district we haven't mirrored yet,
        the whole seat update is deferred — we never persist a seat ``role_type`` +
        ``qualifier`` against a NULL/stale ``jurisdiction_id`` (an internally
        inconsistent seat row that would fall in the title index and read as a
        non-seat). The jurisdiction feed fills the district in; the next cycle
        re-applies the seat as a unit."""
        pm_jur = record.get("jurisdiction_id")
        local_jur_id = None
        if pm_jur:
            local = (
                await session.execute(
                    select(Jurisdiction).where(Jurisdiction.pm_jurisdiction_id == as_ulid(pm_jur))
                )
            ).scalar_one_or_none()
            if local is None:
                logger.warning(
                    "role_seat_jurisdiction_unmirrored",
                    extra={"pm_role_id": record.get("id"), "pm_jurisdiction_id": pm_jur},
                )
                return  # defer the whole seat update until the district is mirrored
            local_jur_id = local.id

        slug = record.get("role_type_slug")
        if await self._is_seat_role_type(session, slug):
            row.role_type = slug
        if "qualifier" in record:
            row.qualifier = record.get("qualifier")
        if local_jur_id is not None:
            row.jurisdiction_id = local_jur_id

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Role):
            return obj.updated_at
        return parse_pm_timestamp(obj.get("updated_at"))
