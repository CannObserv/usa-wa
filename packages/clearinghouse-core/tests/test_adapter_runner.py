"""End-to-end tests for the :class:`AdapterRunner` contract.

Uses an in-memory :class:`BaseAdapter` subclass and a test-only canonical entity
to exercise the runner without depending on any real source or domain package.

Covers:

- cache-hit short-circuit (within TTL, fresh fetch skipped)
- cache-miss refetch (TTL elapsed, refetch happens)
- force=True override
- idempotent upsert on ``(jurisdiction_id, source, source_id)``
- provenance rows (FetchEvent, RawPayload, Citation) written on success
- discover + refresh aggregate summary
"""

import hashlib
from collections.abc import AsyncIterable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID

from clearinghouse_core.adapter import (
    BaseAdapter,
    FetchedPayload,
    NormalizedBatch,
    ResourceRef,
)
from clearinghouse_core.db.ulid import ULID as ULIDColumn
from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.models import Base, TimestampMixin
from clearinghouse_core.provenance import (
    SCHEMA,
    Citation,
    FetchEvent,
    FetchStatus,
    RawPayload,
    Source,
)
from clearinghouse_core.runner import AdapterRunner


class FakeWidget(Base, TimestampMixin):
    """Test-only canonical entity. Exists solely for runner tests."""

    __tablename__ = "fake_widgets"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_fake_widgets_natural_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[ULID] = mapped_column(ULIDColumn(), primary_key=True)
    jurisdiction_id: Mapped[ULID] = mapped_column(
        ULIDColumn(), ForeignKey(f"{SCHEMA}.jurisdictions.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)


class FakeAdapter(BaseAdapter):
    """In-memory adapter for tests.

    Tracks invocation counts so tests can verify cache-hit short-circuits and
    refetch behavior.
    """

    source_slug = "fake_source"
    schema_name = "clearinghouse_core"  # piggyback on the existing test schema
    jurisdiction_slug = "usa-wa"

    def __init__(
        self,
        jurisdiction_id: ULID,
        *,
        body: bytes = b"<widget id='X'/>",
        content_hash: bytes | None = None,
    ) -> None:
        self.jurisdiction_id = jurisdiction_id
        self.body = body
        self.content_hash = content_hash
        self.fetch_calls = 0
        self.discover_calls = 0
        self.normalize_calls = 0
        self._refs: list[ResourceRef] = []

    def queue_refs(self, refs: list[ResourceRef]) -> None:
        self._refs = refs

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        self.fetch_calls += 1
        return FetchedPayload(
            url=f"https://example.test/widgets/{resource_id}",
            fetched_at=datetime.now(UTC),
            content_type="application/xml",
            body=self.body,
            http_status=200,
            content_hash=self.content_hash,
        )

    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        self.discover_calls += 1
        for r in self._refs:
            yield r

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        self.normalize_calls += 1
        # In the runner contract, normalize emits canonical-entity instances.
        # Tests pre-set this via a class attribute so we can vary it per test.
        entities = list(self._next_entities)
        self._next_entities = []
        return NormalizedBatch(entities=entities)

    _next_entities: list[Base] = []


@pytest.fixture
async def setup(db_session):
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()

    jurisdiction = Jurisdiction(
        slug="usa-wa",
        name="WA",
        type_id=state_type.id,
        recorded_at=datetime.now(UTC),
    )
    db_session.add(jurisdiction)
    await db_session.flush()

    source = Source(
        jurisdiction_id=jurisdiction.id,
        name="Fake Source",
        slug="fake_source",
        kind="http",
        reliability=0.9,
        cache_ttl_days=1,
    )
    db_session.add(source)
    await db_session.flush()

    adapter = FakeAdapter(jurisdiction_id=jurisdiction.id)
    runner = AdapterRunner(adapter, db_session, source=source, jurisdiction=jurisdiction)
    return {"jurisdiction": jurisdiction, "source": source, "adapter": adapter, "runner": runner}


def _widget(jurisdiction_id: ULID, source_id: str, label: str) -> FakeWidget:
    return FakeWidget(
        id=ULID(),
        jurisdiction_id=jurisdiction_id,
        source="fake_source",
        source_id=source_id,
        label=label,
    )


async def test_fetch_and_normalize_writes_provenance_chain(db_session, setup):
    """A successful fetch + normalize writes FetchEvent + RawPayload + Citation."""
    adapter = setup["adapter"]
    runner = setup["runner"]
    jur_id = setup["jurisdiction"].id

    adapter._next_entities = [_widget(jur_id, "W-1", "first")]
    n = await runner.fetch_and_normalize("W-1")
    assert n == 1
    assert adapter.fetch_calls == 1
    assert adapter.normalize_calls == 1

    events = (await db_session.execute(select(FetchEvent))).scalars().all()
    payloads = (await db_session.execute(select(RawPayload))).scalars().all()
    widgets = (await db_session.execute(select(FakeWidget))).scalars().all()
    citations = (await db_session.execute(select(Citation))).scalars().all()

    assert len(events) == 1
    assert events[0].resource_id == "W-1"
    assert len(payloads) == 1
    assert payloads[0].fetch_event_id == events[0].id
    assert len(widgets) == 1
    assert widgets[0].source_id == "W-1"
    assert len(citations) == 1
    assert citations[0].entity_type == "fakewidget"
    assert citations[0].confidence == pytest.approx(0.9)
    # Integrity baseline (#54): every fetch carries sha256(body), never NULL.
    assert events[0].content_hash == hashlib.sha256(adapter.body).digest()


async def test_archive_payload_writes_provenance_only(db_session, setup):
    """The archive-only seam (#62) writes one FetchEvent + one RawPayload and
    NO canonical rows — no normalize, no upsert, no citation."""
    adapter = setup["adapter"]
    runner = setup["runner"]

    payload = await adapter.fetch_one("W-1")
    event = await runner._archive_payload("W-1", payload)

    assert isinstance(event, FetchEvent)
    assert event.resource_id == "W-1"
    # Provenance written...
    events = (await db_session.execute(select(FetchEvent))).scalars().all()
    payloads = (await db_session.execute(select(RawPayload))).scalars().all()
    assert len(events) == 1
    assert len(payloads) == 1
    assert payloads[0].fetch_event_id == event.id
    # Integrity baseline (#54) centralized: sha256(body), never NULL.
    assert event.content_hash == hashlib.sha256(adapter.body).digest()
    # ...but NO canonical rows or citations, and normalize never ran.
    assert (await db_session.execute(select(FakeWidget))).scalars().all() == []
    assert (await db_session.execute(select(Citation))).scalars().all() == []
    assert adapter.normalize_calls == 0


async def test_archive_payload_dedups_identical_bytes(db_session, setup):
    """Re-archiving identical bytes records a 2nd FetchEvent (cache TTL + hash
    ledger) but does NOT re-store the identical RawPayload — the dedup guard
    survives the extraction (#62)."""
    adapter = setup["adapter"]
    runner = setup["runner"]

    payload = await adapter.fetch_one("W-1")
    await runner._archive_payload("W-1", payload)
    payload2 = await adapter.fetch_one("W-1")  # identical body
    await runner._archive_payload("W-1", payload2)

    events = (
        (await db_session.execute(select(FetchEvent).where(FetchEvent.resource_id == "W-1")))
        .scalars()
        .all()
    )
    payloads = (await db_session.execute(select(RawPayload))).scalars().all()
    assert len(events) == 2  # both archives recorded
    assert len(payloads) == 1  # bytes stored once


async def test_archive_payload_records_status(db_session, setup):
    """archive_payload defaults to ok but accepts a status (archive the wire of a
    fetch whose normalization failed, without asserting the entity) (#62)."""
    adapter = setup["adapter"]
    runner = setup["runner"]

    payload = await adapter.fetch_one("W-1")
    event = await runner._archive_payload("W-1", payload, status=FetchStatus.err)

    assert event.status == FetchStatus.err


async def test_content_hash_derived_from_body_when_adapter_omits(db_session, setup):
    """The runner derives content_hash = sha256(body) when the adapter leaves it None.

    Single chokepoint so no adapter can skip the integrity baseline (#54).
    """
    adapter = setup["adapter"]
    runner = setup["runner"]
    jur_id = setup["jurisdiction"].id
    assert adapter.content_hash is None  # adapter supplies no hash

    adapter._next_entities = [_widget(jur_id, "W-1", "first")]
    await runner.fetch_and_normalize("W-1")

    event = (await db_session.execute(select(FetchEvent))).scalar_one()
    assert event.content_hash == hashlib.sha256(adapter.body).digest()
    assert event.content_hash is not None


async def test_adapter_supplied_content_hash_is_preserved(db_session):
    """An adapter-supplied content_hash wins over the derived fallback.

    Lets an adapter that streamed its own digest (or hashes a wire form distinct
    from the stored body) keep authority over the baseline.
    """
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()
    jurisdiction = Jurisdiction(
        slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add(jurisdiction)
    await db_session.flush()
    source = Source(
        jurisdiction_id=jurisdiction.id,
        name="Fake Source",
        slug="fake_source",
        kind="http",
        reliability=0.9,
        cache_ttl_days=1,
    )
    db_session.add(source)
    await db_session.flush()

    supplied = bytes(range(32))  # a deliberately-not-sha256(body) digest
    adapter = FakeAdapter(jurisdiction_id=jurisdiction.id, content_hash=supplied)
    runner = AdapterRunner(adapter, db_session, source=source, jurisdiction=jurisdiction)
    adapter._next_entities = [_widget(jurisdiction.id, "W-1", "first")]
    await runner.fetch_and_normalize("W-1")

    event = (await db_session.execute(select(FetchEvent))).scalar_one()
    assert event.content_hash == supplied
    assert event.content_hash != hashlib.sha256(adapter.body).digest()


async def test_identical_content_hash_skips_duplicate_raw_payload(db_session, setup):
    """Re-fetching unchanged bytes records a new FetchEvent (cache TTL + hash ledger)
    but does NOT re-archive the identical RawPayload — bounds archival growth (#39)."""
    adapter = setup["adapter"]
    runner = setup["runner"]
    jur_id = setup["jurisdiction"].id

    adapter._next_entities = [_widget(jur_id, "W-1", "v1")]
    await runner.fetch_and_normalize("W-1")
    adapter._next_entities = [_widget(jur_id, "W-1", "v1")]
    await runner.fetch_and_normalize("W-1", force=True)  # identical body

    events = (
        (await db_session.execute(select(FetchEvent).where(FetchEvent.resource_id == "W-1")))
        .scalars()
        .all()
    )
    payloads = (await db_session.execute(select(RawPayload))).scalars().all()
    assert len(events) == 2  # both fetches recorded
    assert len(payloads) == 1  # bytes archived once


async def test_changed_content_archives_a_new_raw_payload(db_session, setup):
    """Changed bytes (a new content_hash) DO archive a fresh RawPayload."""
    adapter = setup["adapter"]
    runner = setup["runner"]
    jur_id = setup["jurisdiction"].id

    adapter._next_entities = [_widget(jur_id, "W-1", "v1")]
    await runner.fetch_and_normalize("W-1")
    adapter.body = b"<widget id='X' rev='2'/>"  # different wire
    adapter._next_entities = [_widget(jur_id, "W-1", "v2")]
    await runner.fetch_and_normalize("W-1", force=True)

    payloads = (await db_session.execute(select(RawPayload))).scalars().all()
    assert len(payloads) == 2


async def test_cache_hit_short_circuits_within_ttl(db_session, setup):
    """A second call for the same resource within TTL skips the fetch."""
    adapter = setup["adapter"]
    runner = setup["runner"]
    jur_id = setup["jurisdiction"].id

    adapter._next_entities = [_widget(jur_id, "W-1", "v1")]
    await runner.fetch_and_normalize("W-1")
    assert adapter.fetch_calls == 1

    # Second call: cache hit, no fetch
    result = await runner.fetch_and_normalize("W-1")
    assert result == 0
    assert adapter.fetch_calls == 1
    assert adapter.normalize_calls == 1


async def test_force_overrides_cache(db_session, setup):
    """``force=True`` skips the cache lookup and refetches."""
    adapter = setup["adapter"]
    runner = setup["runner"]
    jur_id = setup["jurisdiction"].id

    adapter._next_entities = [_widget(jur_id, "W-1", "v1")]
    await runner.fetch_and_normalize("W-1")
    adapter._next_entities = [_widget(jur_id, "W-1", "v2")]
    n = await runner.fetch_and_normalize("W-1", force=True)
    assert n == 1
    assert adapter.fetch_calls == 2


async def test_idempotent_upsert_on_natural_key(db_session, setup):
    """Two normalizations of the same (jurisdiction, source, source_id) update, not duplicate."""
    adapter = setup["adapter"]
    runner = setup["runner"]
    jur_id = setup["jurisdiction"].id

    adapter._next_entities = [_widget(jur_id, "W-1", "first label")]
    await runner.fetch_and_normalize("W-1")
    adapter._next_entities = [_widget(jur_id, "W-1", "second label")]
    await runner.fetch_and_normalize("W-1", force=True)

    widgets = (await db_session.execute(select(FakeWidget))).scalars().all()
    assert len(widgets) == 1
    assert widgets[0].label == "second label"


async def test_cache_miss_after_ttl_expires(db_session, setup):
    """When the cached fetch is older than TTL, a refetch happens automatically."""
    adapter = setup["adapter"]
    runner = setup["runner"]
    source = setup["source"]
    jur_id = setup["jurisdiction"].id

    adapter._next_entities = [_widget(jur_id, "W-1", "v1")]
    await runner.fetch_and_normalize("W-1")

    # Backdate the fetch beyond TTL so the next call misses cache
    stale_when = datetime.now(UTC) - timedelta(days=source.cache_ttl_days + 1)
    events = (await db_session.execute(select(FetchEvent))).scalars().all()
    for e in events:
        e.fetched_at = stale_when
    await db_session.flush()

    adapter._next_entities = [_widget(jur_id, "W-1", "v2")]
    await runner.fetch_and_normalize("W-1")
    assert adapter.fetch_calls == 2


async def test_refresh_iterates_discover(db_session, setup):
    """``refresh`` iterates ``adapter.discover`` and aggregates per-ref outcomes."""
    adapter = setup["adapter"]
    runner = setup["runner"]
    jur_id = setup["jurisdiction"].id

    adapter.queue_refs([ResourceRef("W-1"), ResourceRef("W-2"), ResourceRef("W-3")])
    adapter._next_entities = [_widget(jur_id, "W-1", "1")]
    # AdapterRunner pulls next-entities once per fetch; queue per-call by
    # re-assigning _next_entities inside fetch_one via a wrapper would be cleaner
    # in practice, but for this test we use a side-channel patch:
    orig_normalize = adapter.normalize

    async def patched_normalize(payload):
        return NormalizedBatch(entities=[_widget(jur_id, payload.url.rsplit("/", 1)[-1], "x")])

    adapter.normalize = patched_normalize  # type: ignore[method-assign]
    try:
        summary = await runner.refresh(since=None)
    finally:
        adapter.normalize = orig_normalize  # type: ignore[method-assign]

    assert summary.discovered == 3
    assert summary.fetched == 3
    assert summary.upserted_entities == 3
    assert summary.skipped_cache_hit == 0
    assert adapter.discover_calls == 1
