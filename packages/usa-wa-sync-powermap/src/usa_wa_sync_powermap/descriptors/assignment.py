"""Assignment descriptor — Person × Role × period, observed by (person, role).

Like roles, an assignment observation carries PM **foreign keys**
``(person_id, role_id)`` — PM's structural match key — so it auto-attaches to
PM's backfilled assignments natively; no name cascade. The ordering requirement is
the strongest in the cluster: both the person *and* the role must be anchored
before the observation can be built. :meth:`dependencies_ready` enforces that (and
that the assignment actually has a person — an assignment carrying only a raw
holder name cannot be expressed to PM, so it stays deferred until a Person exists).

Read is ``feed`` update-only (adopt PM's ``is_current``/dates; skip assignments we
never produced; ``local_match`` keys on the anchor).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid

logger = get_logger(__name__)


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class AssignmentDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_domain_legislative.identity.Assignment`` to PM."""

    entity_type = "role_assignment"
    model = Assignment
    anchor_column = "pm_assignment_id"
    natural_key = ("source", "source_id")
    authority = "pm"
    read_path = "/api/v1/assignments"
    observe_path = "/api/v1/assignments/observations"
    read_source = "feed"
    reconcile_enabled = False  # cohort-only producer; feed is the only read (see #13)
    write_enabled = True

    async def dependencies_ready(self, session: Any, row: Any) -> bool:
        """Both the person and the role must be anchored — the observation keys on
        their PM ids. An assignment with no ``person_id`` (raw holder name only)
        can never be expressed to PM, so it stays deferred."""
        if row.person_id is None:
            return False
        person = await session.get(Person, row.person_id)
        if person is None or person.pm_person_id is None:
            return False
        role = await session.get(Role, row.role_id)
        return role is not None and role.pm_role_id is not None

    async def to_observation(self, session: Any, row: Any) -> dict:
        # dependencies_ready guarantees both anchors exist before delivery.
        person = await session.get(Person, row.person_id)
        role = await session.get(Role, row.role_id)
        return {
            "person_id": str(person.pm_person_id),
            "role_id": str(role.pm_role_id),
            "start_date": row.valid_from.isoformat() if row.valid_from else None,
            "end_date": row.valid_to.isoformat() if row.valid_to else None,
            "is_current": row.is_active,
        }

    async def local_match(self, session: Any, record: dict) -> Any | None:
        """Map a PM assignment to its local row by **anchor** (``pm_assignment_id``)."""
        pm_id = record.get("id")
        if pm_id is None:
            return None
        return (
            await session.execute(
                select(Assignment).where(Assignment.pm_assignment_id == as_ulid(pm_id))
            )
        ).scalar_one_or_none()

    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        """Apply a PM assignment onto the local cache — **update-only**."""
        row = existing if existing is not None else await self.local_match(session, record)
        if row is None:
            return None
        if "is_current" in record and record["is_current"] is not None:
            row.is_active = record["is_current"]
        start = _parse_date(record.get("start_date"))
        if start is not None:
            row.valid_from = start
        if "end_date" in record:
            row.valid_to = _parse_date(record.get("end_date"))
        if record.get("id") is not None:
            row.pm_assignment_id = as_ulid(record["id"])
        await session.flush()
        return row

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Assignment):
            return obj.updated_at
        ts = obj.get("updated_at")
        return _parse_ts(ts) if ts else None
