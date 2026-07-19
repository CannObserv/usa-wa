"""One-shot heal for the chronic LWW assignment-clock ping-pong (#102).

A 2026-07-06 span backfill bumped ~4,300 anchored assignments' local ``updated_at`` ahead of
PM's clock, so the sidecar re-POSTs an *identical* observation every reconcile forever (PM no-ops
it without advancing its clock) until PM 429s. This heal adopts PM's clock — **only** the clock,
since for assignments WE are the authority — when the observation would not change PM, so LWW sees
parity and the churn stops. A genuine pending change (observation differs from PM) is left for the
reconcile to push. This suite pins: adopt-on-unchanged, leave-on-drift, no-op-at-parity, PM-404
skip, empty-cohort abort, and the mutable-field drift check.
"""

from datetime import UTC, date, datetime

from ulid import ULID

from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from clearinghouse_sync_powermap.client import RetryableClientError
from usa_wa_sync_powermap import heal_assignment_clocks as heal
from usa_wa_sync_powermap.descriptors import AssignmentDescriptor


async def _add_assignment(
    session,
    *,
    anchor,
    updated_at=None,
    valid_from=date(2025, 1, 1),
    valid_to=None,
    is_active=True,
    source_id="A-1",
):
    """An anchored assignment (person + role anchored) with an optionally forced local clock."""
    org = Organization(
        source="usa_wa_legislature",
        source_id=f"ORG-{source_id}",
        name="House",
        org_type="chamber",
        pm_organization_id=ULID(),
    )
    session.add(org)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id=f"R-{source_id}",
        organization_id=org.id,
        name="Member",
        role_type="elected_member",
        pm_role_id=ULID(),
    )
    person = Person(
        source="usa_wa_legislature",
        source_id=f"M-{source_id}",
        name_full="Jane Doe",
        pm_person_id=ULID(),
    )
    session.add_all([role, person])
    await session.flush()
    row = Assignment(
        source="usa_wa_legislature",
        source_id=source_id,
        person_id=person.id,
        role_id=role.id,
        valid_from=valid_from,
        valid_to=valid_to,
        is_active=is_active,
        pm_assignment_id=anchor,
    )
    session.add(row)
    await session.flush()
    if updated_at is not None:  # force a local clock ahead of (or behind) PM
        row.updated_at = updated_at
        await session.flush()
    return row


class _FakeClient:
    def __init__(self, by_id):
        self._by = by_id

    async def get_entity(self, _path, pm_id):
        return self._by.get(str(pm_id))


def _pm_record(
    pm_id,
    *,
    is_current=True,
    start_date="2025-01-01",
    end_date=None,
    updated_at="2030-06-01T00:00:00Z",
):
    return {
        "id": str(pm_id),
        "is_current": is_current,
        "start_date": start_date,
        "end_date": end_date,
        "updated_at": updated_at,
    }


async def test_heal_adopts_pm_clock_when_observation_unchanged(db_session):
    anchor = ULID()
    row = await _add_assignment(
        db_session, anchor=anchor, updated_at=datetime(2031, 1, 1, tzinfo=UTC)
    )
    # PM reflects the same tenure (same start/end/is_current) but an older clock — the churn case.
    client = _FakeClient({str(anchor): _pm_record(anchor, start_date="2025-01-01", end_date=None)})
    descriptor = AssignmentDescriptor()

    result = await heal.heal_assignment_clocks(db_session, descriptor, client)

    assert result["healed"] == 1 and result["pending_change"] == 0
    # clock adopted to PM parity; the assignment's DATA is untouched (we adopt only the clock)
    assert descriptor.last_updated(row) == descriptor.last_updated(
        {"updated_at": "2030-06-01T00:00:00Z"}
    )
    assert row.valid_from == date(2025, 1, 1) and row.valid_to is None and row.is_active is True


async def test_heal_leaves_genuine_pending_change(db_session):
    anchor = ULID()
    row = await _add_assignment(
        db_session, anchor=anchor, updated_at=datetime(2031, 1, 1, tzinfo=UTC)
    )
    # PM has a DIFFERENT end_date → our observation WOULD change PM → a real pending update.
    client = _FakeClient({str(anchor): _pm_record(anchor, end_date="2020-12-31")})
    descriptor = AssignmentDescriptor()
    before = descriptor.last_updated(row)

    result = await heal.heal_assignment_clocks(db_session, descriptor, client)

    assert result["healed"] == 0 and result["pending_change"] == 1
    assert descriptor.last_updated(row) == before  # clock NOT adopted — the reconcile must push it


async def test_heal_noop_when_local_not_ahead(db_session):
    anchor = ULID()
    # local clock older than PM → LWW already lets PM win; nothing to heal.
    await _add_assignment(db_session, anchor=anchor, updated_at=datetime(2029, 1, 1, tzinfo=UTC))
    client = _FakeClient({str(anchor): _pm_record(anchor)})
    result = await heal.heal_assignment_clocks(db_session, AssignmentDescriptor(), client)
    assert result["at_parity"] == 1 and result["healed"] == 0


async def test_heal_skips_pm_404(db_session):
    anchor = ULID()
    await _add_assignment(db_session, anchor=anchor, updated_at=datetime(2031, 1, 1, tzinfo=UTC))
    result = await heal.heal_assignment_clocks(db_session, AssignmentDescriptor(), _FakeClient({}))
    assert result["skipped_missing_pm"] == 1 and result["healed"] == 0


async def test_heal_empty_cohort_aborts(db_session):
    result = await heal.heal_assignment_clocks(db_session, AssignmentDescriptor(), _FakeClient({}))
    assert result["aborted"] == "empty_cohort"


async def test_heal_is_idempotent(db_session):
    """CR finding 4: a second run of a mutating CLI must be a no-op. After healing, local == PM,
    so the row reads at_parity — healed=0 on the re-run (unlike the committee heal, which
    force-adopts unconditionally and re-reports healed)."""
    anchor = ULID()
    await _add_assignment(db_session, anchor=anchor, updated_at=datetime(2031, 1, 1, tzinfo=UTC))
    client = _FakeClient({str(anchor): _pm_record(anchor)})
    descriptor = AssignmentDescriptor()

    first = await heal.heal_assignment_clocks(db_session, descriptor, client)
    assert first["healed"] == 1
    second = await heal.heal_assignment_clocks(db_session, descriptor, client)
    assert second["healed"] == 0 and second["at_parity"] == 1


class _RetryOnceClient:
    """get_entity raises RetryableClientError (a 429) once, then succeeds — for the backoff test."""

    def __init__(self, record):
        self._record = record
        self.calls = 0

    async def get_entity(self, _path, pm_id):
        self.calls += 1
        if self.calls == 1:
            raise RetryableClientError("PM 429")
        return self._record


async def test_fetch_record_retries_retryable_error_then_succeeds():
    """CR finding 2: the heal deploys against a 429-ing PM, so a transient RetryableClientError
    must be retried on the bounded schedule, not crash the whole run."""
    slept: list[float] = []

    async def _sleep(delay):
        slept.append(delay)

    anchor = ULID()
    client = _RetryOnceClient({"id": str(anchor)})
    record = await heal._fetch_record(AssignmentDescriptor(), client, anchor, sleep=_sleep)

    assert record == {"id": str(anchor)}
    assert client.calls == 2  # first 429 retried
    assert slept == [heal._BACKOFF_SECONDS[0]]  # one backoff before the successful retry


def test_observation_matches_record_compares_mutable_fields():
    d = AssignmentDescriptor()
    obs = {
        "person_id": "p",
        "role_id": "r",
        "start_date": "2025-01-01",
        "end_date": None,
        "is_current": True,
    }
    assert d.observation_matches_record(
        obs, {"is_current": True, "start_date": "2025-01-01", "end_date": None}
    )
    assert not d.observation_matches_record(
        obs, {"is_current": False, "start_date": "2025-01-01", "end_date": None}
    )
    assert not d.observation_matches_record(
        obs, {"is_current": True, "start_date": "2024-01-01", "end_date": None}
    )
    assert not d.observation_matches_record(
        obs, {"is_current": True, "start_date": "2025-01-01", "end_date": "2026-01-01"}
    )
