"""Archive-first results-cohort provider (#101) — offline re-parse + memoized scans."""

from __future__ import annotations

from datetime import UTC, datetime

from usa_wa_adapter_pdc.normalize.positions import fold_token
from usa_wa_adapter_sos.positions import position_for
from usa_wa_adapter_sos.results.cohort import SosResultsCohortProvider

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source


async def _results_source(session, usa_wa):
    row = Source(
        jurisdiction_id=usa_wa.id, name="SOS Results", slug="usa_wa_sos_results", kind="rest"
    )
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


def _csv(*races):
    header = '"Race","Candidate","Party"\r\n'
    body = "".join(f'"{race}","{cand}","{party}"\r\n' for race, cand, party in races)
    return (header + body).encode()


_RIVERS = (
    "LEGISLATIVE DISTRICT 5 - State Representative Pos. 1",
    "Ann Rivers",
    "(Prefers Republican Party)",
)


async def test_house_positions_resolves_from_archive(db_session, usa_wa):
    source = await _results_source(db_session, usa_wa)
    await _archive(db_session, source, "sos-legresults:20161108", _csv(_RIVERS))

    provider = SosResultsCohortProvider(session=db_session, source_id=source.id)
    positions = await provider.house_positions()

    assert position_for(positions[2016], 5, fold_token("Rivers"), "republican") == "Position 1"
    assert 2012 not in positions  # no archived cohort that year


async def test_latest_payload_bearing_event_wins(db_session, usa_wa):
    """A forced re-pull re-records a payload-less FetchEvent; the provider reads the older
    payload-bearing event, not the newer empty one (the #82 lesson)."""
    source = await _results_source(db_session, usa_wa)
    old, new = datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)
    await _archive(db_session, source, "sos-legresults:20161108", _csv(_RIVERS), fetched_at=old)
    await _archive(
        db_session, source, "sos-legresults:20161108", b"", fetched_at=new, with_payload=False
    )

    provider = SosResultsCohortProvider(session=db_session, source_id=source.id)
    positions = await provider.house_positions()
    assert position_for(positions[2016], 5, fold_token("Rivers"), "republican") == "Position 1"


async def test_scans_are_memoized(db_session, usa_wa):
    source = await _results_source(db_session, usa_wa)
    await _archive(db_session, source, "sos-legresults:20161108", _csv(_RIVERS))
    provider = SosResultsCohortProvider(session=db_session, source_id=source.id)

    assert await provider.house_positions() is await provider.house_positions()
    assert await provider.citation_events() is await provider.citation_events()
