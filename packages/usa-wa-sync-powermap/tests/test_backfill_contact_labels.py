"""Backfill of contact_method display_labels onto PM (#31).

The first org-observation run (2026-06-19) submitted 30 committee phones with no
``display_label``. The label is now synthesized in ``to_observation``/
``to_enrich_observation``, but only *new* observations carry it — the already-sent
rows need a one-off re-observation. These tests pin that backfill: it re-submits a
contact-bearing observation for every produced org that has a phone, exercising
the round-trip update path (re-observe an already-anchored entity → PM applies the
new label).
"""

from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import ObservationResult
from clearinghouse_sync_powermap.models import DISPOSITION_AUTO_ATTACHED
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_sync_powermap.backfill_contact_labels import backfill_contact_labels
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor


async def _add_org(session, *, source_id, name, phone=None, anchor=None, jurisdiction_id=None):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=name,
        org_type="committee",
        phone=phone,
        pm_organization_id=anchor,
        jurisdiction_id=jurisdiction_id,
    )
    session.add(row)
    await session.flush()
    return row


def _anchoring(pm_id):
    """FakeClient result-factory: PM auto-attaches the re-observation to the entity."""

    def _result(_payload):
        return ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=pm_id, raw={})

    return _result


async def test_backfill_reobserves_anchored_phone_orgs_with_label(db_session, usa_wa):
    """An already-anchored org with a phone is re-observed via the enrich path, and
    the submitted payload carries the new ``display_label``."""
    anchor = ULID()
    await _add_org(
        db_session,
        source_id="C-1",
        name="House Appropriations",
        phone="(360) 786-7204",
        anchor=anchor,
        jurisdiction_id=usa_wa.id,
    )
    client = FakeClient(observation_result=_anchoring(anchor))

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert len(client.posted) == 1
    observe_path, payload = client.posted[0]
    assert observe_path == "/api/v1/orgs/observations"
    # anchored → enrich path (keyed by pm_org_id), carrying the labelled contact.
    assert payload["identifier_type"] == "pm_org_id"
    assert payload["contact_methods"] == [
        {"contact_type": "phone", "value": "(360) 786-7204", "display_label": "Committee Office"}
    ]
    assert summary == {"scanned": 1, "submitted": 1, "anchored": 1, "dry_run": False}


async def test_backfill_skips_orgs_without_phone(db_session, usa_wa):
    """Orgs with no phone are not re-observed — the backfill only touches contacts."""
    await _add_org(db_session, source_id="C-2", name="No Phone Org", anchor=ULID())
    client = FakeClient()

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert client.posted == []
    assert summary == {"scanned": 0, "submitted": 0, "anchored": 0, "dry_run": False}


async def test_backfill_dry_run_posts_nothing(db_session, usa_wa):
    """``dry_run`` counts the cohort but submits no observation and mutates no anchor."""
    await _add_org(
        db_session, source_id="C-3", name="Dry Org", phone="(360) 786-0000", anchor=ULID()
    )
    client = FakeClient()

    summary = await backfill_contact_labels(
        db_session, OrganizationDescriptor(), client, dry_run=True
    )

    assert client.posted == []
    assert summary == {"scanned": 1, "submitted": 0, "anchored": 0, "dry_run": True}


async def test_backfill_unanchored_phone_org_uses_full_observe(db_session, usa_wa):
    """A produced-but-unanchored phone org falls back to the full observe payload
    (identifier-keyed) — still labelled — so a never-anchored row is not skipped."""
    await _add_org(
        db_session,
        source_id="C-4",
        name="Unanchored Committee",
        phone="(360) 786-1111",
        jurisdiction_id=usa_wa.id,
    )
    new_id = ULID()
    client = FakeClient(observation_result=_anchoring(new_id))

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    observe_path, payload = client.posted[0]
    assert payload["identifier_type"] == "org_wa_legislature_committee_id"
    assert payload["contact_methods"][0]["display_label"] == "Committee Office"
    assert summary == {"scanned": 1, "submitted": 1, "anchored": 1, "dry_run": False}
