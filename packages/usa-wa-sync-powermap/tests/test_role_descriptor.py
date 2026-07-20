"""RoleDescriptor tests — seat / non-seat observation + dependency gating.

Roles observe by one of two PM structural match keys, so duplicate-prevention is
PM-native:
- **Seat roles** (power-map#261/usa-wa#68) key on ``(org, role_type, jurisdiction,
  qualifier)`` — title omitted (PM owns it); the district must be anchored too.
- **Non-seat roles** key on ``(org, title)``.

The descriptor's job is to defer until the org (and, for a seat, the district) is
anchored, and to mirror PM's curated fields update-only.
"""

from datetime import UTC, datetime

import pytest
from powermap_client.models import RoleObservationRequest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_domain_legislative.identity import Organization, Role
from clearinghouse_domain_legislative.role_types import RoleType
from clearinghouse_sync_powermap.engine import APPLY_KEPT_LOCAL, SyncEngine
from clearinghouse_sync_powermap.models import OP_UPDATE, STATUS_PENDING, OutboxEntry
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_sync_powermap.descriptors import RoleDescriptor


@pytest.fixture
def descriptor() -> RoleDescriptor:
    return RoleDescriptor()


async def _add_org(session, *, source_id="HOUSE", name="House", anchor=None):
    org = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=name,
        org_type="chamber",
        pm_organization_id=anchor,
    )
    session.add(org)
    await session.flush()
    return org


async def _add_role(
    session, *, org, source_id="R-1", name="Chair", role_type="committee_leadership", anchor=None
):
    role = Role(
        source="usa_wa_legislature",
        source_id=source_id,
        organization_id=org.id,
        name=name,
        role_type=role_type,
        pm_role_id=anchor,
    )
    session.add(role)
    await session.flush()
    return role


async def _add_district(session, *, slug="usa-wa-ld-21", anchor=None):
    jt = (
        await session.execute(select(JurisdictionType).where(JurisdictionType.slug == "ld"))
    ).scalar_one_or_none()
    if jt is None:
        jt = JurisdictionType(slug="ld", display_name="Legislative District")
        session.add(jt)
        await session.flush()
    jur = Jurisdiction(
        slug=slug,
        name=slug,
        type_id=jt.id,
        recorded_at=datetime.now(UTC),
        pm_jurisdiction_id=anchor,
    )
    session.add(jur)
    await session.flush()
    return jur


async def _add_seat(
    session,
    *,
    org,
    jurisdiction,
    source_id="SEAT-1",
    qualifier="Position 1",
    role_type="state_representative",
    name="State Representative",
    anchor=None,
):
    role = Role(
        source="usa_wa_legislature",
        source_id=source_id,
        organization_id=org.id,
        name=name,
        role_type=role_type,
        jurisdiction_id=jurisdiction.id,
        qualifier=qualifier,
        pm_role_id=anchor,
    )
    session.add(role)
    await session.flush()
    return role


async def _seed_role_type(
    session,
    *,
    slug="state_representative",
    expects_jurisdiction=True,
    requires_qualifier=False,
    anchor=None,
):
    """Seed the local role_type catalog mirror (power-map#268) — the descriptor reads
    ``expects_jurisdiction`` (shape) and ``requires_qualifier`` (power-map#273 guard) from
    it."""
    rt = RoleType(
        slug=slug,
        display_name=slug.replace("_", " ").title(),
        expects_jurisdiction=expects_jurisdiction,
        requires_qualifier=requires_qualifier,
        pm_role_type_id=anchor,
    )
    session.add(rt)
    await session.flush()
    return rt


# --- seat-Role catalog-driven observation (usa-wa#68) ----------------------


async def test_is_seat_role_type_reads_catalog(db_session, descriptor):
    await _seed_role_type(db_session, slug="state_senator", expects_jurisdiction=True)
    await _seed_role_type(db_session, slug="committee_leadership", expects_jurisdiction=False)
    assert await descriptor._is_seat_role_type(db_session, "state_senator") is True
    assert await descriptor._is_seat_role_type(db_session, "committee_leadership") is False
    assert await descriptor._is_seat_role_type(db_session, "not_in_catalog") is False
    assert await descriptor._is_seat_role_type(db_session, None) is False


async def test_to_observation_seat_emits_structural_tuple(db_session, descriptor):
    org_pm, jur_pm = ULID(), ULID()
    org = await _add_org(db_session, anchor=org_pm)
    jur = await _add_district(db_session, anchor=jur_pm)
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    seat = await _add_seat(db_session, org=org, jurisdiction=jur, qualifier="Position 1")

    obs = await descriptor.to_observation(db_session, seat)

    # No title: PM matches a seat on the structural tuple and discards/auto-generates
    # the title (power-map#267).
    assert obs == {
        "organization_id": str(org_pm),
        "role_type": "state_representative",
        "jurisdiction_id": str(jur_pm),
        "qualifier": "Position 1",
    }
    assert "title" not in obs


async def test_to_observation_non_seat_stays_title_only(db_session, descriptor):
    org = await _add_org(db_session, anchor=ULID())
    role = await _add_role(db_session, org=org, name="Vice Chair")

    obs = await descriptor.to_observation(db_session, role)

    # role_type "committee_leadership" is not seeded in the catalog here → omitted.
    assert obs == {"organization_id": str(org.pm_organization_id), "title": "Vice Chair"}


async def test_to_observation_non_seat_emits_role_type_when_catalog_known(db_session, descriptor):
    """A catalog-known non-seat classifier (``member``, power-map#269) rides alongside the
    title so PM persists it — the classifier isn't dropped to a NULL role_type_id."""
    org = await _add_org(db_session, anchor=ULID())
    await _seed_role_type(db_session, slug="member", expects_jurisdiction=False)
    role = await _add_role(db_session, org=org, name="Member", role_type="member")

    obs = await descriptor.to_observation(db_session, role)

    assert obs == {
        "organization_id": str(org.pm_organization_id),
        "title": "Member",
        "role_type": "member",
    }


async def test_dependencies_ready_seat_requires_anchored_jurisdiction(db_session, descriptor):
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    org = await _add_org(db_session, anchor=ULID())
    unanchored_jur = await _add_district(db_session, slug="usa-wa-ld-05", anchor=None)
    seat = await _add_seat(db_session, org=org, jurisdiction=unanchored_jur, source_id="S-A")
    assert await descriptor.dependencies_ready(db_session, seat) is False

    anchored_jur = await _add_district(db_session, slug="usa-wa-ld-06", anchor=ULID())
    seat2 = await _add_seat(db_session, org=org, jurisdiction=anchored_jur, source_id="S-B")
    assert await descriptor.dependencies_ready(db_session, seat2) is True


async def test_dependencies_ready_seat_defers_until_catalog_synced(db_session, descriptor):
    """A seat whose role_type isn't yet in the synced catalog defers — better than
    emitting a title-shaped observation that could mint a duplicate (usa-wa#68)."""
    org = await _add_org(db_session, anchor=ULID())
    jur = await _add_district(db_session, anchor=ULID())
    seat = await _add_seat(db_session, org=org, jurisdiction=jur)

    # Empty catalog → defer.
    assert await descriptor.dependencies_ready(db_session, seat) is False

    # Once the catalog knows the seat type → ready.
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    assert await descriptor.dependencies_ready(db_session, seat) is True


async def test_requires_qualifier_reads_catalog(db_session, descriptor):
    await _seed_role_type(
        db_session, slug="state_representative", expects_jurisdiction=True, requires_qualifier=True
    )
    await _seed_role_type(
        db_session, slug="state_senator", expects_jurisdiction=True, requires_qualifier=False
    )
    assert await descriptor._requires_qualifier(db_session, "state_representative") is True
    assert await descriptor._requires_qualifier(db_session, "state_senator") is False
    assert await descriptor._requires_qualifier(db_session, "not_in_catalog") is False
    assert await descriptor._requires_qualifier(db_session, None) is False


async def test_dependencies_ready_defers_requires_qualifier_seat_without_qualifier(
    db_session, descriptor
):
    """A districted seat of a requires_qualifier type (power-map#273) arriving with a NULL
    qualifier defers — PM would REJECT it, so we never ship it (usa-wa#71)."""
    await _seed_role_type(
        db_session, slug="state_representative", expects_jurisdiction=True, requires_qualifier=True
    )
    org = await _add_org(db_session, anchor=ULID())
    jur = await _add_district(db_session, anchor=ULID())
    seat = await _add_seat(
        db_session, org=org, jurisdiction=jur, role_type="state_representative", qualifier=None
    )
    assert await descriptor.dependencies_ready(db_session, seat) is False

    # Same seat with its Position qualifier present → ready (the well-formed #68/#69 shape).
    seat_ok = await _add_seat(
        db_session,
        org=org,
        jurisdiction=jur,
        role_type="state_representative",
        qualifier="Position 1",
        source_id="SEAT-OK",
    )
    assert await descriptor.dependencies_ready(db_session, seat_ok) is True


async def test_dependencies_ready_senate_seat_ok_with_null_qualifier(db_session, descriptor):
    """A Senate seat (state_senator, requires_qualifier=False) with NULL qualifier is valid —
    the guard only fires for requires_qualifier types. Mirrors our actual emission."""
    await _seed_role_type(
        db_session, slug="state_senator", expects_jurisdiction=True, requires_qualifier=False
    )
    org = await _add_org(db_session, anchor=ULID())
    jur = await _add_district(db_session, anchor=ULID())
    seat = await _add_seat(
        db_session, org=org, jurisdiction=jur, role_type="state_senator", qualifier=None
    )
    assert await descriptor.dependencies_ready(db_session, seat) is True


async def test_dependencies_ready_seat_defers_when_role_type_not_a_seat(db_session, descriptor):
    """A districted row whose role_type is in the catalog but marked
    expects_jurisdiction=False defers — we emit a seat observation only for a
    catalog-confirmed seat type (usa-wa#68 CR)."""
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=False)
    org = await _add_org(db_session, anchor=ULID())
    jur = await _add_district(db_session, anchor=ULID())
    seat = await _add_seat(db_session, org=org, jurisdiction=jur)

    assert await descriptor.dependencies_ready(db_session, seat) is False


async def test_upsert_mirrors_seat_fields_from_pm(db_session, descriptor):
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    org = await _add_org(db_session, anchor=ULID())
    jur_pm = ULID()
    jur = await _add_district(db_session, anchor=jur_pm)
    pm_id = ULID()
    seat = await _add_seat(db_session, org=org, jurisdiction=jur, anchor=pm_id, qualifier=None)

    record = {
        "id": str(pm_id),
        "title": "State Representative",
        "role_type_slug": "state_representative",
        "jurisdiction_id": str(jur_pm),
        "qualifier": "Position 2",
        "updated_at": "2030-01-01T00:00:00Z",
    }
    result = await descriptor.upsert_from_pm(db_session, record, existing=seat)

    assert result is seat
    assert seat.qualifier == "Position 2"  # adopted PM's qualifier
    assert seat.role_type == "state_representative"  # adopted PM's role_type_slug
    assert seat.jurisdiction_id == jur.id  # PM jurisdiction id resolved to local row


async def test_upsert_non_seat_feed_update_preserves_role_type(db_session, descriptor):
    """A non-seat PM feed record (no role_type_slug/jurisdiction) must not clobber the
    local controlled ``role_type``/``qualifier`` — only the title is adopted."""
    org = await _add_org(db_session, anchor=ULID())
    pm_id = ULID()
    role = await _add_role(db_session, org=org, name="Adapter Title", anchor=pm_id)
    assert role.role_type == "committee_leadership"

    record = {"id": str(pm_id), "title": "Chair", "role_type_slug": None}
    await descriptor.upsert_from_pm(db_session, record, existing=role)

    assert role.name == "Chair"  # title adopted
    assert role.role_type == "committee_leadership"  # NOT clobbered
    assert role.qualifier is None
    assert role.jurisdiction_id is None


async def test_upsert_ignores_role_type_slug_not_a_seat_in_catalog(db_session, descriptor):
    """PM types role_type_slug as a free string with no OpenAPI enum; a slug the synced
    catalog doesn't mark as a seat (a not-yet-synced type, or one on a non-seat role)
    must not overwrite the local ``role_type`` — the catalog is the vocab (usa-wa#68)."""
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    await _seed_role_type(db_session, slug="committee_leadership", expects_jurisdiction=False)
    org = await _add_org(db_session, anchor=ULID())
    pm_id = ULID()
    role = await _add_role(db_session, org=org, anchor=pm_id)  # role_type=committee_leadership

    # A slug absent from the catalog and one present-but-not-a-seat are both ignored.
    for slug in ("not_in_catalog_yet", "committee_leadership"):
        record = {"id": str(pm_id), "title": "Chair", "role_type_slug": slug}
        await descriptor.upsert_from_pm(db_session, record, existing=role)
        assert role.role_type == "committee_leadership"


async def test_upsert_defers_seat_when_district_unmirrored(db_session, descriptor):
    """Atomic mirror: a seat record whose PM jurisdiction we haven't mirrored yet
    adopts NONE of the seat fields — never a role_type+qualifier against a stale
    jurisdiction (usa-wa#68 CR round 2)."""
    org = await _add_org(db_session, anchor=ULID())
    jur = await _add_district(db_session, anchor=ULID())
    pm_id = ULID()
    seat = await _add_seat(
        db_session, org=org, jurisdiction=jur, anchor=pm_id, qualifier="Position 1"
    )

    record = {
        "id": str(pm_id),
        "title": "State Representative",
        "role_type_slug": "state_senator",  # would flip role_type if applied
        "jurisdiction_id": str(ULID()),  # a PM district with no local mirror row
        "qualifier": "Position 2",
    }
    await descriptor.upsert_from_pm(db_session, record, existing=seat)

    # Seat fields untouched — deferred until the district is mirrored.
    assert seat.role_type == "state_representative"
    assert seat.qualifier == "Position 1"
    assert seat.jurisdiction_id == jur.id
    assert seat.name == "State Representative"  # title still adopted (non-seat field)


@pytest.mark.parametrize(
    ("role_type", "qualifier"),
    [
        ("state_representative", "Position 1"),  # House seat — real qualifier
        ("state_senator", None),  # Senate seat — explicit null must survive
    ],
)
async def test_seat_observation_round_trips_through_pm_request(
    db_session, descriptor, role_type, qualifier
):
    """The seat observation dict serializes to PM's ``RoleObservationRequest`` unchanged
    — guards the #68 client regen against dict-shape / model drift. The Senate case is
    the fragile one: attrs ``UNSET``-vs-``None`` handling could elide an explicit null,
    and PM matches a Senate seat on its NULL qualifier."""
    org_pm, jur_pm = ULID(), ULID()
    org = await _add_org(db_session, anchor=org_pm)
    jur = await _add_district(db_session, anchor=jur_pm)
    await _seed_role_type(db_session, slug=role_type, expects_jurisdiction=True)
    seat = await _add_seat(
        db_session, org=org, jurisdiction=jur, role_type=role_type, qualifier=qualifier
    )

    obs = await descriptor.to_observation(db_session, seat)
    round_tripped = RoleObservationRequest.from_dict(obs).to_dict()

    assert round_tripped == obs
    assert round_tripped == {
        "organization_id": str(org_pm),
        "role_type": role_type,
        "jurisdiction_id": str(jur_pm),
        "qualifier": qualifier,
    }
    assert "qualifier" in round_tripped  # explicit, even when None (Senate)


async def test_dependencies_ready_requires_anchored_org(db_session, descriptor):
    # A non-seat role's role_type must also be catalog-known (see the classifier-deferral
    # test below); seed it so this test isolates the org-anchoring requirement.
    await _seed_role_type(db_session, slug="committee_leadership", expects_jurisdiction=False)
    unanchored = await _add_org(db_session, anchor=None)
    role = await _add_role(db_session, org=unanchored)
    assert await descriptor.dependencies_ready(db_session, role) is False

    anchored = await _add_org(db_session, source_id="SENATE", name="Senate", anchor=ULID())
    role2 = await _add_role(db_session, org=anchored, source_id="R-2")
    assert await descriptor.dependencies_ready(db_session, role2) is True


async def test_dependencies_ready_non_seat_defers_until_classifier_in_catalog(
    db_session, descriptor
):
    """A non-seat role carrying a role_type classifier defers until the catalog knows it —
    so we never emit a title-only observation that drops the classifier (power-map#269).
    Mirrors the seat deferral in shape."""
    org = await _add_org(db_session, anchor=ULID())
    role = await _add_role(db_session, org=org, name="Member", role_type="member")

    # Catalog empty → member not yet known → defer.
    assert await descriptor.dependencies_ready(db_session, role) is False

    # Once the catalog carries `member` (non-seat), it's ready.
    await _seed_role_type(db_session, slug="member", expects_jurisdiction=False)
    assert await descriptor.dependencies_ready(db_session, role) is True


async def test_to_observation_keys_on_org_pm_id_and_title(db_session, descriptor):
    org_pm = ULID()
    org = await _add_org(db_session, anchor=org_pm)
    role = await _add_role(db_session, org=org, name="Vice Chair")

    obs = await descriptor.to_observation(db_session, role)

    assert obs == {"organization_id": str(org_pm), "title": "Vice Chair"}


async def test_local_match_by_anchor(db_session, descriptor):
    org = await _add_org(db_session, anchor=ULID())
    pm_id = ULID()
    role = await _add_role(db_session, org=org, anchor=pm_id)

    matched = await descriptor.local_match(db_session, {"id": str(pm_id)})
    assert matched is not None and matched.id == role.id
    assert await descriptor.local_match(db_session, {"id": str(ULID())}) is None


async def test_upsert_adopts_title_and_anchor(db_session, descriptor):
    org = await _add_org(db_session, anchor=ULID())
    role = await _add_role(db_session, org=org, name="Adapter Title")
    pm_id = ULID()
    record = {"id": str(pm_id), "title": "Chair", "updated_at": "2030-01-01T00:00:00Z"}

    result = await descriptor.upsert_from_pm(db_session, record, existing=role)

    assert result is role
    assert role.name == "Chair"  # adopted PM's curated title
    assert role.pm_role_id == pm_id


async def test_upsert_update_only_skips_unknown_role(db_session, descriptor):
    result = await descriptor.upsert_from_pm(db_session, {"id": str(ULID()), "title": "Ghost"})
    assert result is None
    assert (await db_session.execute(select(Role))).scalars().all() == []


async def test_upsert_mirrors_pm_archived_at_to_retired_tombstone(db_session, descriptor):
    """PM archival on an anchored role mirrors onto ``archived_at`` (usa-wa#41)."""
    org = await _add_org(db_session, anchor=ULID())
    pm_id = ULID()
    role = await _add_role(db_session, org=org, anchor=pm_id)
    assert role.archived_at is None

    record = {"id": str(pm_id), "title": "Chair", "archived_at": "2026-06-20T00:00:00Z"}
    result = await descriptor.upsert_from_pm(db_session, record, existing=role)

    assert result is role
    assert role.archived_at == datetime(2026, 6, 20, tzinfo=UTC)


async def test_upsert_clears_tombstone_when_pm_unarchives(db_session, descriptor):
    """PM un-archiving a role clears the mirrored tombstone."""
    org = await _add_org(db_session, anchor=ULID())
    pm_id = ULID()
    role = await _add_role(db_session, org=org, anchor=pm_id)
    role.archived_at = datetime(2026, 6, 20, tzinfo=UTC)
    await db_session.flush()

    result = await descriptor.upsert_from_pm(
        db_session, {"id": str(pm_id), "title": "Chair"}, existing=role
    )

    assert result is role
    assert role.archived_at is None


async def test_last_updated_row_and_record(db_session, descriptor):
    org = await _add_org(db_session, anchor=ULID())
    role = await _add_role(db_session, org=org)
    role.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated(role) == datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated({"updated_at": "2026-06-02T00:00:00Z"}) == datetime(
        2026, 6, 2, tzinfo=UTC
    )


# --- LWW no-op gate (#109) -------------------------------------------------
#
# 305 of 306 anchored roles re-sent every reconcile (610 UPDATE deliveries/day, 100%
# day-over-day overlap) because their local ``updated_at`` is frozen ahead of PM's at
# the 2026-07-06 backfill and PM no-ops the identical observation without advancing its
# clock. Same defect as #102 (assignment) / #104 (person).


async def test_local_newer_is_noop_true_for_seat_matching_pm(db_session, descriptor):
    """#109: an anchored seat whose structural tuple equals PM's is a pure no-op.

    A seat observation carries *only* PM's match key ``(org, role_type, jurisdiction,
    qualifier)`` — title is deliberately omitted (power-map#267) — so when the tuple agrees
    with PM's record the re-observation resolves to this same seat and asserts nothing
    else. ``apply_record`` may safely adopt PM's clock instead of re-POSTing forever."""
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    org = await _add_org(db_session, anchor=ULID())
    district = await _add_district(db_session, anchor=ULID())
    seat = await _add_seat(db_session, org=org, jurisdiction=district, anchor=ULID())
    record = {
        "organization_id": str(org.pm_organization_id),
        "role_type_slug": "state_representative",
        "jurisdiction_id": str(district.pm_jurisdiction_id),
        "qualifier": "Position 1",
        "title": "State Representative Position 1",  # PM-owned; not in the match key
    }
    assert await descriptor.local_newer_is_noop(db_session, seat, record) is True


async def test_local_newer_is_noop_false_when_seat_qualifier_diverges(db_session, descriptor):
    """A local seat whose qualifier drifted from PM's is **not** a no-op — re-observing
    it would resolve to a *different* seat tuple (or mint one), so it must still enqueue.

    This is why the seat branch compares the tuple rather than returning a blanket
    ``True``: the observation is the match key, so a divergent key is a real re-key."""
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    org = await _add_org(db_session, anchor=ULID())
    district = await _add_district(db_session, anchor=ULID())
    seat = await _add_seat(db_session, org=org, jurisdiction=district, anchor=ULID())
    record = {
        "organization_id": str(org.pm_organization_id),
        "role_type_slug": "state_representative",
        "jurisdiction_id": str(district.pm_jurisdiction_id),
        "qualifier": "Position 2",  # local says Position 1 → genuine divergence
    }
    assert await descriptor.local_newer_is_noop(db_session, seat, record) is False


async def test_local_newer_is_noop_true_for_title_role_matching_pm(db_session, descriptor):
    """A non-seat role keys on ``(org, title)`` and additionally persists its catalog
    ``role_type`` classifier (power-map#269) — both agreeing with PM is a no-op."""
    await _seed_role_type(db_session, slug="member", expects_jurisdiction=False)
    org = await _add_org(db_session, anchor=ULID())
    role = await _add_role(db_session, org=org, name="Member", role_type="member", anchor=ULID())
    record = {
        "organization_id": str(org.pm_organization_id),
        "title": "Member",
        "role_type_slug": "member",
    }
    assert await descriptor.local_newer_is_noop(db_session, role, record) is True


async def test_local_newer_is_noop_false_on_title_divergence(db_session, descriptor):
    """A local title differing from PM's curated one must still enqueue — the observation
    would match a *different* ``(org, title)`` role in PM, not update this one."""
    await _seed_role_type(db_session, slug="member", expects_jurisdiction=False)
    org = await _add_org(db_session, anchor=ULID())
    role = await _add_role(
        db_session, org=org, name="Vice Chair", role_type="member", anchor=ULID()
    )
    record = {
        "organization_id": str(org.pm_organization_id),
        "title": "Vice-Chair",  # PM's curated form differs → real change
        "role_type_slug": "member",
    }
    assert await descriptor.local_newer_is_noop(db_session, role, record) is False


async def test_local_newer_is_noop_false_when_dependencies_not_ready(db_session, descriptor):
    """The deps guard (#102's CR lesson, and the trap #109's audit flagged): role has real
    ``dependencies_ready`` prerequisites, so an un-anchored org would build a garbage
    observation (``organization_id="None"``) that could spuriously compare equal. Not
    ready → ``False`` (enqueue as before; ``_deliver`` defers at drain), never a bogus
    clock-adopt. Unlike ``PersonDescriptor``, which has no dependencies."""
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    org = await _add_org(db_session, anchor=None)  # org NOT anchored → deps not ready
    district = await _add_district(db_session, anchor=ULID())
    seat = await _add_seat(db_session, org=org, jurisdiction=district, anchor=ULID())
    record = {
        "organization_id": "None",
        "role_type_slug": "state_representative",
        "jurisdiction_id": str(district.pm_jurisdiction_id),
        "qualifier": "Position 1",
    }
    assert await descriptor.local_newer_is_noop(db_session, seat, record) is False


async def test_apply_record_role_noop_adopts_clock_and_skips_enqueue(db_session, descriptor):
    """Seam test (#109): a real ``RoleDescriptor`` through ``engine.apply_record``'s
    local-newer branch — the clock-skewed anchored seat adopts PM's clock and enqueues
    nothing. This is the 610-writes/day churn, fixed end-to-end."""
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    org = await _add_org(db_session, anchor=ULID())
    district = await _add_district(db_session, anchor=ULID())
    pm_id = ULID()
    seat = await _add_seat(db_session, org=org, jurisdiction=district, anchor=pm_id)
    seat.updated_at = datetime(2050, 1, 1, tzinfo=UTC)  # local clock ahead of PM
    await db_session.flush()
    engine = SyncEngine([descriptor], FakeClient())

    record = {
        "id": str(pm_id),
        "organization_id": str(org.pm_organization_id),
        "role_type_slug": "state_representative",
        "jurisdiction_id": str(district.pm_jurisdiction_id),
        "qualifier": "Position 1",
        "title": "State Representative Position 1",
        "updated_at": "2000-01-01T00:00:00Z",  # PM's clock older → local reads "newer"
    }
    outcome = await engine.apply_record(db_session, descriptor, record)

    assert outcome == APPLY_KEPT_LOCAL
    assert (await db_session.execute(select(OutboxEntry))).first() is None  # no churn enqueue
    assert seat.updated_at == datetime(2000, 1, 1, tzinfo=UTC)  # PM's clock adopted → parity


async def test_apply_record_role_real_change_still_enqueues(db_session, descriptor):
    """The seam's other direction: a genuinely diverged seat tuple on a clock-newer row is
    NOT a no-op → apply_record keeps local and enqueues the UPDATE."""
    await _seed_role_type(db_session, slug="state_representative", expects_jurisdiction=True)
    org = await _add_org(db_session, anchor=ULID())
    district = await _add_district(db_session, anchor=ULID())
    pm_id = ULID()
    seat = await _add_seat(db_session, org=org, jurisdiction=district, anchor=pm_id)
    seat.updated_at = datetime(2050, 1, 1, tzinfo=UTC)
    await db_session.flush()
    engine = SyncEngine([descriptor], FakeClient())

    record = {
        "id": str(pm_id),
        "organization_id": str(org.pm_organization_id),
        "role_type_slug": "state_representative",
        "jurisdiction_id": str(district.pm_jurisdiction_id),
        "qualifier": "Position 2",  # diverges from local Position 1 → real change
        "updated_at": "2000-01-01T00:00:00Z",
    }
    outcome = await engine.apply_record(db_session, descriptor, record)

    assert outcome == APPLY_KEPT_LOCAL
    entry = (await db_session.execute(select(OutboxEntry))).scalar_one()
    assert (entry.op, entry.status) == (OP_UPDATE, STATUS_PENDING)
    assert seat.updated_at == datetime(2050, 1, 1, tzinfo=UTC)  # local clock preserved


async def test_local_newer_is_noop_false_when_record_omits_qualifier(db_session, descriptor):
    """#109 CR-1: an *absent* ``qualifier`` key must not read as an explicit NULL match.

    A Senate seat's qualifier is legitimately ``None``, so a record that omits the key
    entirely compares equal to our ``None`` and — with the rest of the tuple agreeing —
    would yield a false no-op. Latent today (both live paths fetch a full ``RoleDetail``;
    only the ``full_list`` reconcile yields list-shaped records, which no gated descriptor
    uses), but a false no-op **erases** the pending change rather than deferring it, so
    the comparator must require positive evidence of the key rather than infer it."""
    await _seed_role_type(db_session, slug="state_senator", expects_jurisdiction=True)
    org = await _add_org(db_session, anchor=ULID())
    district = await _add_district(db_session, anchor=ULID())
    seat = await _add_seat(
        db_session,
        org=org,
        jurisdiction=district,
        qualifier=None,  # Senate seats carry a NULL qualifier
        role_type="state_senator",
        name="State Senator",
        anchor=ULID(),
    )
    record = {
        "organization_id": str(org.pm_organization_id),
        "role_type_slug": "state_senator",
        "jurisdiction_id": str(district.pm_jurisdiction_id),
        # "qualifier" deliberately absent — not the same as an asserted NULL
    }
    assert await descriptor.local_newer_is_noop(db_session, seat, record) is False

    record["qualifier"] = None  # PM positively asserts the NULL → genuine no-op
    assert await descriptor.local_newer_is_noop(db_session, seat, record) is True
