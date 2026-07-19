"""SOS refresh cycle (#101) — archive the current filing cohort + re-drive the House builder.

The daily driver of the WSL+SOS House Position seat (symmetric with the Senate, driven by the
WSL refresh's sponsor archive). It archives the current election's votewa filing cohort and
re-drives :func:`build_house_position_spans` scoped to the current biennium. Runs after the WSL
refresh (its sponsor archive + Persons are the roster the builder reads).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID
from usa_wa_adapter_sos.filings.transport import WireFetch
from usa_wa_adapter_sos.house import refresh as refresh_module
from usa_wa_adapter_sos.house.refresh import run_refresh

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person

BIENNIUM = "2025-26"


class _StubSponsorClient:
    async def fetch_sponsors(self, biennium):  # pragma: no cover
        raise AssertionError("live sponsor pull; era roster must be archive-first")

    async def parse_sponsors(self, wire):
        return json.loads(wire.decode())


class FakeSOSClient:
    def __init__(self, csv_rows=None):
        self._rows = csv_rows or []

    async def fetch_whofiled(self, election_year):
        header = "RaceName,RaceJurisdictionName,BallotName,PartyName\r\n"
        body = "".join(
            f"{race},Legislative District {ld},{ballot},{party}\r\n"
            for race, ld, ballot, party in self._rows
        )
        wire = (header + body).encode()
        return WireFetch(records=[], wire=wire, content_type="text/csv")


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
    db_session.add(row)
    await db_session.flush()
    return row


async def _add_ld(session, usa_wa, n):
    session.add(
        Jurisdiction(
            slug=f"usa-wa-ld-{n}",
            name=f"LD {n}",
            type_id=usa_wa.type_id,
            pm_jurisdiction_id=_ULID(),
            recorded_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _add_person(session, mid, name):
    session.add(Person(source="usa_wa_legislature", source_id=str(mid), name_full=name))
    await session.flush()


async def _archive_sponsors(session, wsl_source, biennium, rows):
    body = json.dumps(rows).encode()
    ev = FetchEvent(
        source_id=wsl_source.id,
        resource_id=f"sponsors:{biennium}",
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x01" * 32,
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(RawPayload(fetch_event_id=ev.id, content_type="x", body=body, size_bytes=len(body)))
    await session.flush()


def _sponsor(mid, ld, last, agency="House"):
    return {
        "Id": mid,
        "FirstName": "X",
        "LastName": last,
        "District": str(ld),
        "Agency": agency,
        "Party": "D",
    }


async def test_refresh_archives_cohort_and_materializes_house_seat(db_session, usa_wa, wsl_source):
    """The daily SOS refresh archives the current votewa cohort and materializes the House
    Position seat as a usa_wa_legislature Assignment for a sitting member."""
    await _add_ld(db_session, usa_wa, 42)
    await _add_person(db_session, 100, "Alicia Rule")
    await _archive_sponsors(db_session, wsl_source, BIENNIUM, [_sponsor(100, 42, "Rule")])
    sos = FakeSOSClient(
        [("State Representative Pos. 1", 42, "Alicia Rule", "(Prefers Democratic Party)")]
    )

    outcome = await run_refresh(
        db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient(), sos_client=sos
    )

    assert outcome.cohorts_archived == 1
    assert outcome.house_spans == 1
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source == "usa_wa_legislature")
        )
    ).scalar_one()
    assert row.source_id == "100:chamber-house:ld-42-position-1:2025-26"
    assert row.valid_to is None and row.is_active is True  # current → open end


async def test_refresh_is_idempotent_across_two_cycles(db_session, usa_wa, wsl_source):
    """Two consecutive refresh cycles converge — one Assignment, a stable citation count, no
    duplicate rows (the property the daily unit relies on)."""
    await _add_ld(db_session, usa_wa, 42)
    await _add_person(db_session, 100, "Alicia Rule")
    await _archive_sponsors(db_session, wsl_source, BIENNIUM, [_sponsor(100, 42, "Rule")])
    sos = FakeSOSClient(
        [("State Representative Pos. 1", 42, "Alicia Rule", "(Prefers Democratic Party)")]
    )

    first = await run_refresh(
        db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient(), sos_client=sos
    )
    second = await run_refresh(
        db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient(), sos_client=sos
    )

    assert first.house_spans == 1 and second.house_spans == 1
    rows = (
        (
            await db_session.execute(
                select(Assignment).where(Assignment.source == "usa_wa_legislature")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # converged, not duplicated
    citations = await db_session.scalar(
        select(func.count()).select_from(Citation).where(Citation.entity_id == rows[0].id)
    )
    assert citations == 1  # one biennium cited, not re-appended per cycle


async def test_refresh_warns_on_noncurrent_biennium(db_session, usa_wa, wsl_source, caplog):
    await _archive_sponsors(db_session, wsl_source, "2019-20", [])
    with caplog.at_level(logging.WARNING):
        await run_refresh(
            db_session,
            biennium="2019-20",
            sponsor_client=_StubSponsorClient(),
            sos_client=FakeSOSClient(),
        )
    assert "sos_refresh_noncurrent_biennium" in [r.message for r in caplog.records]


async def test_main_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(refresh_module, "configure_logging"):
        code = await refresh_module._main()
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err
