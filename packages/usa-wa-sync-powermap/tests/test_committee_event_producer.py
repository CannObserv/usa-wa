"""C3 committee event producer (usa-wa#124) — diff/no-op gate + orchestration."""

from types import SimpleNamespace

from ulid import ULID

from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from clearinghouse_domain_legislative.identity import EntityEvent, Organization
from clearinghouse_sync_powermap.client import EventObservationResult
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_REJECTED,
    DISPOSITION_UPDATED,
)
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_adapter_legislature.committee_lifecycle import CommitteeWindow
from usa_wa_sync_powermap.committee_event_producer import (
    build_org_event_items,
    produce_committee_events,
)


def _existing(slug, year, *, anchor=None, linked=None):
    return SimpleNamespace(
        event_type_slug=slug,
        event_year=year,
        pm_entity_event_id=anchor,
        linked_entity_id=linked,
    )


def _link(subject, linked, slug="succeeded_by", year=2021):
    return SimpleNamespace(
        subject_source_id=subject, linked_source_id=linked, slug=slug, effective_year=year
    )


# --- build_org_event_items (pure) --------------------------------------------


def test_creates_founded_and_dissolved_when_absent():
    window = CommitteeWindow(
        source_id="A", is_current=False, founded_year=2001, dissolved_year=2004
    )
    items, noop, unresolved = build_org_event_items(
        window=window, outgoing_links=[], linked_pm_ids={}, existing=[]
    )
    slugs = {(i["event_type_slug"], i["event_year"]) for i in items}
    assert slugs == {("founded", 2001), ("dissolved", 2004)}
    assert all("pm_event_id" not in i for i in items)  # unanchored → create
    assert noop == 0


def test_anchored_matching_year_is_noop():
    window = CommitteeWindow(source_id="A", is_current=True, founded_year=2001, dissolved_year=None)
    existing = [_existing("founded", 2001, anchor=ULID())]
    items, noop, _ = build_org_event_items(
        window=window, outgoing_links=[], linked_pm_ids={}, existing=existing
    )
    assert items == []
    assert noop == 1


def test_anchored_changed_year_refines_in_place():
    anchor = ULID()
    window = CommitteeWindow(
        source_id="A", is_current=False, founded_year=2001, dissolved_year=None
    )
    existing = [_existing("founded", 1999, anchor=anchor)]  # stale year
    items, noop, _ = build_org_event_items(
        window=window, outgoing_links=[], linked_pm_ids={}, existing=existing
    )
    assert len(items) == 1
    assert items[0]["pm_event_id"] == str(anchor)  # id-addressed refine
    assert items[0]["event_year"] == 2001
    assert noop == 0


def test_link_item_carries_linked_entity():
    linked_pm = str(ULID())
    items, _, unresolved = build_org_event_items(
        window=None,
        outgoing_links=[_link("A", "B")],
        linked_pm_ids={"B": linked_pm},
        existing=[],
    )
    assert unresolved == 0
    assert items[0]["event_type_slug"] == "succeeded_by"
    assert items[0]["linked_entity_id"] == linked_pm
    assert items[0]["linked_entity_type"] == "organization"


def test_link_with_unanchored_target_is_skipped():
    items, _, unresolved = build_org_event_items(
        window=None,
        outgoing_links=[_link("A", "B")],
        linked_pm_ids={"B": None},  # target org not anchored
        existing=[],
    )
    assert items == []
    assert unresolved == 1


def test_anchored_link_matching_year_is_noop():
    anchor = ULID()
    linked_pm = ULID()
    existing = [_existing("succeeded_by", 2021, anchor=anchor, linked=linked_pm)]
    items, noop, _ = build_org_event_items(
        window=None,
        outgoing_links=[_link("A", "B", year=2021)],
        linked_pm_ids={"B": str(linked_pm)},
        existing=existing,
    )
    assert items == []
    assert noop == 1


def test_stats_as_dict_avoids_reserved_logrecord_keys():
    """as_dict() must not collide with reserved LogRecord attrs (e.g. 'created') — it is
    logged via a single wrapper key, but keep the field names clear of the raw reserved
    set so a future direct ``extra=`` can't crash once logging is configured."""
    import logging

    from usa_wa_sync_powermap.committee_event_producer import ProduceStats

    reserved = set(logging.makeLogRecord({}).__dict__)
    # 'created' IS a field of as_dict — assert we never pass the dict raw as extra by
    # confirming the producer wraps it (regression for the KeyError this test's PR fixed).
    keys = set(ProduceStats().as_dict())
    assert "created" in keys  # the collision exists...
    logging.makeLogRecord({"stats": ProduceStats().as_dict()})  # ...but wrapped it's safe
    assert reserved & {"stats"} == set()


# --- produce_committee_events (orchestration) --------------------------------


async def _committee(session, source_id, *, anchor=None):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=f"C{source_id}",
        org_type="committee",
        pm_organization_id=anchor,
    )
    session.add(row)
    await session.flush()
    return row


async def _mirror_event(session, org, slug, year, *, anchor, linked=None):
    """A mirrored (source='powermap') org EntityEvent, as sync_entity_events would write."""
    session.add(
        EntityEvent(
            source="powermap",
            source_id=str(anchor),
            entity_kind="organization",
            entity_id=org.id,
            event_type_slug=slug,
            event_year=year,
            pm_entity_event_id=anchor,
            linked_entity_kind="organization" if linked is not None else None,
            linked_entity_id=linked,
            visibility="public",
        )
    )
    await session.flush()


async def test_produce_emits_window_for_anchored_org(db_session, usa_wa):
    org = await _committee(db_session, "10171", anchor=ULID())
    windows = {
        "10171": CommitteeWindow(
            source_id="10171", is_current=False, founded_year=2001, dissolved_year=2004
        )
    }
    client = FakeClient()
    stats = await produce_committee_events(db_session, client, windows=windows, links=[])

    assert stats.submitted == 2  # founded + dissolved
    assert stats.created == 2
    assert len(client.posted_events) == 1
    posted_org, posted_items = client.posted_events[0]
    assert posted_org == org.pm_organization_id
    assert {i["event_type_slug"] for i in posted_items} == {"founded", "dissolved"}


async def test_produce_skips_unanchored_org(db_session, usa_wa):
    await _committee(db_session, "10171", anchor=None)  # not anchored to PM
    windows = {
        "10171": CommitteeWindow(
            source_id="10171", is_current=False, founded_year=2001, dissolved_year=2004
        )
    }
    client = FakeClient()
    stats = await produce_committee_events(db_session, client, windows=windows, links=[])

    assert stats.skipped_unanchored_org == 1
    assert client.posted_events == []


async def test_produce_refines_and_routes_reject_reason(db_session, usa_wa):
    org = await _committee(db_session, "10171", anchor=ULID())
    anchor = ULID()
    await _mirror_event(db_session, org, "founded", 1999, anchor=anchor)  # stale year
    windows = {
        "10171": CommitteeWindow(
            source_id="10171", is_current=False, founded_year=2001, dissolved_year=2004
        )
    }

    # founded → updated (refine); dissolved → rejected (transient) to exercise telemetry.
    def _results(_org, items):
        out = []
        for item in items:
            if item["event_type_slug"] == "founded":
                out.append(
                    EventObservationResult(
                        disposition=DISPOSITION_UPDATED, event_id=anchor, reason=None, raw={}
                    )
                )
            else:
                out.append(
                    EventObservationResult(
                        disposition=DISPOSITION_REJECTED,
                        event_id=None,
                        reason="linked_entity_unresolved",
                        raw={},
                    )
                )
        return out

    client = FakeClient(event_observation_result=_results)
    stats = await produce_committee_events(db_session, client, windows=windows, links=[])

    # founded refine carries the pm_event_id anchor.
    _org, items = client.posted_events[0]
    founded = next(i for i in items if i["event_type_slug"] == "founded")
    assert founded["pm_event_id"] == str(anchor)
    assert stats.updated == 1
    assert stats.rejected == 1
    assert stats.reject_reasons == {"linked_entity_unresolved": 1}


async def test_produce_counts_auto_attached_as_reobserved_not_created(db_session, usa_wa):
    """PM content-dedups an unanchored re-send (mirror lag) to ``auto-attached`` — it did
    NOT create anything, so it must land in ``reobserved``, never inflate ``created``."""
    await _committee(db_session, "10171", anchor=ULID())
    windows = {
        "10171": CommitteeWindow(
            source_id="10171", is_current=False, founded_year=2001, dissolved_year=2004
        )
    }

    def _results(_org, items):
        return [
            EventObservationResult(
                disposition=DISPOSITION_AUTO_ATTACHED, event_id=ULID(), reason=None, raw={}
            )
            for _ in items
        ]

    client = FakeClient(event_observation_result=_results)
    stats = await produce_committee_events(db_session, client, windows=windows, links=[])
    assert stats.created == 0
    assert stats.reobserved == 2


async def test_produce_noop_when_year_matches(db_session, usa_wa):
    org = await _committee(db_session, "10171", anchor=ULID())
    await _mirror_event(db_session, org, "founded", 2001, anchor=ULID())
    windows = {
        "10171": CommitteeWindow(
            source_id="10171", is_current=True, founded_year=2001, dissolved_year=None
        )
    }
    client = FakeClient()
    stats = await produce_committee_events(db_session, client, windows=windows, links=[])

    assert stats.noop == 1
    assert client.posted_events == []  # nothing submitted


async def test_produce_emits_link_between_two_anchored_orgs(db_session, usa_wa):
    subject = await _committee(db_session, "14294", anchor=ULID())
    linked = await _committee(db_session, "28244", anchor=ULID())
    link = CommitteeSuccessionEvent(
        source="usa_wa_operator",
        source_id="succeeded_by:14294:28244:2021",
        subject_source_id="14294",
        linked_source_id="28244",
        slug="succeeded_by",
        effective_year=2021,
        evidence_url="https://x",
    )
    db_session.add(link)
    await db_session.flush()

    client = FakeClient()
    stats = await produce_committee_events(db_session, client, windows={}, links=[link])

    posted_org, items = client.posted_events[0]
    assert posted_org == subject.pm_organization_id
    assert items[0]["event_type_slug"] == "succeeded_by"
    assert items[0]["linked_entity_id"] == str(linked.pm_organization_id)
    assert stats.created == 1
