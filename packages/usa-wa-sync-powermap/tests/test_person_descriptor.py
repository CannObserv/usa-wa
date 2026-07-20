"""PersonDescriptor tests — PM-first cascade (identifier → server-side name)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import Person, PersonIdentifier
from clearinghouse_sync_powermap.client import EntityPage
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_sync_powermap.descriptors import PersonDescriptor
from usa_wa_sync_powermap.descriptors.person import identifier_type_for


@pytest.fixture
def descriptor() -> PersonDescriptor:
    return PersonDescriptor()


async def _add_person(
    session, *, source="usa_wa_legislature", source_id="M-1", name="Jay Inslee", anchor=None
):
    row = Person(source=source, source_id=source_id, name_full=name, pm_person_id=anchor)
    session.add(row)
    await session.flush()
    return row


def test_identifier_type_for_maps_source():
    assert identifier_type_for("usa_wa_legislature") == "person_wa_legislature_member_id"
    assert identifier_type_for("usa_wa_pdc") == "person_wa_pdc"
    assert identifier_type_for("other") is None


async def test_pm_match_identifier_hit(db_session, descriptor):
    pm_id = ULID()
    row = await _add_person(db_session, source_id="M-7")
    client = FakeClient(search_pages=[EntityPage(records=[{"id": str(pm_id)}], cursor=None)])

    assert await descriptor.pm_match(client, db_session, row) == pm_id
    assert len(client.searched) == 1
    assert client.searched[0]["identifier_type"] == "person_wa_legislature_member_id"


async def test_name_search_uses_configured_match_cap(db_session):
    """#12: the descriptor's ``search_match_cap`` is the ``limit`` passed to the
    name-match search."""
    row = await _add_person(db_session, source_id="M-CAP", name="Some Person")
    seen_limits: list[int] = []

    class _RecordingClient:
        async def search_entities(self, search_path, *, limit=20, **kwargs):
            seen_limits.append(limit)
            return EntityPage(records=[], cursor=None)

    descriptor = PersonDescriptor(search_match_cap=91)
    await descriptor.pm_match(_RecordingClient(), db_session, row)

    assert 91 in seen_limits


async def test_pm_match_name_fallback_confirms_normalized(db_session, descriptor):
    """PM's q filters by name server-side; the match is confirmed by exact
    normalized equality (so a loose q hit on a different person is rejected)."""
    pm_id = ULID()
    row = await _add_person(db_session, source_id="M-8", name="Jay Inslee")
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(
                records=[
                    {"id": str(ULID()), "display_name": "Jay Inslee Jr."},  # not exact
                    {"id": str(pm_id), "display_name": "Jay Inslee"},
                ],
                cursor=None,
            ),
        ]
    )

    assert await descriptor.pm_match(client, db_session, row) == pm_id
    assert client.searched[1]["q"] == "Jay Inslee"


async def test_pm_match_confirms_accent_variant(db_session, descriptor):
    """PM's FTS (pm_unaccent_simple, #201) returns an accent-folded match; our
    normalize_name now unaccents too, so the confirm step accepts it instead of
    rejecting it (the pre-#201 ASCII-only bug)."""
    pm_id = ULID()
    row = await _add_person(db_session, source_id="M-9", name="Jose Belen")  # adapter: ASCII
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),  # identifier miss
            EntityPage(records=[{"id": str(pm_id), "display_name": "José Belén"}], cursor=None),
        ]
    )

    assert await descriptor.pm_match(client, db_session, row) == pm_id


async def test_pm_match_ambiguous_returns_none(db_session, descriptor):
    """Two exact-name homonyms, nothing to disambiguate → None (don't anchor the
    wrong person); the engine will observe-create instead."""
    row = await _add_person(db_session, source_id="M-9", name="John Smith")
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),
            EntityPage(
                records=[
                    {"id": str(ULID()), "display_name": "John Smith"},
                    {"id": str(ULID()), "display_name": "John Smith"},
                ],
                cursor=None,
            ),
        ]
    )

    assert await descriptor.pm_match(client, db_session, row) is None


async def test_pm_match_no_match_returns_none(db_session, descriptor):
    row = await _add_person(db_session, source_id="M-NEW", name="Brand New Legislator")
    client = FakeClient(
        search_pages=[
            EntityPage(records=[], cursor=None),
            EntityPage(records=[{"id": str(ULID()), "display_name": "Someone Else"}], cursor=None),
        ]
    )
    assert await descriptor.pm_match(client, db_session, row) is None


async def test_to_observation_keys_on_identifier_and_name(db_session, descriptor):
    row = await _add_person(db_session, source_id="M-3", name="Jane Doe")
    obs = await descriptor.to_observation(db_session, row)
    assert obs["identifier_type"] == "person_wa_legislature_member_id"
    assert obs["identifier_value"] == "M-3"
    assert obs["names"] == [{"name": "Jane Doe", "name_type": "legal"}]
    assert "additional_identifiers" not in obs  # no child rows → key omitted


async def _add_identifier(session, person, *, scheme, value, source="usa_wa_pdc"):
    ident = PersonIdentifier(
        source=source,
        source_id=f"{value}:{scheme}",
        person_id=person.id,
        scheme=scheme,
        value=value,
    )
    session.add(ident)
    await session.flush()
    return ident


async def test_to_observation_emits_child_identifiers_as_additional(db_session, descriptor):
    """A cross-source child identifier (e.g. PDC's ``wa_pdc``) rides the WSL Person's
    observation as an ``additional_identifier`` so PM attaches it to the same person the
    primary (``person_wa_legislature_member_id``) resolves — the #69 cross-link."""
    row = await _add_person(db_session, source_id="M-3", name="Jane Doe")
    await _add_identifier(db_session, row, scheme="wa_pdc", value="159")

    obs = await descriptor.to_observation(db_session, row)

    assert obs["identifier_type"] == "person_wa_legislature_member_id"  # primary unchanged
    assert obs["identifier_value"] == "M-3"
    assert obs["additional_identifiers"] == [
        {"identifier_type_slug": "person_wa_pdc", "identifier_value": "159"}
    ]


async def test_to_observation_omits_child_that_duplicates_primary(db_session, descriptor):
    """A child row whose scheme maps to the primary slug isn't re-sent as additional."""
    row = await _add_person(db_session, source_id="M-3", name="Jane Doe")
    await _add_identifier(
        db_session, row, scheme="wa_legislature_member_id", value="M-3", source="usa_wa_legislature"
    )

    obs = await descriptor.to_observation(db_session, row)

    assert "additional_identifiers" not in obs


async def test_to_enrich_observation_merges_child_identifiers(db_session, descriptor):
    """Enrich re-keys to the PM anchor and carries BOTH the demoted primary AND the
    child identifiers as additional_identifiers (so the cross-link survives enrich)."""
    pm = ULID()
    row = await _add_person(db_session, source_id="M-1", name="Jay Inslee", anchor=pm)
    await _add_identifier(db_session, row, scheme="wa_pdc", value="159")

    obs = await descriptor.to_enrich_observation(db_session, row)

    assert obs["identifier_type"] == "pm_person_id"
    assert obs["identifier_value"] == str(pm)
    assert {"identifier_type_slug": "person_wa_pdc", "identifier_value": "159"} in obs[
        "additional_identifiers"
    ]
    assert {
        "identifier_type_slug": "person_wa_legislature_member_id",
        "identifier_value": "M-1",
    } in obs["additional_identifiers"]


async def test_local_match_by_anchor(db_session, descriptor):
    pm_id = ULID()
    row = await _add_person(db_session, source_id="M-1", anchor=pm_id)
    assert (await descriptor.local_match(db_session, {"id": str(pm_id)})).id == row.id
    assert await descriptor.local_match(db_session, {"id": str(ULID())}) is None


async def test_upsert_adopts_display_name_and_anchor(db_session, descriptor):
    row = await _add_person(db_session, source_id="M-1", name="Adapter Name")
    pm_id = ULID()
    record = {
        "id": str(pm_id),
        "display_name": "Jay R. Inslee",
        "updated_at": "2030-01-01T00:00:00Z",
    }

    result = await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert result is row
    assert row.name_full == "Jay R. Inslee"
    assert row.pm_person_id == pm_id


async def test_local_newer_is_noop_true_when_name_and_identifiers_match(db_session, descriptor):
    """#104: a local-newer person is spurious clock skew when re-producing its observation
    would leave PM unchanged — name equals PM's display_name and every additional identifier
    is already on PM's record. ``apply_record`` then adopts PM's clock instead of re-POSTing
    an identical observation every reconcile forever."""
    row = await _add_person(db_session, source_id="M-3", name="Jane Doe", anchor=ULID())
    await _add_identifier(db_session, row, scheme="wa_pdc", value="159")
    record = {
        "display_name": "Jane Doe",
        "identifiers": [{"type_slug": "person_wa_pdc", "value": "159"}],
    }
    assert await descriptor.local_newer_is_noop(db_session, row, record) is True


async def test_local_newer_is_noop_false_on_local_rename(db_session, descriptor):
    """A genuine local rename (name_full diverged from PM's display_name) must still enqueue —
    re-observing it would change PM, so it is NOT a spurious no-op."""
    row = await _add_person(db_session, source_id="M-3", name="Jane Q. Doe", anchor=ULID())
    record = {"display_name": "Jane Doe", "identifiers": []}
    assert await descriptor.local_newer_is_noop(db_session, row, record) is False


async def test_local_newer_is_noop_false_on_new_additional_identifier(db_session, descriptor):
    """A fresh cross-source identifier (e.g. a new ``wa_pdc`` link, #69) absent from PM's
    ``identifiers[]`` must still enqueue — the observation would add it to PM."""
    row = await _add_person(db_session, source_id="M-3", name="Jane Doe", anchor=ULID())
    await _add_identifier(db_session, row, scheme="wa_pdc", value="159")
    record = {"display_name": "Jane Doe", "identifiers": []}  # PM lacks the wa_pdc link
    assert await descriptor.local_newer_is_noop(db_session, row, record) is False


async def test_upsert_update_only_skips_unknown(db_session, descriptor):
    result = await descriptor.upsert_from_pm(
        db_session, {"id": str(ULID()), "display_name": "Ghost"}
    )
    assert result is None
    assert (await db_session.execute(select(Person))).scalars().all() == []


async def test_upsert_mirrors_pm_archived_at_to_retired_tombstone(db_session, descriptor):
    """PM archival (its "inactive" signal, not a delete) arrives as an ``updated``
    feed event carrying ``archived_at``; mirror it onto ``archived_at`` so the
    archived person drops out of live reads (usa-wa#41, parity with the org #40 fix)."""
    pm_id = ULID()
    row = await _add_person(db_session, source_id="M-1", anchor=pm_id)
    assert row.archived_at is None

    record = {"id": str(pm_id), "display_name": "Jay Inslee", "archived_at": "2026-06-20T00:00:00Z"}
    result = await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert result is row
    assert row.archived_at == datetime(2026, 6, 20, tzinfo=UTC)  # mirrors PM's own clock


async def test_upsert_clears_tombstone_when_pm_unarchives(db_session, descriptor):
    """PM un-archiving (``archived_at`` back to null/absent) revives the row."""
    pm_id = ULID()
    row = await _add_person(db_session, source_id="M-1", anchor=pm_id)
    row.archived_at = datetime(2026, 6, 20, tzinfo=UTC)
    await db_session.flush()

    record = {"id": str(pm_id), "display_name": "Jay Inslee"}
    result = await descriptor.upsert_from_pm(db_session, record, existing=row)

    assert result is row
    assert row.archived_at is None


async def test_last_updated_row_and_record(db_session, descriptor):
    row = await _add_person(db_session, source_id="M-1")
    row.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated(row) == datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated({"updated_at": "2026-06-02T00:00:00Z"}) == datetime(
        2026, 6, 2, tzinfo=UTC
    )


# --- enrich-on-match (#198) ---------------------------------------------------


async def test_needs_enrich(db_session, descriptor):
    row = await _add_person(db_session, source_id="M-1", name="Jay")
    assert await descriptor.needs_enrich({"identifiers": []}, row) is True
    has_it = {"identifiers": [{"type_slug": "person_wa_legislature_member_id", "value": "M-1"}]}
    assert await descriptor.needs_enrich(has_it, row) is False


async def test_to_enrich_observation_rekeys_to_pm_person_id(db_session, descriptor):
    pm = ULID()
    row = await _add_person(db_session, source_id="M-1", name="Jay Inslee", anchor=pm)

    obs = await descriptor.to_enrich_observation(db_session, row)

    assert obs["identifier_type"] == "pm_person_id"
    assert obs["identifier_value"] == str(pm)
    assert obs["additional_identifiers"] == [
        {"identifier_type_slug": "person_wa_legislature_member_id", "identifier_value": "M-1"}
    ]
    assert obs["names"] == [{"name": "Jay Inslee", "name_type": "legal"}]
