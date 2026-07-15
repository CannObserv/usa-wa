"""SubscriptionReconciler tests — additive discovery → register → backfill.

Uses the shipped FakeClient/FakeDescriptor. The reconciler is the membership engine
behind the subscription-filtered feed (PM #203): it must register only the entities
not already subscribed and backfill their current state by id, additively.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from ulid import ULID

from clearinghouse_sync_powermap.client import (
    DiscoveredEntity,
    RetryableClientError,
    SubscriptionResult,
)
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


async def test_backfill_skips_entity_already_present_locally(db_session, fake_descriptor):
    """usa-wa#89: an entity that surfaces as ``new`` (missing from list_subscriptions)
    but which we ALREADY hold locally is registered idempotently but NOT re-fetched.

    This kills the phantom-new backfill crawl: PM's ``/subscriptions`` pagination can
    under-report the registered set (power-map#297), so already-subscribed rows
    resurface as ``new`` every cycle. Re-fetching each was the burst that tripped PM's
    429. A row we already anchored is current via the feed + reconcile backstop, so the
    backfill (which only exists to seed a newly-subscribed entity the forward-only feed
    won't retroactively deliver) is skipped."""
    pm_id = ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=pm_id)
    client = FakeClient(
        discovered=[_disc(pm_id)],
        subscribed=[],  # under-reported: pm_id is really subscribed but omitted here
        entities={pm_id: _record(pm_id, "1", "FromBackfill")},
    )
    reconciler = _reconciler(client, [fake_descriptor])

    report = await reconciler.sync_subscriptions(db_session)

    assert client.added == [[pm_id]]  # still (idempotently) registered
    assert client.fetched == []  # but NOT re-fetched — the phantom crawl is gone
    assert report.already_cached == 1
    assert report.backfilled == 0
    row = (await db_session.execute(select(FakeEntity))).scalar_one()
    assert row.name == "Ours"  # untouched by a backfill


async def test_backfill_retries_transient_read(db_session, fake_descriptor):
    """usa-wa#89: a 429 during a genuine backfill fetch pauses + resumes rather than
    aborting the backstop before it stamps (which re-crawls next cycle and re-trips the
    limiter). Mirrors the anchored-cohort crawl's #85 pause-and-resume."""
    pm_id = ULID()

    class FlakyClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._left = 1

        async def get_entity(self, read_path, pm_id):
            if self._left > 0:
                self._left -= 1
                raise RetryableClientError("PM 429", retry_after=2.0)
            return await super().get_entity(read_path, pm_id)

    client = FlakyClient(
        discovered=[_disc(pm_id)],
        subscribed=[],
        entities={pm_id: _record(pm_id, "1", "FromBackfill")},
    )
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    engine = SyncEngine([fake_descriptor], client, sleep=sleep)
    reconciler = SubscriptionReconciler(client, engine, SPEC)

    report = await reconciler.sync_subscriptions(db_session)

    assert report.backfilled == 1  # resumed and applied, not aborted
    assert sleeps == [2.0]  # honored the Retry-After hint
    row = (await db_session.execute(select(FakeEntity))).scalar_one()
    assert row.name == "FromBackfill"


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


async def test_prune_removes_subscriptions_outside_the_mirror_set(db_session, fake_descriptor):
    """#73 Axis 1 step 6: a subscription not in the desired mirror set (a stranger left
    over from the old broad subtree walk) is unsubscribed; our anchored row is kept."""
    ours, stranger = ULID(), ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=ours)
    client = FakeClient(discovered=[], subscribed=[ours, stranger])
    reconciler = _local_reconciler(client, [fake_descriptor])

    summary = await reconciler.prune_subscriptions(db_session)

    assert client.removed == [[stranger]]
    assert client.subscribed == [ours]
    assert summary["removed"] == 1
    assert summary["stale"] == 1
    assert summary["aborted"] is None


async def test_prune_aborts_when_desired_set_is_empty(db_session, fake_descriptor):
    """A discovery collapse (empty desired set) must not mass-unsubscribe everything —
    an empty mirror set aborts, removing nothing."""
    a, b = ULID(), ULID()
    client = FakeClient(discovered=[], subscribed=[a, b])  # no PM discovery, no local rows
    reconciler = _local_reconciler(client, [fake_descriptor])

    summary = await reconciler.prune_subscriptions(db_session)

    assert summary["aborted"] == "empty_desired"
    assert client.removed == []
    assert client.subscribed == [a, b]


async def test_prune_floor_aborts_on_excessive_fraction(db_session, fake_descriptor):
    """The prune floor guards against a partial discovery: if the stale fraction exceeds
    ``max_prune_fraction`` the run aborts (a legitimate large cleanup raises the floor)."""
    ours, s1, s2, s3 = ULID(), ULID(), ULID(), ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=ours)
    client = FakeClient(discovered=[], subscribed=[ours, s1, s2, s3])
    reconciler = _local_reconciler(client, [fake_descriptor])

    # 3 of 4 stale = 0.75 > 0.5 floor → abort.
    summary = await reconciler.prune_subscriptions(db_session, max_prune_fraction=0.5)

    assert summary["aborted"] == "prune_floor"
    assert client.removed == []


async def test_prune_dry_run_removes_nothing(db_session, fake_descriptor):
    """--dry-run computes the diff + guards but unsubscribes nothing."""
    ours, stranger = ULID(), ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=ours)
    client = FakeClient(discovered=[], subscribed=[ours, stranger])
    reconciler = _local_reconciler(client, [fake_descriptor])

    summary = await reconciler.prune_subscriptions(db_session, dry_run=True)

    assert summary["stale"] == 1
    assert summary["removed"] == 0
    assert client.removed == []
    assert client.subscribed == [ours, stranger]


async def test_prune_partial_removal_reports_actual_count(db_session, fake_descriptor):
    """A bulk remove that drops fewer than requested (partial PM-side failure) reports the
    real removed count, not len(stale) — a re-run retries the remainder."""
    ours, s1, s2 = ULID(), ULID(), ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=ours)

    class PartialRemoveClient(FakeClient):
        async def remove_subscriptions(self, entity_ids):
            ids = list(entity_ids)
            self.removed.append(ids)
            return 1  # claims only one of the stale ids was actually removed

    client = PartialRemoveClient(discovered=[], subscribed=[ours, s1, s2])
    reconciler = _local_reconciler(client, [fake_descriptor])

    summary = await reconciler.prune_subscriptions(db_session)

    assert summary["stale"] == 2
    assert summary["removed"] == 1  # reports the actual count, not the request size


async def test_prune_is_noop_when_already_aligned(db_session, fake_descriptor):
    """Registered == desired: nothing stale, nothing removed (idempotent second run)."""
    ours = ULID()
    await _seed(db_session, source_id="1", name="Ours", anchor=ours)
    client = FakeClient(discovered=[], subscribed=[ours])
    reconciler = _local_reconciler(client, [fake_descriptor])

    summary = await reconciler.prune_subscriptions(db_session)

    assert summary["stale"] == 0
    assert summary["removed"] == 0
    assert client.removed == []
