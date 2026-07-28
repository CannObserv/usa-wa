"""Committee-succession store: provenance write + dedup + supersede + read (usa-wa#124 C2)."""

import hashlib

from sqlalchemy import func, select

from clearinghouse_core.provenance import FetchEvent, RawPayload, Source
from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from usa_wa_adapter_legislature.committee_succession_store import (
    current_events,
    get_or_create_operator_source,
    record_succession_event,
    succession_source_id,
    supersede_event,
    superseded_events,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction


async def _source(session) -> Source:
    return await get_or_create_operator_source(session, await resolve_jurisdiction(session))


def test_source_id_deterministic_with_and_without_year():
    assert succession_source_id("14294", "28244", "succeeded_by", 2021) == (
        "succeeded_by:14294:28244:2021"
    )
    assert succession_source_id("14294", "28244", "succeeded_by", None) == (
        "succeeded_by:14294:28244"
    )


async def test_record_writes_hashed_provenance_and_projection(db_session, usa_wa):
    source = await _source(db_session)
    event = await record_succession_event(
        db_session,
        source,
        subject_source_id="14294",
        linked_source_id="28244",
        slug="succeeded_by",
        effective_year=2021,
        evidence_url="https://example.gov/lc",
        notes="renamed",
        entered_by="greg",
    )
    assert event.source_id == "succeeded_by:14294:28244:2021"

    fe = (
        await db_session.execute(
            select(FetchEvent).where(FetchEvent.resource_id == event.source_id)
        )
    ).scalar_one()
    payload = (
        await db_session.execute(select(RawPayload).where(RawPayload.fetch_event_id == fe.id))
    ).scalar_one()
    assert fe.content_hash == hashlib.sha256(payload.body).digest()


async def test_record_is_idempotent_on_natural_key(db_session, usa_wa):
    source = await _source(db_session)
    for _ in range(2):
        await record_succession_event(
            db_session,
            source,
            subject_source_id="14294",
            linked_source_id="28244",
            slug="succeeded_by",
            effective_year=2021,
            evidence_url="https://example.gov/lc",
        )
    n_events = (
        await db_session.execute(select(func.count()).select_from(CommitteeSuccessionEvent))
    ).scalar_one()
    n_fetch = (await db_session.execute(select(func.count()).select_from(FetchEvent))).scalar_one()
    assert n_events == 1
    assert n_fetch == 1  # byte-identical re-ingest appends no fresh provenance


async def test_supersede_relink_stamps_prior_and_appends_new(db_session, usa_wa):
    """A re-link correction (wrong successor) is a distinct natural key: the prior link is
    superseded and a new row created (the create-new + retract-old shape, power-map#322)."""
    source = await _source(db_session)
    prior = await record_succession_event(
        db_session,
        source,
        subject_source_id="14294",
        linked_source_id="99999",  # wrong successor
        slug="succeeded_by",
        effective_year=2021,
        evidence_url="https://example.gov/lc",
    )
    corrected = await supersede_event(
        db_session,
        source,
        prior,
        linked_source_id="28244",  # the real successor
        evidence_url="https://example.gov/lc-fixed",
    )
    assert corrected.id != prior.id
    assert prior.superseded_by_id == corrected.id
    assert corrected.linked_source_id == "28244"
    # Only the corrected link is "current".
    current = await current_events(db_session)
    assert [e.id for e in current] == [corrected.id]
    # The prior (re-linked) row is the producer's retract candidate (#127).
    superseded = await superseded_events(db_session)
    assert [e.id for e in superseded] == [prior.id]


async def test_supersede_can_clear_year(db_session, usa_wa):
    """Passing ``effective_year=None`` explicitly CLEARS the year (a distinct key), vs
    omitting it (inherit prior's) — the sentinel distinguishes the two."""
    source = await _source(db_session)
    prior = await record_succession_event(
        db_session,
        source,
        subject_source_id="14294",
        linked_source_id="28244",
        slug="succeeded_by",
        effective_year=2021,
        evidence_url="https://example.gov/lc",
    )
    corrected = await supersede_event(
        db_session, source, prior, effective_year=None, evidence_url="https://example.gov/lc-fixed"
    )
    assert corrected.id != prior.id
    assert corrected.effective_year is None
    assert prior.superseded_by_id == corrected.id


async def test_current_events_excludes_non_operator_source(db_session, usa_wa):
    """The producer's input set is operator attestations only — a stray row under a
    different source must not leak in."""
    source = await _source(db_session)
    await record_succession_event(
        db_session,
        source,
        subject_source_id="14294",
        linked_source_id="28244",
        slug="succeeded_by",
        effective_year=2021,
        evidence_url="https://example.gov/lc",
    )
    db_session.add(
        CommitteeSuccessionEvent(
            source="some_other_source",
            source_id="succeeded_by:1:2",
            subject_source_id="1",
            linked_source_id="2",
            slug="succeeded_by",
            evidence_url="https://example.gov/other",
        )
    )
    await db_session.flush()
    current = await current_events(db_session)
    assert [e.source for e in current] == ["usa_wa_operator"]


async def test_supersede_same_key_is_plain_update_not_self_superseded(db_session, usa_wa):
    """A correction that changes only evidence/notes resolves to the prior row (same key) —
    an idempotent update, never self-superseded."""
    source = await _source(db_session)
    prior = await record_succession_event(
        db_session,
        source,
        subject_source_id="14294",
        linked_source_id="28244",
        slug="succeeded_by",
        effective_year=2021,
        evidence_url="https://example.gov/lc",
    )
    same = await supersede_event(
        db_session, source, prior, evidence_url="https://example.gov/lc-better"
    )
    assert same.id == prior.id
    assert prior.superseded_by_id is None
    assert same.evidence_url == "https://example.gov/lc-better"
