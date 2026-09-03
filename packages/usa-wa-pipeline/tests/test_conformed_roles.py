"""Roles and seats as deterministic structural keys (#309 part 2, increment 4).

A Role is a named slot in an Organization; an Assignment binds one in time
(ONTOLOGY.md § 2). The span already carries the slot's identity as
``(span_kind, span_discriminator)`` — these pin the derivation of the *key* the
Postgres tier mints from that pair, so the published `assignments` can name its
role and a consumer can join a real dimension instead of re-deriving one.

Every key function is imported unchanged from the adapter and the vocabulary
package; what is tested here is the mapping and its refusals.
"""

import pytest

from usa_wa_pipeline.conformed.roles import ROLE_COLUMNS, role_for_span, role_rows


def test_a_party_span_names_its_party_member_role() -> None:
    role = role_for_span("party", "republican")
    assert role.role_key == "party-role:republican"
    assert role.role_type == "party_member"
    assert role.org_source_id == "party-republican"
    assert role.district is None and role.qualifier is None


def test_a_committee_span_names_that_committee_s_member_role() -> None:
    role = role_for_span("committee", "28240")
    assert role.role_key == "committee-member-role:28240"
    assert role.role_type == "committee_member"
    # the committee IS the org — its own source_id, not a structural one
    assert role.org_source_id == "28240"


def test_a_senate_span_names_the_seat_for_its_district() -> None:
    role = role_for_span("chamber-senate", "22")
    assert role.role_key == "seat:senate:ld-22"
    assert role.role_type == "state_senator"
    assert role.org_source_id == "usa_wa_senate"
    assert role.district == 22
    assert role.qualifier is None, "one Senate seat per LD needs no disambiguator"


def test_a_house_span_names_the_positioned_seat() -> None:
    """The House discriminator round-trips: `ld-5-position-1` is the same
    (LD, position) pair the seat key encodes, which is why a redistricting
    renumber opens a new seat rather than silently re-pointing an old one."""
    role = role_for_span("chamber-house", "ld-5-position-1")
    assert role.role_key == "seat:house:ld-5:position-1"
    assert role.role_type == "state_representative"
    assert role.org_source_id == "usa_wa_house"
    assert role.district == 5
    assert role.qualifier == "Position 1"


def test_an_unknown_kind_is_refused() -> None:
    """A new span kind must be given a role mapping deliberately. Guessing one
    would publish an assignment pointing at a role nothing defines."""
    with pytest.raises(ValueError, match="span kind"):
        role_for_span("chamber-tribunal", "ld-5")


def test_an_unparseable_discriminator_is_refused() -> None:
    with pytest.raises(ValueError, match="discriminator"):
        role_for_span("chamber-senate", "not-a-district")


def test_a_role_carries_its_own_registry_ulid() -> None:
    """#313: roles get a ULID so the API has a stable handle when the derived
    `role_key` moves, and because PM already holds 312 role anchors. The key
    stays first-class beside it — nothing PM matches on is mediated away."""
    rows, counters = role_rows(
        [{"span_kind": "chamber-senate", "span_discriminator": "22"}],
        {"usa_wa_legislature:usa_wa_senate": "01SENATE"},
        {"usa_wa_legislature:seat:senate:ld-22": "01ROLE"},
    )
    [row] = rows
    assert row["entity_id"] == "01ROLE"
    assert row["role_key"] == "seat:senate:ld-22"
    assert counters["unregistered_roles"] == 0


def test_a_role_the_registry_has_not_reached_is_counted_not_dropped() -> None:
    """Same asymmetry as the org join, for the same reason: the nightly runs
    `dbt build -> registrar -> publish`, so a brand-new seat is unregistered in
    the build that first sees it and bound by the next one. Dropping it would
    delete the dimension row a published assignment already names; the counter
    is what makes the one-run latency visible instead of silent."""
    rows, counters = role_rows(
        [{"span_kind": "committee", "span_discriminator": "999"}],
        {"usa_wa_legislature:999": "01ORG"},
        {},
    )
    [row] = rows
    assert row["entity_id"] is None
    assert row["role_key"] == "committee-member-role:999"
    assert counters["unregistered_roles"] == 1


def test_role_rows_are_one_per_slot_and_carry_the_org_entity() -> None:
    """The dimension is keyed on the role, not the assignment: two members in
    one seat across time are one row. The org entity comes from the crosswalk,
    so a consumer reaches the organization without re-deriving identity."""
    assignments = [
        {"span_kind": "chamber-senate", "span_discriminator": "22"},
        {"span_kind": "chamber-senate", "span_discriminator": "22"},
        {"span_kind": "party", "span_discriminator": "democratic"},
    ]
    rows, counters = role_rows(
        assignments,
        {
            "usa_wa_legislature:usa_wa_senate": "01SENATE",
            "usa_wa_legislature:party-democratic": "01DEMS",
        },
        {
            "usa_wa_legislature:seat:senate:ld-22": "01SEAT",
            "usa_wa_legislature:party-role:democratic": "01PARTYROLE",
        },
    )
    assert len(rows) == 2
    assert list(rows[0]) == ROLE_COLUMNS
    by_key = {r["role_key"]: r for r in rows}
    assert by_key["seat:senate:ld-22"]["org_entity_id"] == "01SENATE"
    assert by_key["party-role:democratic"]["org_entity_id"] == "01DEMS"
    assert counters["roles"] == 2
    assert counters["unregistered_orgs"] == 0


def test_a_role_whose_org_is_unregistered_is_counted_not_dropped() -> None:
    """Unlike an assignment, a role with no org entity is still a real slot —
    the seat exists whether or not the registry has minted its chamber yet. It
    publishes with a null org and is counted, so the gap is visible rather than
    silently deleting the dimension row an assignment points at."""
    rows, counters = role_rows(
        [{"span_kind": "committee", "span_discriminator": "999"}],
        {},
        {"usa_wa_legislature:committee-member-role:999": "01ROLE"},
    )
    [row] = rows
    assert row["org_entity_id"] is None
    assert counters["unregistered_orgs"] == 1
