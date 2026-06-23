"""Backfill of contact_method display_labels onto PM (#31).

The first org-observation run (2026-06-19) submitted 30 committee phones with no
``display_label``. The label is now synthesized in ``to_observation``/
``to_enrich_observation``, but only *new* observations carry it — the already-sent
rows need a one-off re-observation. These tests pin that backfill: it re-submits a
contact-bearing observation for every produced org that has a phone, isolating each
row (a rejection or transport failure is counted, not fatal) and exercising the
round-trip update path (re-observe an already-anchored entity → PM applies the
new label).
"""

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import (
    DeliveryBlockedError,
    ObservationResult,
    PayloadRejectedError,
    RetryableClientError,
)
from clearinghouse_sync_powermap.models import DISPOSITION_AUTO_ATTACHED, DISPOSITION_REJECTED
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_sync_powermap.backfill_contact_labels import backfill_contact_labels
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor


async def _add_org(
    session,
    *,
    source_id,
    name,
    phone=None,
    anchor=None,
    jurisdiction_id=None,
    source="usa_wa_legislature",
):
    row = Organization(
        source=source,
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
    assert summary == {
        "scanned": 1,
        "accepted": 1,
        "rejected": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": False,
    }


async def test_backfill_skips_orgs_without_phone(db_session, usa_wa):
    """Orgs with no phone are not re-observed — the backfill only touches contacts."""
    await _add_org(db_session, source_id="C-2", name="No Phone Org", anchor=ULID())
    client = FakeClient()

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert client.posted == []
    assert summary["scanned"] == 0
    assert summary["accepted"] == 0


async def test_backfill_ignores_other_sources(db_session, usa_wa):
    """A phone-bearing org from a different source is out of scope (#31 CR finding 7)."""
    await _add_org(
        db_session,
        source_id="X-1",
        name="Other Source Org",
        phone="(360) 786-9999",
        anchor=ULID(),
        source="usa_wa_pdc",
    )
    client = FakeClient()

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert client.posted == []
    assert summary["scanned"] == 0


async def test_backfill_skips_retired_orgs(db_session, usa_wa):
    """A retired (PM-deleted) org carries a dead anchor — re-observing it would push
    against a tombstoned PM entity. The backfill excludes it (usa-wa#38)."""
    from datetime import UTC, datetime

    row = await _add_org(
        db_session,
        source_id="C-dead",
        name="Merged-Away Committee",
        phone="(360) 786-1111",
        anchor=ULID(),
        jurisdiction_id=usa_wa.id,
    )
    row.retired_at = datetime.now(UTC)
    await db_session.flush()
    client = FakeClient()

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert client.posted == []
    assert summary["scanned"] == 0


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
    assert summary == {
        "scanned": 1,
        "accepted": 0,
        "rejected": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": True,
    }


async def test_backfill_unanchored_phone_org_uses_full_observe(db_session, usa_wa):
    """A produced-but-unanchored phone org falls back to the full observe payload
    (identifier-keyed) — still labelled — and the returned anchor is captured."""
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
    assert summary["accepted"] == 1
    row = (await db_session.execute(select(Organization))).scalars().one()
    assert row.pm_organization_id == new_id  # newly-captured anchor


async def test_backfill_counts_rejected_disposition(db_session, usa_wa):
    """A ``rejected`` disposition is counted as rejected, not accepted (no anchor write)."""

    def _rejected(_payload):
        return ObservationResult(disposition=DISPOSITION_REJECTED, pm_id=None, raw={"x": 1})

    await _add_org(
        db_session, source_id="C-5", name="Rej Org", phone="(360) 786-2222", anchor=ULID()
    )
    client = FakeClient(observation_result=_rejected)

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert summary["accepted"] == 0
    assert summary["rejected"] == 1


async def test_backfill_isolates_delivery_failure_and_continues(db_session, usa_wa):
    """A per-row transport blip is counted as failed and skipped — it must not abort
    the run, so a later healthy row still delivers (#31 CR finding 1)."""
    bad_anchor, good_anchor = ULID(), ULID()
    await _add_org(
        db_session, source_id="C-A", name="Bad Row", phone="(360) 786-3333", anchor=bad_anchor
    )
    await _add_org(
        db_session, source_id="C-B", name="Good Row", phone="(360) 786-4444", anchor=good_anchor
    )

    calls = {"n": 0}

    def _flaky(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableClientError("PM 503")
        return ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=good_anchor, raw={})

    client = FakeClient(observation_result=_flaky)

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert summary["scanned"] == 2
    assert summary["failed"] == 1
    assert summary["accepted"] == 1


async def test_backfill_payload_rejection_is_isolated(db_session, usa_wa):
    """A ``PayloadRejectedError`` (422) is counted as rejected and does not abort."""

    def _raise(_payload):
        raise PayloadRejectedError("PM rejected the request (422)")

    await _add_org(
        db_session, source_id="C-6", name="Reject Exc", phone="(360) 786-5555", anchor=ULID()
    )
    client = FakeClient(observation_result=_raise)

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert summary == {
        "scanned": 1,
        "accepted": 0,
        "rejected": 1,
        "failed": 0,
        "skipped": 0,
        "dry_run": False,
    }


async def test_backfill_auth_block_aborts_run(db_session, usa_wa):
    """A ``DeliveryBlockedError`` (401/403) is a global credential failure, not a
    per-row condition — it propagates and aborts rather than failing every row
    against a dead endpoint (#31 CR round-2 finding 11)."""

    def _blocked(_payload):
        raise DeliveryBlockedError("PM 403 Insufficient scope")

    await _add_org(
        db_session, source_id="C-9", name="Blocked", phone="(360) 786-8888", anchor=ULID()
    )
    client = FakeClient(observation_result=_blocked)

    with pytest.raises(DeliveryBlockedError):
        await backfill_contact_labels(db_session, OrganizationDescriptor(), client)


async def test_backfill_counts_unexpected_disposition(db_session, usa_wa):
    """A result that is neither anchoring nor rejected (e.g. an id-less non-rejected
    disposition) is counted as failed, never silently dropped (#31 CR round-2 #12)."""

    def _weird(_payload):
        return ObservationResult(disposition="new", pm_id=None, raw={})

    await _add_org(db_session, source_id="C-W", name="Weird", phone="(360) 786-1212", anchor=ULID())
    client = FakeClient(observation_result=_weird)

    summary = await backfill_contact_labels(db_session, OrganizationDescriptor(), client)

    assert summary["accepted"] == 0
    assert summary["rejected"] == 0
    assert summary["failed"] == 1


async def test_backfill_propagates_unexpected_bug(db_session, usa_wa):
    """A non-delivery exception (a real bug) propagates — never silently counted."""

    def _boom(_payload):
        raise KeyError("payload construction bug")

    await _add_org(db_session, source_id="C-7", name="Buggy", phone="(360) 786-6666", anchor=ULID())
    client = FakeClient(observation_result=_boom)

    with pytest.raises(KeyError):
        await backfill_contact_labels(db_session, OrganizationDescriptor(), client)


async def test_backfill_skips_when_dependencies_not_ready(db_session, usa_wa):
    """A row whose PM prerequisites aren't ready is skipped, not posted (#31 CR finding 3)."""

    class _DepsNotReady(OrganizationDescriptor):
        async def dependencies_ready(self, session, row):
            return False

    await _add_org(
        db_session, source_id="C-8", name="Not Ready", phone="(360) 786-7777", anchor=ULID()
    )
    client = FakeClient(observation_result=_anchoring(ULID()))

    summary = await backfill_contact_labels(db_session, _DepsNotReady(), client)

    assert client.posted == []
    assert summary["skipped"] == 1
    assert summary["accepted"] == 0
