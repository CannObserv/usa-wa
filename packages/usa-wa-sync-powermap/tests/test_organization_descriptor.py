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
from usa_wa_sync_powermap.descriptors.organization import (
    identifier_type_for,
    identifier_value_for,
)


@pytest.fixture
def descriptor() -> OrganizationDescriptor:
    return OrganizationDescriptor()


async def _add_org(
    session,
    *,
    source="usa_wa_legislature",
    source_id,
    name,
    short_name=None,
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
        short_name=short_name,
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
    # The legislature anchor gets its own type — not the committee type it formerly
    # fell through to (#33; the mismodeling surfaced while diagnosing #29).
    assert identifier_type_for("usa_wa_legislature", "legislature") == "org_wa_legislature"
    assert (
        identifier_type_for("usa_wa_legislature", "committee") == "org_wa_legislature_committee_id"
    )
    assert (
        identifier_type_for("usa_wa_legislature", "subcommittee")
        == "org_wa_legislature_committee_id"
    )
    assert identifier_type_for("usa_wa_legislature", "party") == "org_wa_party"
    assert identifier_type_for("usa_wa_pdc", "pac") == "org_wa_pdc"
    assert identifier_type_for("some_other_source", "committee") is None


def test_identifier_value_for_strips_party_prefix():
    """A party's identifier value is the bare slug (power-map#270), not the source_id."""
    # committee/other: value == source_id verbatim
    assert identifier_value_for("usa_wa_legislature", "committee", "C-1") == "C-1"
    # party: 'party-republican' → 'republican'
    assert identifier_value_for("usa_wa_legislature", "party", "party-republican") == "republican"
    assert identifier_value_for("usa_wa_legislature", "party", "party-democratic") == "democratic"


async def test_party_pm_match_uses_bare_slug_value(db_session, descriptor):
    """A party org matches PM on org_wa_party with the bare-slug value, not source_id."""
    pm_id = ULID()
    row = await _add_org(
        db_session,
        source_id="party-republican",
        name="Washington State Republican Party",
        org_type="party",
    )
    client = FakeClient(search_pages=[EntityPage(records=[{"id": str(pm_id)}], cursor=None)])

    matched = await descriptor.pm_match(client, db_session, row)

    assert matched == pm_id
    assert client.searched[0]["identifier_type"] == "org_wa_party"
    assert client.searched[0]["identifier_value"] == "republican"


async def test_party_to_observation_emits_org_wa_party(db_session, descriptor, usa_wa):
    """A party observation carries org_wa_party + the bare slug (not the committee type)."""
    row = await _add_org(
        db_session,
        source_id="party-democratic",
        name="Washington State Democratic Party",
        org_type="party",
        jurisdiction_id=usa_wa.id,
    )
    obs = await descriptor.to_observation(db_session, row)
    assert obs["identifier_type"] == "org_wa_party"
    assert obs["identifier_value"] == "democratic"


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


async def test_pm_match_other_class_matches_on_clean_short_name(db_session, descriptor):
    """For the Joint/`Other` class, the name-match searches by the clean short_name (the
    name we assert), not the double-prefixed local name — so a PM org curated under the
    clean name is matched, not duplicated (#61). With the prefixed name the normalize_name
    target would not equal the PM org's clean name and the match would false-miss."""
    pm_id = ULID()
    row = await _add_org(
        db_session,
        source_id="-140",
        name="Joint Joint Transportation Committee",  # WSL LongName (local, verbatim)
        short_name="Joint Transportation Committee",  # clean Name — what we assert + match on
        org_type="other",
    )
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(  # PM holds the body under its clean (curator) name
                records=[
                    {"id": str(pm_id), "name": "Joint Transportation Committee", "parent_id": None}
                ],
                cursor=None,
            ),
        ]
    )

    matched = await descriptor.pm_match(client, db_session, row)

    assert matched == pm_id
    assert client.searched[1]["q"] == "Joint Transportation Committee"  # clean, not prefixed


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


# --- pm_match guard: cross-Id re-key over-match (redesign, model A) ------------


async def test_pm_match_name_skips_candidate_claimed_by_another_committee(db_session, descriptor):
    """A normalized-name-equal PM org that already carries a committee identifier is
    claimed by a *different* WSL Id (stage 1 already ran on ours and missed), so we
    must create a new org — NOT glue onto the claimed one. Guards the re-key over-match
    that crash-looped the sidecar (WSL re-keys committees across eras)."""
    claimed_pm = ULID()
    row = await _add_org(db_session, source_id="17366", name="Appropriations")
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss (our 17366 not in PM yet)
            EntityPage(  # FTS surfaces the same-name org already claimed by Id 875
                records=[{"id": str(claimed_pm), "name": "Appropriations", "parent_id": None}],
                cursor=None,
            ),
        ],
        entities={
            str(claimed_pm): {
                "id": str(claimed_pm),
                "name": "Appropriations",
                "identifiers": [{"type_slug": "org_wa_legislature_committee_id", "value": "875"}],
            }
        },
    )

    matched = await descriptor.pm_match(client, db_session, row)

    assert matched is None  # claimed → create-new, no cross-Id glue
    # The name candidate's detail was fetched to read its identifiers.
    assert (descriptor.read_path, str(claimed_pm)) in client.fetched


async def test_pm_match_name_adopts_unclaimed_same_name_org(db_session, descriptor):
    """The legitimate adopt path survives the guard: a same-name PM org carrying **no**
    committee identifier (PM's pre-curated, unclaimed org) is still matched + adopted."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-42", name="Health Committee")
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(
                records=[{"id": str(pm_id), "name": "Health Committee", "parent_id": None}],
                cursor=None,
            ),
        ],
        entities={
            str(pm_id): {
                "id": str(pm_id),
                "name": "Health Committee",
                "identifiers": [],  # unclaimed → adoptable
            }
        },
    )

    matched = await descriptor.pm_match(client, db_session, row)

    assert matched == pm_id
    assert (descriptor.read_path, str(pm_id)) in client.fetched


async def test_pm_match_name_adopts_when_candidate_detail_absent(db_session, descriptor):
    """A candidate whose detail fetch 404s (get_entity → None) is treated as unclaimed
    and still adopted — the guard fails open on a missing detail, not closed."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-77", name="Rules Committee")
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(
                records=[{"id": str(pm_id), "name": "Rules Committee", "parent_id": None}],
                cursor=None,
            ),
        ],
        # No entities preset → get_entity returns None for this candidate.
    )

    assert await descriptor.pm_match(client, db_session, row) == pm_id


async def test_pm_match_identifier_hit_skips_detail_fetch(db_session, descriptor):
    """The guard's detail fetch is on the name path only: a stage-1 identifier hit
    short-circuits before any get_entity (no wasted detail fetch on the happy path)."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="House Approps")
    client = FakeClient(search_pages=[EntityPage(records=[{"id": str(pm_id)}], cursor=None)])

    matched = await descriptor.pm_match(client, db_session, row)

    assert matched == pm_id
    assert client.fetched == []  # no detail fetch on the identifier happy path


async def test_pm_match_guard_drops_claimed_then_adopts_survivor(db_session, descriptor):
    """The guard runs before hierarchy: two same-name candidates, one claimed by another
    committee Id and one unclaimed → the claimed one is dropped, leaving a single
    survivor that is adopted directly (no parent needed to disambiguate)."""
    claimed_pm = ULID()
    survivor_pm = ULID()
    row = await _add_org(db_session, source_id="C-DUP", name="Local Government")
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(
                records=[
                    {"id": str(claimed_pm), "name": "Local Government", "parent_id": None},
                    {"id": str(survivor_pm), "name": "Local Government", "parent_id": None},
                ],
                cursor=None,
            ),
        ],
        entities={
            str(claimed_pm): {
                "id": str(claimed_pm),
                "name": "Local Government",
                "identifiers": [{"type_slug": "org_wa_legislature_committee_id", "value": "OTHER"}],
            },
            str(survivor_pm): {
                "id": str(survivor_pm),
                "name": "Local Government",
                "identifiers": [],
            },
        },
    )

    assert await descriptor.pm_match(client, db_session, row) == survivor_pm


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


async def test_to_observation_sends_clean_short_name_for_other_class(
    db_session, descriptor, usa_wa
):
    """Meeting-derived Joint/`Other` orgs send the clean short_name, not the
    agency-double-prefixed name, as the PM name evidence (#61)."""
    row = await _add_org(
        db_session,
        source_id="-140",
        name="Joint Joint Transportation Committee",  # WSL LongName, verbatim local
        short_name="Joint Transportation Committee",  # clean Name
        org_type="other",
        jurisdiction_id=usa_wa.id,
    )

    obs = await descriptor.to_observation(db_session, row)

    assert obs["names"] == [{"name": "Joint Transportation Committee", "name_type": "legal"}]


async def test_to_observation_other_class_falls_back_to_name_without_short_name(
    db_session, descriptor, usa_wa
):
    """An `other` org lacking short_name keeps `name` — no empty/None name emitted."""
    row = await _add_org(
        db_session,
        source_id="-999",
        name="Joint Some Body",
        short_name=None,
        org_type="other",
        jurisdiction_id=usa_wa.id,
    )

    obs = await descriptor.to_observation(db_session, row)

    assert obs["names"] == [{"name": "Joint Some Body", "name_type": "legal"}]


async def test_to_observation_keeps_name_for_committee_class(db_session, descriptor, usa_wa):
    """Non-`other` classes still send `name` even when a (terse) short_name exists —
    e.g. House/Senate committees, whose short_name would lose chamber context."""
    row = await _add_org(
        db_session,
        source_id="C-9",
        name="House Finance",
        short_name="Finance",
        org_type="committee",
        jurisdiction_id=usa_wa.id,
    )

    obs = await descriptor.to_observation(db_session, row)

    assert obs["names"] == [{"name": "House Finance", "name_type": "legal"}]


async def test_to_observation_emits_acronym_and_phone(db_session, descriptor, usa_wa):
    """acronym → org_acronyms[0]; phone → a labelled phone contact_method.

    WSL carries no per-phone label, so committee phones get a static
    ``display_label`` (#31) — operators can't read an unlabelled number."""
    row = await _add_org(
        db_session,
        source_id="C-9",
        name="House Committee on Appropriations",
        jurisdiction_id=usa_wa.id,
        acronym="APP",
        phone="(360) 786-7204",
    )

    obs = await descriptor.to_observation(db_session, row)

    assert obs["org_acronyms"] == [{"acronym": "APP"}]
    assert obs["contact_methods"] == [
        {
            "contact_type": "phone",
            "value": "(360) 786-7204",
            "display_label": "Committee Office",
        }
    ]


async def test_to_observation_phone_label_falls_back_for_non_committee(
    db_session, descriptor, usa_wa
):
    """A non-committee org with a phone gets the generic ``Main Office`` label —
    ``Committee Office`` is committee-specific (the descriptor is org-type-generic)."""
    row = await _add_org(
        db_session,
        source_id="HOUSE",
        name="House",
        org_type="chamber",
        jurisdiction_id=usa_wa.id,
        phone="(360) 786-0000",
    )

    obs = await descriptor.to_observation(db_session, row)

    assert obs["contact_methods"] == [
        {"contact_type": "phone", "value": "(360) 786-0000", "display_label": "Main Office"}
    ]


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
    active=True,
    acronyms=None,
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
    record = {
        "id": str(pm_id),
        "name": name,
        "parent_id": str(parent_id) if parent_id else None,
        "jurisdiction_affiliations": affiliations,
        "updated_at": updated_at,
        # power-map#240: detail payload carries ``active`` (required bool).
        "active": active,
    }
    if acronyms is not None:
        # OrgDetail's embedded ``acronyms: list[OrgAcronym]`` ({id, acronym, is_canonical}).
        record["acronyms"] = acronyms
    return record


def _pm_acronym(acronym, *, is_canonical=False, acr_id=None):
    """A single PM ``OrgAcronym`` embedded-list entry ({id, acronym, is_canonical})."""
    return {
        "id": str(acr_id or ULID()),
        "acronym": acronym,
        "is_canonical": is_canonical,
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
    assert row.archived_at is None  # a live (un-archived) record leaves the tombstone clear


async def test_upsert_adopts_pm_canonical_acronym_into_scalar(db_session, descriptor):
    """#65: symmetric with name adoption — the ``is_canonical=true`` acronym in PM's
    embedded ``acronyms[]`` is resolved into ``Organization.acronym`` (the scalar), so a
    curated canonical (``WA Senate HSG``) replaces the produced value (``HSG``). The
    child-table mirror already lands both variants; this is the scalar the #47 docstring
    promises to be PM-resolved."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="Housing", anchor=pm_id, acronym="HSG")

    record = _pm_org(
        pm_id,
        name="Housing",
        acronyms=[
            _pm_acronym("HSG", is_canonical=False),
            _pm_acronym("WA Senate HSG", is_canonical=True),
        ],
    )
    result = await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert result is row
    assert row.acronym == "WA Senate HSG"  # adopted PM's canonical, not the local produced


async def test_upsert_keeps_local_acronym_when_pm_has_no_canonical(db_session, descriptor):
    """When PM reports acronyms but none is ``is_canonical``, do NOT clobber the local
    scalar with None — symmetric with name adoption's ``if name:`` guard (an absent
    canonical never nulls the produced value)."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-2", name="X", anchor=pm_id, acronym="APP")

    record = _pm_org(pm_id, name="X", acronyms=[_pm_acronym("APP", is_canonical=False)])
    await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert row.acronym == "APP"  # untouched — no canonical to resolve


async def test_upsert_keeps_local_acronym_when_record_omits_acronyms(db_session, descriptor):
    """A search-shaped record (no embedded ``acronyms`` key) must not touch the scalar —
    the same missing-key guard as ``active``/``names``. Only a detail payload resolves it."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-3", name="X", anchor=pm_id, acronym="APP")

    record = _pm_org(pm_id, name="X")  # no acronyms key
    assert "acronyms" not in record
    await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert row.acronym == "APP"  # untouched


async def test_upsert_keeps_local_acronym_when_pm_acronyms_empty(db_session, descriptor):
    """PM reporting ``acronyms: []`` (an org with zero acronyms) prunes the child mirror
    but must not null the scalar — no canonical present, so the produced value stands."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-4", name="X", anchor=pm_id, acronym="APP")

    record = _pm_org(pm_id, name="X", acronyms=[])
    await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert row.acronym == "APP"  # untouched


async def test_upsert_update_only_skips_unknown_org(db_session, descriptor):
    """A feed change to an org we never produced is skipped — not mirrored — so a
    later adapter row + sweep does not create a duplicate."""
    result = await descriptor.upsert_from_pm(db_session, _pm_org(ULID()))

    assert result is None
    assert (await db_session.execute(select(Organization))).scalars().all() == []


async def test_upsert_mirrors_pm_archived_at_to_retired_tombstone(db_session, descriptor):
    """PM archival (its "inactive" signal) is not a delete — it arrives as an
    ``updated`` feed event carrying ``archived_at`` on the still-present record.
    Mirror it onto the local retirement tombstone so the inactivated committee
    drops out of live reads (usa-wa#40). PM curates the inactivation (incl. the
    dormant-vs-abolished call); we only mirror — ``authority = "pm"``."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="X", anchor=pm_id)
    assert row.archived_at is None

    record = _pm_org(pm_id, name="X")
    record["archived_at"] = "2026-06-20T00:00:00Z"
    result = await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert result is row
    assert row.archived_at == datetime(2026, 6, 20, tzinfo=UTC)  # mirrors PM's own clock


async def test_upsert_adopts_name_and_archives_together(db_session, descriptor):
    """The realistic feed shape: one record carries both a (possibly new) canonical
    name and ``archived_at``. The mirror coexists with name adoption — name is taken
    *and* the tombstone is stamped in the same upsert."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="Old Name", anchor=pm_id)

    record = _pm_org(pm_id, name="Regulated Substances & Gaming")
    record["archived_at"] = "2026-06-20T00:00:00Z"
    result = await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert result is row
    assert row.name == "Regulated Substances & Gaming"
    assert row.archived_at == datetime(2026, 6, 20, tzinfo=UTC)


async def test_upsert_clears_tombstone_when_pm_unarchives(db_session, descriptor):
    """PM un-archiving (``archived_at`` back to null/absent) revives the row — clear
    the mirror. Safe against resurrecting a genuine delete: a deleted PM org has no
    record to deliver an ``updated`` event, so this path never fires for it."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="X", anchor=pm_id)
    row.archived_at = datetime(2026, 6, 20, tzinfo=UTC)
    await db_session.flush()

    result = await descriptor.upsert_from_pm(db_session, _pm_org(pm_id, name="X"), existing=row)

    assert result is row
    assert row.archived_at is None


async def test_upsert_mirrors_pm_active_flag(db_session, descriptor):
    """power-map#240: mirror PM's ``active`` (orgs-only domain flag) onto the local
    column. PM is authority; a feed ``updated`` carrying ``active=false`` (a
    dissolved committee) lands locally — but the row stays live (not a hide gate)."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="X", anchor=pm_id)
    assert row.active is True  # default

    result = await descriptor.upsert_from_pm(
        db_session, _pm_org(pm_id, name="X", active=False), existing=row
    )

    assert result is row
    assert row.active is False
    assert row.archived_at is None  # active is orthogonal to archival — not a hide gate


async def test_upsert_active_round_trips_back_to_true(db_session, descriptor):
    """A re-activation (``active`` back to true) mirrors in — PM-authoritative LWW."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="X", anchor=pm_id)
    row.active = False
    await db_session.flush()

    await descriptor.upsert_from_pm(db_session, _pm_org(pm_id, name="X", active=True), existing=row)

    assert row.active is True


async def test_upsert_leaves_active_untouched_when_record_omits_it(db_session, descriptor):
    """A search-shaped record omits ``active`` (detail-only field). Guard ``is not
    None`` so an absent key never clobbers the local value to false/None."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-1", name="X", anchor=pm_id)
    row.active = False
    await db_session.flush()

    record = _pm_org(pm_id, name="X")
    del record["active"]  # search results carry no active
    await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert row.active is False  # untouched, not reset to default-true


async def test_to_observation_omits_active(db_session, descriptor, usa_wa):
    """``active`` is PM-authoritative and rejected on archived orgs
    (``active_on_archived_org``); the routine observation must never echo it back —
    that would invite an LWW write-back fight and the 422. Producer-set is #44."""
    row = await _add_org(db_session, source_id="C-1", name="X")
    payload = await descriptor.to_observation(db_session, row)

    assert "active" not in payload


async def test_to_active_observation_keys_by_anchor(db_session, descriptor):
    """The producer active-flag payload (#44) is enrich-keyed by the PM anchor
    (``pm_org_id``) and asserts only ``active`` — no name/acronym evidence
    re-asserted (the org is already curated in PM). Synchronous."""
    pm_id = ULID()
    row = await _add_org(db_session, source_id="C-9", name="Defunct Committee", anchor=pm_id)

    retire = descriptor.to_active_observation(row, active=False)
    reactivate = descriptor.to_active_observation(row, active=True)

    assert retire["identifier_type"] == "pm_org_id"
    assert retire["identifier_value"] == str(pm_id)
    assert retire["active"] is False
    assert reactivate["active"] is True  # same shape drives reactivation
    # Minimal mutation — no curated-evidence fields ride along.
    for payload in (retire, reactivate):
        assert "names" not in payload
        assert "org_acronyms" not in payload
        assert "contact_methods" not in payload


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

    assert obs["org_acronyms"] == [{"acronym": "APP"}]
    assert obs["contact_methods"] == [
        {
            "contact_type": "phone",
            "value": "(360) 786-7204",
            "display_label": "Committee Office",
        }
    ]
    # Still anchor-keyed and narrow — parent/affiliations remain PM-curated.
    assert obs["identifier_type"] == "pm_org_id"
    assert "jurisdiction_affiliations" not in obs
    assert "organization_parent_id" not in obs


# --- rematch_anchor (merge-orphan self-heal, #31) ----------------------------


async def test_rematch_anchor_resolves_winner_by_identifier(db_session, descriptor):
    """A dead anchor re-resolves to the merge-winner via an exact identifier lookup
    (no jurisdiction scope; no name/hierarchy fuzz)."""
    winner = ULID()
    row = await _add_org(db_session, source_id="C-1", name="House Approps")
    client = FakeClient(search_pages=[EntityPage(records=[{"id": str(winner)}], cursor=None)])

    assert await descriptor.rematch_anchor(client, db_session, row) == winner
    assert len(client.searched) == 1
    assert client.searched[0]["identifier_type"] == "org_wa_legislature_committee_id"
    assert client.searched[0]["identifier_value"] == "C-1"
    assert client.searched[0]["jurisdiction"] is None


async def test_rematch_anchor_returns_none_on_identifier_miss(db_session, descriptor):
    """No identifier winner → None (the engine retires: genuine delete, not a merge)."""
    row = await _add_org(db_session, source_id="C-2", name="Gone Committee")
    client = FakeClient(search_pages=[EntityPage(records=[], cursor=None)])

    assert await descriptor.rematch_anchor(client, db_session, row) is None


def test_org_descriptor_supports_rematch_and_lifecycle_columns():
    desc = OrganizationDescriptor()
    assert desc.supports_rematch is True
    assert desc.deleted_column == "deleted_at"
    assert desc.archived_column == "archived_at"
