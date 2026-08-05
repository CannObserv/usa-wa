"""One-shot producer retraction of spurious anchored assignments (#144 Phase 2).

power-map#391 shipped ``op:"retract"`` on the assignment observation channel; this CLI drives it
for a curated set of local assignment ``source_id``s — the sanctioned way to retire an artifact
tenure usa-wa produced (Wynne LD39 Senate 2001-02) without orphaning the PM anchor. Retracting an
anchored row POSTs the id-addressed ``op:"retract"`` payload and tombstones the local row
(``archived_at``); an unanchored / not-found id is skipped-and-counted; an unexpected disposition
does not tombstone. Retraction is terminal (no reversible ``archived:false``).
"""

from datetime import date

from ulid import ULID

from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from clearinghouse_sync_powermap.client import ObservationResult
from usa_wa_sync_powermap import retract_assignments as ra


class _FakeClient:
    """Records every ``post_observation`` payload; returns a configurable disposition."""

    def __init__(self, disposition="retracted"):
        self._disposition = disposition
        self.posted: list[tuple[str, dict]] = []

    async def post_observation(self, observe_path, payload):
        self.posted.append((observe_path, payload))
        return ObservationResult(disposition=self._disposition, pm_id=None, raw={})

    async def aclose(self):
        pass


async def _add_assignment(session, *, source_id, anchor, is_active=False):
    org = Organization(
        source="usa_wa_legislature",
        source_id=f"ORG-{source_id}",
        name="Senate",
        org_type="chamber",
        pm_organization_id=ULID(),
    )
    session.add(org)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id=f"R-{source_id}",
        organization_id=org.id,
        name="Senator",
        role_type="state_senator",
        pm_role_id=ULID(),
    )
    person = Person(
        source="usa_wa_legislature",
        source_id=f"M-{source_id}",
        name_full="John Wynne",
        pm_person_id=ULID(),
    )
    session.add_all([role, person])
    await session.flush()
    row = Assignment(
        source="usa_wa_legislature",
        source_id=source_id,
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2001, 1, 1),
        valid_to=date(2002, 12, 31),
        is_active=is_active,
        pm_assignment_id=anchor,
    )
    session.add(row)
    await session.flush()
    return row


async def test_retracts_anchored_row_posts_op_retract_and_tombstones(db_session):
    anchor = ULID()
    row = await _add_assignment(
        db_session, source_id="481:chamber-senate:39:2001-02", anchor=anchor
    )
    client = _FakeClient(disposition="retracted")

    result = await ra.retract_assignments(db_session, client, ["481:chamber-senate:39:2001-02"])

    assert client.posted == [
        (
            "/api/v1/assignments/observations",
            {
                "identifier_type": "pm_assignment_id",
                "identifier_value": str(anchor),
                "op": "retract",
            },
        )
    ]
    assert row.archived_at is not None  # tombstoned locally
    assert result["retracted"] == 1
    assert result["not_found"] == 0
    assert result["not_anchored"] == 0
    assert result["unexpected"] == 0


async def test_unanchored_row_is_skipped_not_retracted(db_session):
    row = await _add_assignment(db_session, source_id="481:party:republican:2001-02", anchor=None)
    client = _FakeClient()

    result = await ra.retract_assignments(db_session, client, ["481:party:republican:2001-02"])

    assert client.posted == []  # never POSTed
    assert row.archived_at is None
    assert result["not_anchored"] == 1
    assert result["retracted"] == 0


async def test_unknown_source_id_is_counted_not_found(db_session):
    client = _FakeClient()
    result = await ra.retract_assignments(db_session, client, ["nope:does-not-exist"])
    assert client.posted == []
    assert result["not_found"] == 1
    assert result["retracted"] == 0


async def test_unexpected_disposition_does_not_tombstone(db_session):
    anchor = ULID()
    row = await _add_assignment(
        db_session, source_id="481:chamber-senate:39:2001-02", anchor=anchor
    )
    client = _FakeClient(disposition="rejected")

    result = await ra.retract_assignments(db_session, client, ["481:chamber-senate:39:2001-02"])

    assert len(client.posted) == 1  # attempted
    assert row.archived_at is None  # NOT tombstoned on a non-retracted disposition
    assert result["unexpected"] == 1
    assert result["retracted"] == 0
