"""Assignment descriptor — Person × Role × period, observed by (person, role, start_date).

Like roles, an assignment observation carries PM **foreign keys**
``(person_id, role_id)`` so it auto-attaches to PM's backfilled assignments
natively; no name cascade. PM's match key is ``(person, role, start_date)`` with
**NULLS NOT DISTINCT** (power-map#177/#289): a NULL ``start_date`` is the single
"undated" tenure per ``(person, role)``, while distinct dated ``start_date``s
**coexist** as separate rows — so a member with non-contiguous tenure in one role
(a dormancy gap) lands as distinct assignments, one per span, because
:meth:`to_observation` sends each span's ``valid_from`` as ``start_date``.
The ordering requirement is the strongest in the cluster: both the person *and* the
role must be anchored before the observation can be built. :meth:`dependencies_ready`
enforces that (and that the assignment actually has a person — an assignment carrying
only a raw holder name cannot be expressed to PM, so it stays deferred until a Person
exists).

Read is ``feed`` update-only (adopt PM's ``is_current``/dates; skip assignments we
never produced; ``local_match`` keys on the anchor).
"""

from datetime import date, datetime
from typing import Any

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid, parse_pm_timestamp

logger = get_logger(__name__)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class AssignmentDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_domain_legislative.identity.Assignment`` to PM."""

    entity_type = "role_assignment"
    model = Assignment
    anchor_column = "pm_assignment_id"
    deleted_column = "deleted_at"  # terminal tombstone (#31); no id re-match yet → log-and-skip
    archived_column = "archived_at"  # PM reversible archival mirror (#41/#42)
    natural_key = ("source", "source_id")
    authority = "pm"
    read_path = "/api/v1/assignments"
    observe_path = "/api/v1/assignments/observations"
    read_source = "feed"
    # Cohort-only producer: feed is the primary read; the bounded anchored-cohort
    # backstop re-fetches only OUR anchored rows to recover dropped feed events (#13).
    reconcile_mode = "anchored_cohort"
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
        """Map a PM assignment to its local row by **anchor** (``pm_assignment_id``).

        Delegates to the tolerant base helper (usa-wa#86): a duplicate anchor logs
        + returns a deterministic winner rather than raising ``MultipleResultsFound``."""
        return await self._anchor_match(session, record)

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
        self.mirror_archival(row, record)  # PM archived_at → local archived_at mirror (#41/#42)
        await session.flush()
        return row

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Assignment):
            return obj.updated_at
        return parse_pm_timestamp(obj.get("updated_at"))

    def observation_matches_record(self, observation: dict, record: dict) -> bool:
        """Whether re-producing ``observation`` would leave PM's ``record`` unchanged.

        For an anchored assignment PM's match key ``(person, role, start_date)`` is
        immutable, so only ``is_current`` and the ``start_date``/``end_date`` window
        can drift. When all three agree, re-observing is a PM no-op — so a local row
        reading "newer" than PM on an *identical* payload may adopt PM's clock instead
        of re-POSTing forever (the usa-wa#102 churn). Consumed by the one-shot heal and
        the ``apply_record`` local-newer gate."""
        return (
            bool(observation.get("is_current")) == bool(record.get("is_current"))
            and _parse_date(observation.get("start_date")) == _parse_date(record.get("start_date"))
            and _parse_date(observation.get("end_date")) == _parse_date(record.get("end_date"))
        )

    async def local_newer_is_noop(self, session: Any, existing: Any, record: dict) -> bool:
        """#102: a local-newer assignment is spurious when re-producing it wouldn't change PM.

        Builds the observation we would send and compares its mutable fields to PM's record. When
        they match, ``apply_record`` adopts PM's clock instead of enqueuing an identical payload
        forever — and the one-shot heal uses the same test."""
        return self.observation_matches_record(await self.to_observation(session, existing), record)
