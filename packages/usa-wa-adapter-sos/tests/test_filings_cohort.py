"""Archive-first SOS filing-cohort provider (#100/#101) — offline re-parse + memoized scans."""

from __future__ import annotations

from datetime import UTC, datetime

from usa_wa_adapter_pdc.normalize.positions import fold_token
from usa_wa_adapter_sos.filings.cohort import SosFilingCohortProvider
from usa_wa_adapter_sos.positions import position_for

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source


async def _sos_source(session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="SOS", slug="usa_wa_sos", kind="rest")
    session.add(row)
    await session.flush()
    return row


async def _archive(session, source, resource_id, body, *, fetched_at=None, with_payload=True):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=fetched_at or datetime.now(UTC),
        http_status=200,
        content_hash=bytes(32),
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    if with_payload:
        session.add(
            RawPayload(
                fetch_event_id=ev.id, content_type="text/csv", body=body, size_bytes=len(body)
            )
        )
        await session.flush()
    return ev


def _csv(*rows):
    header = "RaceName,RaceJurisdictionName,BallotName,PartyName\r\n"
    body = "".join(
        f"{race},Legislative District {ld},{ballot},{party}\r\n" for race, ld, ballot, party in rows
    )
    return (header + body).encode()


async def test_house_filings_resolves_position_from_archive(db_session, usa_wa):
    """The offline re-parse yields a ``{year: {LD: [HouseFiling]}}`` map the position lookup
    resolves against; a year with no archived cohort is absent."""
    source = await _sos_source(db_session, usa_wa)
    await _archive(
        db_session,
        source,
        "sos-whofiled:201611",
        _csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Republican Party)")),
    )

    provider = SosFilingCohortProvider(session=db_session, source_id=source.id)
    filings = await provider.house_filings()

    assert position_for(filings[2016], 5, fold_token("Rivers"), "republican") == "Position 1"
    assert 2012 not in filings  # no archived cohort that year


async def test_latest_payload_bearing_event_wins(db_session, usa_wa):
    """A forced re-pull re-records a payload-less FetchEvent; the provider must read the older
    payload-bearing event, not the newer empty one (the #82 lesson)."""
    source = await _sos_source(db_session, usa_wa)
    old = datetime(2020, 1, 1, tzinfo=UTC)
    new = datetime(2021, 1, 1, tzinfo=UTC)
    await _archive(
        db_session,
        source,
        "sos-whofiled:201611",
        _csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Republican Party)")),
        fetched_at=old,
    )
    # newer event, no payload (byte-identical re-pull skipped the RawPayload)
    await _archive(
        db_session, source, "sos-whofiled:201611", b"", fetched_at=new, with_payload=False
    )

    provider = SosFilingCohortProvider(session=db_session, source_id=source.id)
    filings = await provider.house_filings()
    # the older payload-bearing wire was parsed, not the newer empty one
    assert position_for(filings[2016], 5, fold_token("Rivers"), "republican") == "Position 1"


async def test_house_filings_is_memoized(db_session, usa_wa):
    """The archive scan runs once — a second ``house_filings()`` returns the cached object
    without re-querying (the memoization early-return, #100 CR finding 6)."""
    source = await _sos_source(db_session, usa_wa)
    await _archive(
        db_session,
        source,
        "sos-whofiled:201611",
        _csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Republican Party)")),
    )
    provider = SosFilingCohortProvider(session=db_session, source_id=source.id)
    first = await provider.house_filings()
    second = await provider.house_filings()
    assert first is second  # same object — the second call short-circuits on the memo


async def test_citation_events_is_memoized(db_session, usa_wa):
    """``citation_events()`` caches too (#101 CR round 2, finding 9) — the builder calls it
    directly *and* via ``house_filings``, so the scan must run once."""
    source = await _sos_source(db_session, usa_wa)
    await _archive(
        db_session,
        source,
        "sos-whofiled:201611",
        _csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Republican Party)")),
    )
    provider = SosFilingCohortProvider(session=db_session, source_id=source.id)
    first = await provider.citation_events()
    second = await provider.citation_events()
    assert first is second  # same object — the second call short-circuits on the memo
