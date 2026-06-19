"""OrganizationDescriptor tests — PM-first match cascade + update-only upsert.

The cascade is the duplicate-prevention mechanism: PM has backfilled the WA org
tree with curated names but no usa-wa identifiers, so an un-anchored adapter row
must be matched to its pre-existing PM org (identifier → normalized name →
hierarchy) before any CREATE is enqueued.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import EntityPage
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor
from usa_wa_sync_powermap.descriptors.organization import identifier_type_for


@pytest.fixture
def descriptor() -> OrganizationDescriptor:
    return OrganizationDescriptor()


async def _add_org(
    session,
    *,
    source="usa_wa_legislature",
    source_id,
    name,
    org_type="committee",
    anchor=None,
    jurisdiction_id=None,
    parent_id=None,
    acronym=None,
    phone=None,
):
    row = Organization(
        source=source,
        source_id=source_id,
        name=name,
        org_type=org_type,
        pm_organization_id=anchor,
        jurisdiction_id=jurisdiction_id,
        parent_organization_id=parent_id,
        acronym=acronym,
        phone=phone,
    )
    session.add(row)
    await session.flush()
    return row


# --- identifier_type mapping --------------------------------------------------


def test_identifier_type_for_maps_source_and_org_type():
    assert identifier_type_for("usa_wa_legislature", "chamber") == "org_wa_legislature_chamber"
    assert (
        identifier_type_for("usa_wa_legislature", "committee") == "org_wa_legislature_committee_id"
    )
    assert (
        identifier_type_for("usa_wa_legislature", "subcommittee")
        == "org_wa_legislature_committee_id"
    )
    assert identifier_type_for("usa_wa_pdc", "pac") == "org_wa_pdc"
    assert identifier_type_for("some_other_source", "committee") is None


# --- pm_match cascade ---------------------------------------------------------


async def test_pm_match_identifier_hit(db_session, descriptor):
    """An exact identifier match short-circuits the cascade (no cohort scan)."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="House Approps")
    client = FakeClient(search_pages=[EntityPage(records=[{"id": str(pm_id)}], cursor=None)])

    matched = await descriptor.pm_match(client, db_session, row)

    assert matched == pm_id
    assert len(client.searched) == 1  # only the identifier search ran
    assert client.searched[0]["identifier_type"] == "org_wa_legislature_committee_id"
    assert client.searched[0]["identifier_value"] == "C-1"
    # Identifier is globally unique → not scoped by jurisdiction (avoids false-miss).
    assert client.searched[0]["jurisdiction"] is None


async def test_pm_match_name_via_fts(db_session, descriptor):
    """Identifier misses; one PM FTS query (q + jurisdiction) returns the match,
    confirmed by normalized equality — a single query, no enumeration.

    FTS folds '&'→'and'/punctuation server-side, so an adapter '&' variant is
    surfaced by PM and our normalize_name confirm (which also folds '&') accepts it."""
    pm_id = ULID()
    row = await _add_org(
        db_session, source_id="C-9", name="Consumer Protection & Business Committee"
    )
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(  # FTS result (PM folds & → and; returns the canonical)
                records=[
                    {"id": str(ULID()), "name": "Ways and Means", "parent_id": None},
                    {
                        "id": str(pm_id),
                        "name": "Consumer Protection and Business Committee",
                        "parent_id": None,
                    },
                ],
                cursor=None,
            ),
        ]
    )

    matched = await descriptor.pm_match(client, db_session, row)

    assert matched == pm_id
    assert len(client.searched) == 2  # identifier + one FTS query; no enumeration
    assert client.searched[1]["q"] == "Consumer Protection & Business Committee"
    assert client.searched[1]["jurisdiction"] == "usa-wa"


async def test_name_search_uses_configured_match_cap(db_session):
    """#12: the descriptor's ``search_match_cap`` is the ``limit`` it passes to the
    name-match search, so an operator-tuned cap actually widens the candidate window."""
    row = await _add_org(db_session, source_id="C-CAP", name="Some Committee")
    seen_limits: list[int] = []

    class _RecordingClient:
        async def search_entities(self, search_path, *, limit=20, **kwargs):
            seen_limits.append(limit)
            return EntityPage(records=[], cursor=None)

    descriptor = OrganizationDescriptor(search_match_cap=137)
    await descriptor.pm_match(_RecordingClient(), db_session, row)

    # First call is the identifier lookup (limit=1); the name FTS uses the cap.
    assert 137 in seen_limits


async def test_pm_match_disambiguates_by_parent_hierarchy(db_session, descriptor):
    """Two same-name committees (FTS returns both) → resolved by the anchored parent."""
    parent_pm = ULID()
    winner = ULID()
    parent = await _add_org(
        db_session, source_id="HOUSE", name="House", org_type="chamber", anchor=parent_pm
    )
    row = await _add_org(db_session, source_id="C-3", name="Rules Committee", parent_id=parent.id)
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(  # q returns both same-name matches
                records=[
                    {"id": str(ULID()), "name": "Rules Committee", "parent_id": str(ULID())},
                    {"id": str(winner), "name": "Rules Committee", "parent_id": str(parent_pm)},
                ],
                cursor=None,
            ),
        ]
    )

    matched = await descriptor.pm_match(client, db_session, row)

    assert matched == winner


async def test_pm_match_returns_none_when_genuinely_new(db_session, descriptor):
    """No identifier, FTS returns no normalized-equal name → None → observe-create."""
    row = await _add_org(db_session, source_id="C-NEW", name="Brand New Select Committee")
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(records=[{"id": str(ULID()), "name": "Unrelated Org"}], cursor=None),  # FTS
        ]
    )

    assert await descriptor.pm_match(client, db_session, row) is None
    assert len(client.searched) == 2  # identifier + one FTS query; no enumeration


# --- to_observation -----------------------------------------------------------


async def test_to_observation_committee_payload(db_session, descriptor, usa_wa):
    usa_wa.pm_jurisdiction_id = ULID()
    await db_session.flush()
    parent = await _add_org(
        db_session, source_id="HOUSE", name="House", org_type="chamber", anchor=ULID()
    )
    row = await _add_org(
        db_session,
        source_id="C-7",
        name="Health & Wellness Committee",
        jurisdiction_id=usa_wa.id,
        parent_id=parent.id,
    )

    obs = await descriptor.to_observation(db_session, row)

    assert obs["identifier_type"] == "org_wa_legislature_committee_id"
    assert obs["identifier_value"] == "C-7"
    assert obs["names"] == [{"name": "Health & Wellness Committee", "name_type": "legal"}]
    assert obs["jurisdiction_affiliations"] == [
        {"jurisdiction_id": str(usa_wa.pm_jurisdiction_id), "affiliation_type_slug": "governing"}
    ]
    assert obs["organization_parent_id"] == str(parent.pm_organization_id)


async def test_to_observation_emits_acronym_and_phone(db_session, descriptor, usa_wa):
    """acronym → org_acronyms[0]; phone → a phone contact_method."""
    row = await _add_org(
        db_session,
        source_id="C-9",
        name="House Committee on Appropriations",
        jurisdiction_id=usa_wa.id,
        acronym="APP",
        phone="(360) 786-7204",
    )

    obs = await descriptor.to_observation(db_session, row)

    assert obs["org_acronyms"] == ["APP"]
    assert obs["contact_methods"] == [{"contact_type": "phone", "value": "(360) 786-7204"}]


async def test_to_observation_omits_acronym_and_phone_when_absent(db_session, descriptor, usa_wa):
    """No acronym/phone → neither key is present (PM keys on presence, not null)."""
    row = await _add_org(db_session, source_id="C-10", name="Bare Org", jurisdiction_id=usa_wa.id)
    obs = await descriptor.to_observation(db_session, row)
    assert "org_acronyms" not in obs
    assert "contact_methods" not in obs


async def test_to_observation_omits_acronym_when_empty_string(db_session, descriptor, usa_wa):
    """An empty-string acronym is treated as absent (no org_acronyms: [''])."""
    row = await _add_org(
        db_session, source_id="C-11", name="Empty Acr Org", jurisdiction_id=usa_wa.id, acronym=""
    )
    obs = await descriptor.to_observation(db_session, row)
    assert "org_acronyms" not in obs


async def test_to_observation_omits_affiliation_when_jurisdiction_unsynced(
    db_session, descriptor, usa_wa
):
    """A local jurisdiction with no PM anchor yields no affiliation (PM keys by PM id)."""
    row = await _add_org(db_session, source_id="C-8", name="Global Org", jurisdiction_id=usa_wa.id)
    obs = await descriptor.to_observation(db_session, row)
    assert "jurisdiction_affiliations" not in obs


# --- upsert_from_pm (update-only) ---------------------------------------------


def _pm_org(
    pm_id,
    name="PM Canonical",
    *,
    parent_id=None,
    governing_jur=None,
    updated_at="2030-01-01T00:00:00Z",
):
    affiliations = []
    if governing_jur is not None:
        affiliations.append(
            {
                "jurisdiction_id": str(governing_jur),
                "affiliation_type": {
                    "id": str(ULID()),
                    "slug": "governing",
                    "display_name": "is governed by",
                },
            }
        )
    return {
        "id": str(pm_id),
        "name": name,
        "parent_id": str(parent_id) if parent_id else None,
        "jurisdiction_affiliations": affiliations,
        "updated_at": updated_at,
    }


async def test_upsert_adopts_canonical_name_and_anchor(db_session, descriptor, usa_wa):
    usa_wa.pm_jurisdiction_id = ULID()
    await db_session.flush()
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="Adapter Name")

    result = await descriptor.upsert_from_pm(
        db_session,
        _pm_org(
            pm_id,
            name="Washington State House Appropriations Committee",
            governing_jur=usa_wa.pm_jurisdiction_id,
        ),
        existing=row,
    )

    assert result is row
    assert row.name == "Washington State House Appropriations Committee"  # adopted PM canonical
    assert row.pm_organization_id == pm_id
    assert row.jurisdiction_id == usa_wa.id  # resolved governing affiliation → local jurisdiction


async def test_upsert_update_only_skips_unknown_org(db_session, descriptor):
    """A feed change to an org we never produced is skipped — not mirrored — so a
    later adapter row + sweep does not create a duplicate."""
    result = await descriptor.upsert_from_pm(db_session, _pm_org(ULID()))

    assert result is None
    assert (await db_session.execute(select(Organization))).scalars().all() == []


async def test_local_match_by_anchor(db_session, descriptor):
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="X", anchor=pm_id)
    await _add_org(db_session, source_id="C-2", name="Y")  # different, unanchored

    matched = await descriptor.local_match(db_session, {"id": str(pm_id)})

    assert matched is not None and matched.id == row.id
    assert await descriptor.local_match(db_session, {"id": str(ULID())}) is None


async def test_last_updated_row_and_record(db_session, descriptor):
    row = await _add_org(db_session, source_id="C-1", name="X")
    row.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated(row) == datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated({"updated_at": "2026-06-02T00:00:00Z"}) == datetime(
        2026, 6, 2, tzinfo=UTC
    )


# --- enrich-on-match (#198) ---------------------------------------------------


async def test_needs_enrich_true_when_pm_lacks_our_identifier(db_session, descriptor):
    row = await _add_org(db_session, source_id="C-1", name="X")
    assert await descriptor.needs_enrich({"identifiers": []}, row) is True
    has_it = {"identifiers": [{"type_slug": "org_wa_legislature_committee_id", "value": "C-1"}]}
    assert await descriptor.needs_enrich(has_it, row) is False


async def test_to_enrich_observation_rekeys_to_pm_org_id(db_session, descriptor, usa_wa):
    usa_wa.pm_jurisdiction_id = ULID()
    await db_session.flush()
    org_pm = ULID()
    row = await _add_org(
        db_session,
        source_id="C-1",
        name="Health Committee",
        anchor=org_pm,
        jurisdiction_id=usa_wa.id,
    )

    obs = await descriptor.to_enrich_observation(db_session, row)

    assert obs["identifier_type"] == "pm_org_id"
    assert obs["identifier_value"] == str(org_pm)
    assert obs["additional_identifiers"] == [
        {"identifier_type_slug": "org_wa_legislature_committee_id", "identifier_value": "C-1"}
    ]
    assert obs["names"] == [{"name": "Health Committee", "name_type": "legal"}]
    # Enrich is narrow: parent + affiliations are NOT re-asserted (PM curates them).
    assert "jurisdiction_affiliations" not in obs
    assert "organization_parent_id" not in obs
    # No acronym/phone on this row → those carry-through fields stay absent.
    assert "org_acronyms" not in obs
    assert "contact_methods" not in obs


async def test_to_enrich_observation_carries_acronym_and_phone(db_session, descriptor, usa_wa):
    """WSL-sourced acronym/phone ride along on enrich — facts PM lacks (#25)."""
    usa_wa.pm_jurisdiction_id = ULID()
    await db_session.flush()
    row = await _add_org(
        db_session,
        source_id="C-2",
        name="Appropriations",
        anchor=ULID(),
        jurisdiction_id=usa_wa.id,
        acronym="APP",
        phone="(360) 786-7204",
    )

    obs = await descriptor.to_enrich_observation(db_session, row)

    assert obs["org_acronyms"] == ["APP"]
    assert obs["contact_methods"] == [{"contact_type": "phone", "value": "(360) 786-7204"}]
    # Still anchor-keyed and narrow — parent/affiliations remain PM-curated.
    assert obs["identifier_type"] == "pm_org_id"
    assert "jurisdiction_affiliations" not in obs
    assert "organization_parent_id" not in obs
