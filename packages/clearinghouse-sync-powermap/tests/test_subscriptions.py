"""SubscriptionReconciler tests — additive discovery → register → backfill.

Uses the shipped FakeClient/FakeDescriptor. The reconciler is the membership engine
behind the subscription-filtered feed (PM #203): it must register only the entities
not already subscribed and backfill their current state by id, additively.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import DiscoveredEntity, SubscriptionResult
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.subscriptions import DiscoverySpec, SubscriptionReconciler
from clearinghouse_sync_powermap.testing import FakeClient, FakeDescriptor, FakeEntity

SPEC = DiscoverySpec(root_type="jurisdiction", root_id="usa-wa", follow=["lineage", "roles"])


def _disc(pm_id, entity_type="fake", hops=1):
    return DiscoveredEntity(
        entity_type=entity_type, entity_id=pm_id, display_name="X", hops_from_root=hops
    )


def _record(pm_id, source_id, name):
    return {
        "id": str(pm_id),
        "source": "wsl",
        "source_id": source_id,
        "name": name,
        "updated_at": "2050-01-01T00:00:00Z",
    }


def _reconciler(client, descriptors):
    return SubscriptionReconciler(client, SyncEngine(descriptors, client), SPEC)


def _local_reconciler(client, descriptors):
    return SubscriptionReconciler(
        client, SyncEngine(descriptors, client), SPEC, include_local_cohort=True
    )


async def _seed(session, *, source_id, name, anchor=None, deleted_at=None):
    row = FakeEntity(
        source="wsl", source_id=source_id, name=name, pm_fake_id=anchor, deleted_at=deleted_at
    )
    session.add(row)
    await session.flush()
    return row


async def test_bootstrap_registers_all_and_backfills(db_session, fake_descriptor):
    """Empty registered set (bootstrap): every discovered id is registered + backfilled."""
    pm_id = ULID()
    client = FakeClient(
        discovered=[_disc(pm_id)],
        subscribed=[],
        entities={pm_id: _record(pm_id, "1", "FromBackfill")},
    )
    reconciler = _reconciler(client, [fake_descriptor])

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == [[pm_id]]  # the new id was registered
    assert pm_id in client.subscribed
    assert report.newly_subscribed == 1
    assert report.backfilled == 1
    row = (await db_session.execute(select(FakeEntity))).scalar_one()
    assert row.name == "FromBackfill"
    assert row.pm_fake_id == pm_id


async def test_additive_diff_only_registers_and_backfills_new(db_session, fake_descriptor):
    """An already-subscribed id is left alone; only the genuinely-new one is touched."""
    existing, fresh = ULID(), ULID()
    client = FakeClient(
        discovered=[_disc(existing), _disc(fresh)],
        subscribed=[existing],
        entities={
            existing: _record(existing, "1", "Existing"),
            fresh: _record(fresh, "2", "Fresh"),
        },
    )
    reconciler = _reconciler(client, [fake_descriptor])

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == [[fresh]]
    assert report.already_registered == 1
    assert report.newly_subscribed == 1
    assert report.backfilled == 1
    # Only the fresh entity was backfilled into the cache.
    names = {r.name for r in (await db_session.execute(select(FakeEntity))).scalars()}
    assert names == {"Fresh"}


async def test_idempotent_rerun_is_noop(db_session, fake_descriptor):
    """No drift: everything already registered → no register, no backfill."""
    pm_id = ULID()
    client = FakeClient(
        discovered=[_disc(pm_id)],
        subscribed=[pm_id],
        entities={pm_id: _record(pm_id, "1", "X")},
    )
    reconciler = _reconciler(client, [fake_descriptor])

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == []
    assert report.newly_subscribed == 0
    assert report.backfilled == 0
    assert (await db_session.execute(select(FakeEntity))).first() is None


async def test_unknown_entity_type_registered_but_not_backfilled(db_session, fake_descriptor):
    """A discovered type with no local descriptor is subscribed but skipped on backfill."""
    known, unknown = ULID(), ULID()
    client = FakeClient(
        discovered=[_disc(known), _disc(unknown, entity_type="mystery")],
        subscribed=[],
        entities={known: _record(known, "1", "Known")},
    )
    reconciler = _reconciler(client, [fake_descriptor])

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == [[known, unknown]]  # both registered
    assert report.skipped_unknown_type == 1
    assert report.backfilled == 1  # only the known type
    names = {r.name for r in (await db_session.execute(select(FakeEntity))).scalars()}
    assert names == {"Known"}


async def test_not_found_ids_are_not_backfilled(db_session, fake_descriptor):
    """PM reports a registered id as not_found → it is not backfilled."""
    ghost = ULID()

    class NotFoundClient(FakeClient):
        async def add_subscriptions(self, entity_ids):
            ids = list(entity_ids)
            self.added.append(ids)
            return SubscriptionResult(registered=0, already_subscribed=0, not_found=[ghost])

    client = NotFoundClient(discovered=[_disc(ghost)], subscribed=[], entities={})
    reconciler = _reconciler(client, [fake_descriptor])

    report = await reconciler.sync_subscriptions(db_session)

    assert report.not_found == 1
    assert report.backfilled == 0
    assert (await db_session.execute(select(FakeEntity))).first() is None


async def test_backfill_skip_counted_separately_from_backfilled(db_session):
    """An update-only descriptor that declines to mirror an unproduced record counts
    as backfill_skipped, not backfilled — so the log is not inflated (CR round 2)."""

    class UpdateOnlyDescriptor(FakeDescriptor):
        async def upsert_from_pm(self, session, record, existing=None):  # noqa: ARG002
            return None  # never mirror an unproduced record → APPLY_SKIPPED

    pm_id = ULID()
    client = FakeClient(
        discovered=[_disc(pm_id)],
        subscribed=[],
        entities={pm_id: _record(pm_id, "1", "Foreign")},
    )
    reconciler = _reconciler(client, [UpdateOnlyDescriptor()])

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == [[pm_id]]  # still subscribed
    assert report.backfilled == 0
    assert report.backfill_skipped == 1
    assert (await db_session.execute(select(FakeEntity))).first() is None


async def test_discover_called_with_spec(db_session, fake_descriptor):
    """The reconciler passes the deployment's discovery spec straight through."""
    client = FakeClient(discovered=[], subscribed=[])
    reconciler = _reconciler(client, [fake_descriptor])

    await reconciler.sync_subscriptions(db_session)

    assert client.discover_calls == [
        {"root_type": "jurisdiction", "root_id": "usa-wa", "follow": ["lineage", "roles"]}
    ]


async def test_local_cohort_anchored_rows_are_subscribed(db_session, fake_descriptor):
    """#73 Axis 1: with include_local_cohort, OUR anchored rows are discovered from the
    local cache (not a PM subtree walk) and subscribed — so the feed delivers PM's edits
    to rows we produced, without following the subtree into strangers we never mirror.
    An unanchored row (not yet pushed to PM) is not subscribable, so it is skipped."""
    anchored = ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=anchored)
    await _seed(db_session, source_id="2", name="NotYetPushed", anchor=None)
    client = FakeClient(discovered=[], subscribed=[])
    reconciler = _local_reconciler(client, [fake_descriptor])

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == [[anchored]]  # only the anchored row
    assert anchored in client.subscribed
    assert report.newly_subscribed == 1


async def test_local_cohort_skips_deleted_rows(db_session, fake_descriptor):
    """A tombstoned anchored row is never re-subscribed — mirrors the anchored-cohort
    reconcile, which excludes terminally-deleted ids from its re-fetch cohort."""
    live, dead = ULID(), ULID()
    await _seed(db_session, source_id="1", name="Live", anchor=live)
    await _seed(
        db_session,
        source_id="2",
        name="Dead",
        anchor=dead,
        deleted_at=datetime(2050, 1, 1, tzinfo=UTC),
    )
    client = FakeClient(discovered=[], subscribed=[])
    reconciler = _local_reconciler(client, [fake_descriptor])

    await reconciler.sync_subscriptions(db_session)

    assert client.added == [[live]]


async def test_local_cohort_deduped_with_pm_discovery(db_session, fake_descriptor):
    """An id in BOTH the PM discovery result and the local cohort is registered once."""
    shared = ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=shared)
    client = FakeClient(
        discovered=[_disc(shared)],
        subscribed=[],
        entities={shared: _record(shared, "1", "Ours")},
    )
    reconciler = _local_reconciler(client, [fake_descriptor])

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == [[shared]]
    assert report.newly_subscribed == 1


async def test_include_local_cohort_false_keeps_pm_discovery_only(db_session, fake_descriptor):
    """Default flag (False): a local anchored row is NOT auto-subscribed — the portable
    default stays PM-subtree-only, so sibling deployments are unaffected."""
    anchored = ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=anchored)
    client = FakeClient(discovered=[], subscribed=[])
    reconciler = _reconciler(client, [fake_descriptor])  # no flag

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == []
    assert report.newly_subscribed == 0
