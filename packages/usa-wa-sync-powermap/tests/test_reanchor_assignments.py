"""Natural-key re-anchor for assignments whose ``pm_assignment_id`` died (#283).

power-map#467 migrated a merged role's assignments by copy-and-delete, reminting every
ULID and recording no old→new map; power-map#469 restored the rows but could not restore
their ids. So 138 local anchors point at PM rows that 404 while the *content* lives on
under a new id. Clear-and-re-produce would mint duplicates beside them — the anchor has
to be re-resolved on PM's own uniqueness key, ``(person, role, start_date)``.

This suite pins: re-anchor on natural-key match, leave a healthy anchor alone, leave an
unresolvable dead anchor alone (never guess), the ``AnchorReanchor`` ledger row, clock
adoption only when the payload matches PM, the role scope filter, and empty-cohort abort.
"""

from datetime import UTC, date, datetime

from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from clearinghouse_sync_powermap.models import AnchorReanchor
from usa_wa_sync_powermap import reanchor_assignments as heal
from usa_wa_sync_powermap.descriptors import AssignmentDescriptor


async def _add_role(session, *, source_id="R-1", pm_role_id=None):
    org = Organization(
        source="usa_wa_legislature",
        source_id=f"ORG-{source_id}",
        name="House Committee on Transportation",
        org_type="committee",
        pm_organization_id=ULID(),
    )
    session.add(org)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id=source_id,
        organization_id=org.id,
        name="Member",
        role_type="committee_member",
        pm_role_id=pm_role_id or ULID(),
    )
    session.add(role)
    await session.flush()
    return role


async def _add_assignment(
    session,
    role,
    *,
    anchor,
    source_id="A-1",
    valid_from=date(2019, 1, 1),
    valid_to=date(2020, 12, 31),
    is_active=False,
    pm_person_id=None,
    updated_at=None,
):
    person = Person(
        source="usa_wa_legislature",
        source_id=f"M-{source_id}",
        name_full="Jane Doe",
        pm_person_id=pm_person_id or ULID(),
    )
    session.add(person)
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
    if updated_at is not None:
        row.updated_at = updated_at
        await session.flush()
    return row, person


def _pm_record(
    pm_id,
    person_pm_id,
    role_pm_id,
    *,
    start_date="2019-01-01",
    end_date="2020-12-31",
    is_current=False,
    updated_at="2030-06-01T00:00:00Z",
):
    return {
        "id": str(pm_id),
        "person_id": str(person_pm_id),
        "role_id": str(role_pm_id),
        "start_date": start_date,
        "end_date": end_date,
        "is_current": is_current,
        "updated_at": updated_at,
    }


class _FakeClient:
    """Serves ``list_assignments_for_role`` from a ``{role_pm_id: [record, ...]}`` map."""

    def __init__(self, by_role):
        self._by = {str(k): v for k, v in by_role.items()}
        self.calls: list[str] = []

    async def list_assignments_for_role(self, role_pm_id):
        self.calls.append(str(role_pm_id))
        return list(self._by.get(str(role_pm_id), []))


async def test_reanchors_dead_anchor_onto_natural_key_match(db_session):
    """The incident shape: local anchor 404s, PM holds the same (person, start) under a
    new id. Adopt the new id rather than re-producing (which would duplicate)."""
    role = await _add_role(db_session)
    dead, live = ULID(), ULID()
    row, person = await _add_assignment(db_session, role, anchor=dead)
    client = _FakeClient(
        {role.pm_role_id: [_pm_record(live, person.pm_person_id, role.pm_role_id)]}
    )

    result = await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    assert result["reanchored"] == 1
    assert result["unresolved"] == 0
    assert str(row.pm_assignment_id) == str(live)


async def test_reanchor_writes_the_ledger_row(db_session):
    """The old id is the only handle on what we lost; ``AnchorReanchor`` is the durable
    old→new record (the same ledger the #108 in-place overwrite writes)."""
    role = await _add_role(db_session)
    dead, live = ULID(), ULID()
    _, person = await _add_assignment(db_session, role, anchor=dead, source_id="A-9")
    client = _FakeClient(
        {role.pm_role_id: [_pm_record(live, person.pm_person_id, role.pm_role_id)]}
    )

    await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    ledger = (await db_session.execute(select(AnchorReanchor))).scalars().all()
    assert len(ledger) == 1
    assert str(ledger[0].old_pm_id) == str(dead)
    assert str(ledger[0].new_pm_id) == str(live)
    assert ledger[0].entity_type == "role_assignment"
    assert ledger[0].source_id == "A-9"


async def test_leaves_healthy_anchor_untouched(db_session):
    """An anchor PM still serves is not a candidate — no ledger row, no clock write."""
    role = await _add_role(db_session)
    anchor = ULID()
    row, person = await _add_assignment(db_session, role, anchor=anchor)
    client = _FakeClient(
        {role.pm_role_id: [_pm_record(anchor, person.pm_person_id, role.pm_role_id)]}
    )

    result = await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    assert result["healthy"] == 1
    assert result["reanchored"] == 0
    assert str(row.pm_assignment_id) == str(anchor)
    assert (await db_session.execute(select(AnchorReanchor))).scalars().all() == []


async def test_dead_anchor_without_natural_key_match_is_left_alone(db_session):
    """No (person, start_date) match → we do NOT guess and do NOT clear the anchor.
    Clearing it would hand the row to the CREATE path and mint a duplicate if PM in fact
    holds it under a key we failed to match."""
    role = await _add_role(db_session)
    dead = ULID()
    row, person = await _add_assignment(db_session, role, anchor=dead)
    # PM holds a row for this role, but for a different person.
    client = _FakeClient({role.pm_role_id: [_pm_record(ULID(), ULID(), role.pm_role_id)]})

    result = await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    assert result["unresolved"] == 1
    assert result["reanchored"] == 0
    assert str(row.pm_assignment_id) == str(dead)


async def test_start_date_must_match_not_just_person(db_session):
    """``start_date`` is half of PM's uniqueness key. A same-person row at a different
    start is a *different* assignment (the #108 orphan mechanism), never a match."""
    role = await _add_role(db_session)
    dead = ULID()
    row, person = await _add_assignment(db_session, role, anchor=dead, valid_from=date(2019, 1, 1))
    client = _FakeClient(
        {
            role.pm_role_id: [
                _pm_record(ULID(), person.pm_person_id, role.pm_role_id, start_date="2013-01-01")
            ]
        }
    )

    result = await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    assert result["unresolved"] == 1
    assert str(row.pm_assignment_id) == str(dead)


async def test_adopts_pm_clock_when_payload_matches(db_session):
    """Setting the anchor fires ``onupdate`` and would leave local newer than PM, so the
    reconcile would re-POST forever (the #102 churn). Adopt PM's clock when the
    observation would not change PM."""
    role = await _add_role(db_session)
    dead, live = ULID(), ULID()
    row, person = await _add_assignment(
        db_session, role, anchor=dead, updated_at=datetime(2031, 1, 1, tzinfo=UTC)
    )
    client = _FakeClient(
        {role.pm_role_id: [_pm_record(live, person.pm_person_id, role.pm_role_id)]}
    )

    await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    assert row.updated_at == datetime(2030, 6, 1, tzinfo=UTC)


async def test_keeps_local_clock_when_a_real_change_is_pending(db_session):
    """A genuine local delta must still be pushed — adopting PM's clock here would let
    PM's older record win and silently drop the change."""
    role = await _add_role(db_session)
    dead, live = ULID(), ULID()
    row, person = await _add_assignment(
        db_session,
        role,
        anchor=dead,
        valid_to=date(2020, 12, 31),
        updated_at=datetime(2031, 1, 1, tzinfo=UTC),
    )
    client = _FakeClient(
        {
            role.pm_role_id: [
                _pm_record(live, person.pm_person_id, role.pm_role_id, end_date="2018-12-31")
            ]
        }
    )

    await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    assert str(row.pm_assignment_id) == str(live)
    assert row.updated_at != datetime(2030, 6, 1, tzinfo=UTC)


async def test_role_scope_limits_the_sweep(db_session):
    """A targeted run must not read 312 roles' worth of PM pages."""
    wanted = await _add_role(db_session, source_id="committee-member-role:3532")
    other = await _add_role(db_session, source_id="committee-member-role:99")
    await _add_assignment(db_session, wanted, anchor=ULID(), source_id="A-in")
    await _add_assignment(db_session, other, anchor=ULID(), source_id="A-out")
    client = _FakeClient({})

    await heal.reanchor_assignments(
        db_session,
        AssignmentDescriptor(),
        client,
        role_source_ids=["committee-member-role:3532"],
    )

    assert client.calls == [str(wanted.pm_role_id)]


async def test_empty_cohort_aborts(db_session):
    result = await heal.reanchor_assignments(db_session, AssignmentDescriptor(), _FakeClient({}))
    assert result["aborted"] == "empty_cohort"


async def test_is_idempotent(db_session):
    """A second run finds the anchor healthy and writes no second ledger row."""
    role = await _add_role(db_session)
    dead, live = ULID(), ULID()
    _, person = await _add_assignment(db_session, role, anchor=dead)
    client = _FakeClient(
        {role.pm_role_id: [_pm_record(live, person.pm_person_id, role.pm_role_id)]}
    )
    descriptor = AssignmentDescriptor()

    first = await heal.reanchor_assignments(db_session, descriptor, client)
    second = await heal.reanchor_assignments(db_session, descriptor, client)

    assert first["reanchored"] == 1
    assert second["reanchored"] == 0
    assert second["healthy"] == 1
    assert len((await db_session.execute(select(AnchorReanchor))).scalars().all()) == 1


async def test_skips_assignment_with_no_person_anchor(db_session):
    """No ``pm_person_id`` → no natural key to match on. Counted, never guessed."""
    role = await _add_role(db_session)
    dead = ULID()
    row, person = await _add_assignment(db_session, role, anchor=dead)
    person.pm_person_id = None
    await db_session.flush()
    client = _FakeClient({role.pm_role_id: [_pm_record(ULID(), ULID(), role.pm_role_id)]})

    result = await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    assert result["skipped_no_person_anchor"] == 1
    assert str(row.pm_assignment_id) == str(dead)


async def test_empty_pm_listing_implicates_the_role_not_its_rows(db_session):
    """An empty listing means the ROLE is unreachable, not that N assignments failed to match.

    ``GET /assignments?role_id=<dead>`` answers **200 with an empty ``data``**, not 404
    (verified against production), so a dead ``pm_role_id`` is indistinguishable from "no
    natural-key match" unless it is called out. That is precisely the #283 failure — the
    role anchor was the broken thing — and reporting it as N unresolved assignments points
    the operator at the wrong layer entirely."""
    role = await _add_role(db_session, source_id="committee-member-role:3532")
    await _add_assignment(db_session, role, anchor=ULID(), source_id="A-1")
    await _add_assignment(db_session, role, anchor=ULID(), source_id="A-2")
    client = _FakeClient({})  # PM serves nothing for this role

    result = await heal.reanchor_assignments(db_session, AssignmentDescriptor(), client)

    assert result["roles_with_empty_listing"] == 1
    assert result["unresolved"] == 0  # the rows are not each blamed for the role's fault
