"""End-to-end integration test — live WSL + TEST_DATABASE_URL.

Run with ``uv run pytest -m integration``. Excluded from the default tier so
the offline suite stays hermetic.

The test invokes ``python -m usa_wa_adapter_legislature.refresh`` as a
subprocess against ``TEST_DATABASE_URL`` (which must already be at the
current migration head), then asserts via a fresh session that the written
provenance chain — Source → FetchEvent → RawPayload → Citation → canonical
entity — is complete and internally consistent.

Assertions deliberately pin *invariants*, not cardinalities (#195): the
refresh grows a resource at a time, and a fixed row count encodes the shape
the code had on the day it was written rather than the behaviour under test.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_core.testing import assert_test_url_safety, reset_migration_schemas
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.sessions import LegislativeSession
from usa_wa_adapter_legislature.adapter import (
    COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX,
    COMMITTEES_RESOURCE_PREFIX,
    SPONSORS_RESOURCE_PREFIX,
)
from usa_wa_adapter_legislature.meetings.windows import COMMITTEE_MEETINGS_RESOURCE_PREFIX

#: Every archive the daily refresh is expected to pull, by ``resource_id`` prefix
#: (`refresh.py`: committees for the biennium, the sponsors roster, the per-committee
#: members-history fan-out, and the biennium meeting window). Imported rather than
#: spelled out — an assertion must never key on an exact upstream string (AGENTS.md).
EXPECTED_RESOURCE_PREFIXES = (
    COMMITTEES_RESOURCE_PREFIX,
    SPONSORS_RESOURCE_PREFIX,
    COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX,
    COMMITTEE_MEETINGS_RESOURCE_PREFIX,
)

# ``db`` as well as ``integration`` (#185): this test opens its own engine against
# ``TEST_DATABASE_URL`` instead of taking ``db_session``, so the conftest's
# fixture-closure sweep cannot see that it needs a database. Without the marker,
# ``pytest -m 'not db'`` would select it on a machine with none.
pytestmark = [pytest.mark.integration, pytest.mark.db]


async def _seed_jurisdiction(database_url: str) -> None:
    """Ensure the usa-wa Jurisdiction cache row exists (the refresh assumes it).

    Re-asserts the conftest URL safety guard before any destructive DML — this
    test opens its own engine and bypasses the savepointed ``db_session``
    fixture, so the module-level check at conftest import isn't sufficient
    on its own.
    """
    assert_test_url_safety(database_url)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM clearinghouse_core.citations"))
            await conn.execute(text("DELETE FROM canonical.organizations"))
            await conn.execute(text("DELETE FROM canonical.legislative_sessions"))
            await conn.execute(text("DELETE FROM clearinghouse_core.raw_payloads"))
            await conn.execute(text("DELETE FROM clearinghouse_core.fetch_events"))
            await conn.execute(text("DELETE FROM clearinghouse_core.sources"))
        async with AsyncSession(engine) as session:
            jur = (
                await session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
            ).scalar_one_or_none()
            if jur is None:
                jtype = (
                    await session.execute(
                        select(JurisdictionType).where(JurisdictionType.slug == "state")
                    )
                ).scalar_one_or_none()
                if jtype is None:
                    jtype = JurisdictionType(slug="state", display_name="State")
                    session.add(jtype)
                    await session.flush()
                session.add(
                    Jurisdiction(
                        slug="usa-wa",
                        name="Washington State",
                        type_id=jtype.id,
                        recorded_at=datetime.now(UTC),
                    )
                )
                await session.commit()
    finally:
        await engine.dispose()


async def test_refresh_module_writes_full_anchor_chain_to_test_db():
    test_db_url = os.environ.get("TEST_DATABASE_URL")
    assert test_db_url, "TEST_DATABASE_URL must be set"
    assert_test_url_safety(test_db_url)

    # Child env points DATABASE_URL at the test DB and drops DATABASE_URL_OWNER:
    # alembic/env.py prefers the owner DSN over DATABASE_URL, so an inherited
    # owner DSN would silently retarget the live DB. Reused for both subprocesses
    # so neither the migrate nor the refresh run can fall through to prod.
    child_env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL_OWNER"}
    child_env["DATABASE_URL"] = test_db_url
    # Reset to a known-clean state first: drop alembic_version + every declared
    # schema so the upgrade always replays from base. Without this the test
    # inherits whatever schema state a prior integration test left behind — a
    # half-migrated DB (alembic_version cleared but tables present) makes the
    # from-base upgrade collide on existing tables (issue #26).
    await reset_migration_schemas(test_db_url)
    # Run alembic upgrade head against the test DB so the schema matches.
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env=child_env,
        capture_output=True,
    )
    await _seed_jurisdiction(test_db_url)

    result = subprocess.run(
        [sys.executable, "-m", "usa_wa_adapter_legislature.refresh"],
        env={**child_env, "USA_WA_BIENNIUM": "2025-26"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"refresh failed: stdout={result.stdout} stderr={result.stderr}"
    # The summary line is the #179b harness's `key=value` form, not the CLI's old
    # "WSL refresh: …" print. Exit code and counters are unchanged.
    assert "job=wsl-refresh outcome=ok" in result.stdout
    assert "committee_errors=0" in result.stdout

    engine = create_async_engine(test_db_url)
    try:
        async with AsyncSession(engine) as session:
            sources = (await session.execute(select(Source))).scalars().all()
            assert len(sources) == 1
            source = sources[0]

            fetch_events = (await session.execute(select(FetchEvent))).scalars().all()
            raw_payloads = (await session.execute(select(RawPayload))).scalars().all()

            # The invariant is that the anchor chain is COMPLETE and internally
            # consistent — not that it has a fixed cardinality (#195). The old
            # ``== 1`` dated from #22/#26, when the refresh pulled a single WSL
            # resource; every resource added to the daily run since invalidated it.
            assert fetch_events, "the refresh must record at least one FetchEvent"
            assert {e.source_id for e in fetch_events} == {source.id}
            assert {e.status for e in fetch_events} == {FetchStatus.ok}
            # #54: the runner is the single hashing chokepoint, so no event may
            # carry a NULL content_hash.
            assert all(e.content_hash is not None for e in fetch_events)
            for prefix in EXPECTED_RESOURCE_PREFIXES:
                assert any(e.resource_id.startswith(prefix) for e in fetch_events), (
                    f"no FetchEvent for the {prefix!r} archive"
                )

            # One RawPayload per payload-BEARING FetchEvent — not per FetchEvent.
            # #82: the daily run forces past the TTL, and a forced re-pull whose wire
            # is byte-identical to one already archived re-records the FetchEvent
            # (refreshing the TTL and the #55 content-hash ledger) but deliberately
            # does NOT re-store the body — ``AdapterRunner._archive_payload`` skips
            # ``_record_raw_payload`` when ``_payload_already_archived`` finds a stored
            # body under a prior event with the same (source, resource_id, content_hash).
            # So ``len(payloads) == len(events)`` is NOT the invariant. What holds is:
            # exactly one archived body per distinct dedup key.
            assert raw_payloads, "the refresh must archive at least one RawPayload"
            # Keyed by event: one-payload-per-event is a DB constraint
            # (``uq_raw_payloads_fetch_event``), and ``fetch_event_id`` is a NOT NULL FK,
            # so neither "no two payloads share an event" nor "every payload's event is
            # one of these" is assertable here — the schema already forbids the negation.
            # Only the dedup-key relation below is behaviour this test can actually fail on.
            payload_by_event = {p.fetch_event_id: p for p in raw_payloads}
            bodies_per_dedup_key: dict[tuple[str, bytes], int] = {}
            for event in fetch_events:
                key = (event.resource_id, event.content_hash)
                bodies_per_dedup_key.setdefault(key, 0)
                bodies_per_dedup_key[key] += 1 if event.id in payload_by_event else 0
            assert set(bodies_per_dedup_key.values()) == {1}, (
                f"expected exactly one archived body per (resource_id, content_hash): "
                f"{bodies_per_dedup_key}"
            )

            # Each archived body actually hashes to its event's ledger entry. The WSL
            # adapter supplies no ``FetchedPayload.content_hash``, so the runner derives
            # sha256 over the very bytes it stores as ``RawPayload.body``.
            for event in fetch_events:
                payload = payload_by_event.get(event.id)
                if payload is None:
                    continue  # the #82 payload-less re-record; body lives under its twin
                assert event.content_hash == hashlib.sha256(payload.body).digest(), (
                    f"content_hash does not match the archived body for {event.resource_id}"
                )
                assert payload.size_bytes == len(payload.body)

            orgs = (await session.execute(select(Organization))).scalars().all()
            # 1 legislature + 2 chambers + ≥1 committee.
            legislature = [o for o in orgs if o.org_type == "legislature"]
            chambers = [o for o in orgs if o.org_type == "chamber"]
            committees = [o for o in orgs if o.org_type == "committee"]
            assert len(legislature) == 1
            assert len(chambers) == 2
            assert len(committees) >= 1

            # The members-history pull FANS OUT per committee (`refresh.py`), and prefix
            # presence alone would pass a regression that collapsed it to a single call.
            # Bound it instead of counting it: the upper bound is structural — the loop
            # makes at most one fetch per committee it iterates, over a set strictly
            # narrower than this one (live ∧ cited by the current biennium's archive) —
            # and the lower bound just has to be above 1 to prove it fanned. An exact
            # `== len(committees)` would be WRONG: the loop skips committees with no
            # agency mapping and committees with no `short_name`, and reproducing that
            # eligibility predicate here is the same staleness trap in a new costume.
            members_hist = [
                e
                for e in fetch_events
                if e.resource_id.startswith(COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX)
            ]
            assert 1 < len(members_hist) <= len(committees), (
                f"members-history fan-out looks wrong: {len(members_hist)} events "
                f"for {len(committees)} committees"
            )

            sessions = (await session.execute(select(LegislativeSession))).scalars().all()
            biennium = [s for s in sessions if s.classification == "biennium"]
            regulars = [s for s in sessions if s.classification == "regular"]
            assert len(biennium) == 1
            assert len(regulars) == 2

            citations = (await session.execute(select(Citation))).scalars().all()
            # Same stale-cardinality trap as the FetchEvent count above (#195): the old
            # ``== len(committees)`` held only while committees were the sole normalized
            # entity. The sponsors roster, the members fan-out and the meeting window now
            # normalize Persons and further Orgs, each earning its own Citation. The
            # standing invariant is that the chain CLOSES — every citation resolves to an
            # event from this run, and every committee is provenance-anchored.
            # ``Citation.fetch_event_id`` is a NOT NULL FK, so "every citation resolves to
            # an event" is a schema guarantee, not something this test can fail on. What it
            # CAN prove is the other direction — that the entities the refresh created are
            # provenance-anchored.
            assert citations, "the refresh must write at least one Citation"
            cited_org_ids = {c.entity_id for c in citations if c.entity_type == "organization"}
            assert {c.id for c in committees} <= cited_org_ids, (
                "every committee must be anchored by a Citation"
            )
    finally:
        await engine.dispose()
