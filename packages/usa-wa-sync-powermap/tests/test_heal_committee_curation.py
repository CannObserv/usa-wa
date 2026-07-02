"""One-shot force-adopt heal for LWW-locked committees (#65 Part 2).

The daily refresh's clock-bump (pre-fill-only) left 13 committees with
``local.updated_at`` ahead of PM's, so the sidecar's LWW keeps the stale local
values and never adopts PM's curation. This CLI force-applies PM's curated record
to the whole anchored produced cohort — the PM-wins branch of ``apply_record``
(``upsert_from_pm`` + clock-parity stamp) run unconditionally, bypassing the LWW
check — so the locked rows adopt curation once. Idempotent; a no-op on rows already
at parity. This suite drives that: adopt despite a newer local clock, stamp parity,
skip a PM-404, and abort on an empty cohort.
"""

from datetime import UTC, datetime

from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from usa_wa_sync_powermap import heal_committee_curation as heal
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor


async def _add_org(db_session, usa_wa, *, source_id, anchor, name, acronym=None, updated_at=None):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=name,
        short_name=name,
        acronym=acronym,
        org_type="committee",
        pm_organization_id=anchor,
        jurisdiction_id=usa_wa.id,
    )
    db_session.add(row)
    await db_session.flush()
    if updated_at is not None:  # force a local clock ahead of PM (LWW-locked)
        row.updated_at = updated_at
        await db_session.flush()
    return row


class _FakeClient:
    def __init__(self, by_id):
        self._by = by_id

    async def get_entity(self, _path, pm_id):
        return self._by.get(str(pm_id))

    async def list_entity_events(self, _path, _pm_id):
        return []


def _pm_record(pm_id, *, name, acronyms=None, updated_at="2030-01-01T00:00:00Z"):
    rec = {
        "id": str(pm_id),
        "name": name,
        "parent_id": None,
        "updated_at": updated_at,
        "active": True,
    }
    if acronyms is not None:
        rec["acronyms"] = acronyms
    return rec


async def test_heal_force_adopts_despite_newer_local_clock(db_session, usa_wa):
    anchor = ULID()
    # local clock far in the future — LWW would normally KEEP_LOCAL
    row = await _add_org(
        db_session,
        usa_wa,
        source_id="C-1",
        anchor=anchor,
        name="House Committee on Appropriations",
        acronym="APP",
        updated_at=datetime(2031, 1, 1, tzinfo=UTC),
    )
    client = _FakeClient(
        {
            str(anchor): _pm_record(
                anchor,
                name="Washington State House Appropriations Committee",
                acronyms=[{"id": str(ULID()), "acronym": "WA House APP", "is_canonical": True}],
                updated_at="2030-06-01T00:00:00Z",
            )
        }
    )
    descriptor = OrganizationDescriptor()

    result = await heal.heal_committee_curation(db_session, descriptor, client)

    assert result["healed"] == 1
    assert (
        row.name == "Washington State House Appropriations Committee"
    )  # adopted despite newer clock
    assert row.acronym == "WA House APP"
    # clock parity stamped so the next reconcile doesn't re-fight
    assert descriptor.last_updated(row) == descriptor.last_updated(
        {"updated_at": "2030-06-01T00:00:00Z"}
    )


async def test_heal_skips_pm_404(db_session, usa_wa):
    anchor = ULID()
    await _add_org(db_session, usa_wa, source_id="C-1", anchor=anchor, name="Gone")
    client = _FakeClient({})  # PM returns nothing → 404
    result = await heal.heal_committee_curation(db_session, OrganizationDescriptor(), client)
    assert result["healed"] == 0
    assert result["skipped_missing_pm"] == 1


async def test_heal_skips_unanchored(db_session, usa_wa):
    await _add_org(db_session, usa_wa, source_id="C-1", anchor=None, name="Never synced")
    client = _FakeClient({})
    result = await heal.heal_committee_curation(db_session, OrganizationDescriptor(), client)
    assert result["checked"] == 0  # unanchored excluded from the cohort
    assert result["aborted"] == "empty_cohort"


async def test_heal_empty_cohort_aborts(db_session, usa_wa):
    client = _FakeClient({})
    result = await heal.heal_committee_curation(db_session, OrganizationDescriptor(), client)
    assert result["aborted"] == "empty_cohort"


async def test_heal_is_idempotent(db_session, usa_wa):
    anchor = ULID()
    row = await _add_org(
        db_session, usa_wa, source_id="C-1", anchor=anchor, name="Old", acronym="O"
    )
    client = _FakeClient(
        {str(anchor): _pm_record(anchor, name="Curated", updated_at="2030-06-01T00:00:00Z")}
    )
    descriptor = OrganizationDescriptor()
    first = await heal.heal_committee_curation(db_session, descriptor, client)
    assert first["healed"] == 1
    assert row.name == "Curated"
    # second run re-applies the same values + parity → still "healed" count but no drift
    second = await heal.heal_committee_curation(db_session, descriptor, client)
    assert second["healed"] == 1
    assert row.name == "Curated"
