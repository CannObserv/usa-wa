"""Operator-event store: provenance write + dedup + supersede + read (#107)."""

import hashlib
from datetime import date

from sqlalchemy import func, select

from clearinghouse_core.provenance import FetchEvent, RawPayload, Source
from clearinghouse_domain_legislative.operator_events import KIND_DEPARTED, KIND_SEATED
from usa_wa_adapter_legislature.operator_events_store import (
    citation_target_for_event,
    current_events,
    get_or_create_operator_source,
    record_operator_event,
    supersede_event,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction


async def _source(session) -> Source:
    juris = await resolve_jurisdiction(session)
    return await get_or_create_operator_source(session, juris)


async def test_record_writes_hashed_provenance(db_session, usa_wa):
    source = await _source(db_session)
    event = await record_operator_event(
        db_session,
        source,
        member_id="29091",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://example.gov/ramos",
        entered_by="greg",
    )

    fe = (
        await db_session.execute(
            select(FetchEvent).where(FetchEvent.resource_id == event.source_id)
        )
    ).scalar_one()
    payload = (
        await db_session.execute(select(RawPayload).where(RawPayload.fetch_event_id == fe.id))
    ).scalar_one()
    assert fe.content_hash == hashlib.sha256(payload.body).digest()
    assert fe.source_id == source.id
    assert event.member_id == "29091"


async def test_record_is_idempotent_no_duplicate_provenance(db_session, usa_wa):
    source = await _source(db_session)
    kwargs = dict(
        member_id="29091",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://example.gov/ramos",
    )
    first = await record_operator_event(db_session, source, **kwargs)
    second = await record_operator_event(db_session, source, **kwargs)

    assert first.id == second.id
    fe_count = (
        await db_session.execute(
            select(func.count())
            .select_from(FetchEvent)
            .where(FetchEvent.resource_id == first.source_id)
        )
    ).scalar_one()
    assert fe_count == 1


async def test_supersede_stamps_prior_and_current_excludes_it(db_session, usa_wa):
    source = await _source(db_session)
    prior = await record_operator_event(
        db_session,
        source,
        member_id="29091",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://example.gov/ramos",
    )
    corrected = await supersede_event(
        db_session,
        source,
        prior,
        reason="died",
        effective_date=date(2025, 4, 20),
        evidence_url="https://example.gov/ramos-official",
    )

    assert prior.superseded_by_id == corrected.id
    current = await current_events(db_session, member_ids=["29091"])
    assert [e.id for e in current] == [corrected.id]


async def test_citation_target_resolves(db_session, usa_wa):
    source = await _source(db_session)
    event = await record_operator_event(
        db_session,
        source,
        member_id="35410",
        kind=KIND_SEATED,
        reason="appointed",
        effective_date=date(2025, 6, 3),
        evidence_url="https://example.gov/hunt",
        seat_kind="chamber-senate",
        seat_discriminator="5",
    )
    target = await citation_target_for_event(db_session, event)
    assert target is not None
    fetch_event_id, fetched_at, resource_id = target
    assert resource_id == event.source_id
