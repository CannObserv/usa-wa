"""SOS refresh cycle (#101) — re-drive the House Position builder from the archive.

The daily driver of the WSL+SOS House Position seat (symmetric with the Senate, driven by the
WSL refresh's sponsor archive). Since #201 it is **rebuild-only**: the Phase-A archive refresh
is `usa_wa_adapter_sos.results.archive_refresh` (its own unit, ordered before this one), and
this module re-drives :func:`build_house_position_spans` scoped to the current biennium from
whatever the archive holds. Runs after the WSL refresh (its sponsor archive + Persons are the
roster the builder reads).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID

from clearinghouse_core import job as job_module
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person
from usa_wa_facts_seats.house import refresh as refresh_module
from usa_wa_facts_seats.house.refresh import run_refresh

BIENNIUM = "2025-26"


class _StubSponsorClient:
    async def fetch_sponsors(self, biennium):  # pragma: no cover
        raise AssertionError("live sponsor pull; era roster must be archive-first")

    async def parse_sponsors(self, wire):
        return json.loads(wire.decode())


@pytest.fixture
async def wsl_source(db_session, usa_wa):
    row = Source(jurisdiction_id=usa_wa.id, name="WSL", slug="usa_wa_legislature", kind="soap")
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def sos_source(db_session, usa_wa):
    """The results Source the **archive** half provisions — pre-seeded here, as the
    `usa-wa-sos-archive-refresh.service` predecessor does in prod."""
    row = Source(
        jurisdiction_id=usa_wa.id, name="SOS results", slug="usa_wa_sos_results", kind="rest"
    )
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


async def _archive(session, source, resource_id, body):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes([hash(resource_id) & 0xFF]) * 32,
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(RawPayload(fetch_event_id=ev.id, content_type="x", body=body, size_bytes=len(body)))
    await session.flush()


async def _archive_sponsors(session, wsl_source, biennium, rows):
    await _archive(session, wsl_source, f"sponsors:{biennium}", json.dumps(rows).encode())


async def _archive_results(session, sos_source, resource_id, rows):
    header = '"Race","Candidate","Party"\r\n'
    body = "".join(
        f'"LEGISLATIVE DISTRICT {ld} - {race}","{ballot}","{party}"\r\n'
        for race, ld, ballot, party in rows
    )
    await _archive(session, sos_source, resource_id, (header + body).encode())


def _sponsor(mid, ld, last, agency="House"):
    return {
        "Id": mid,
        "FirstName": "X",
        "LastName": last,
        "District": str(ld),
        "Agency": agency,
        "Party": "D",
    }


async def _seed_sitting_member(db_session, usa_wa, wsl_source, sos_source):
    await _add_ld(db_session, usa_wa, 42)
    await _add_person(db_session, 100, "Alicia Rule")
    await _archive_sponsors(db_session, wsl_source, BIENNIUM, [_sponsor(100, 42, "Rule")])
    await _archive_results(
        db_session,
        sos_source,
        "sos-legresults:20241105",
        [("State Representative Pos. 1", 42, "Alicia Rule", "(Prefers Democratic Party)")],
    )


async def test_refresh_materializes_the_house_seat_from_the_archive(
    db_session, usa_wa, wsl_source, sos_source
):
    """The daily SOS refresh materializes the House Position seat as a usa_wa_legislature
    Assignment for a sitting member, reading both cohorts **archive-first**."""
    await _seed_sitting_member(db_session, usa_wa, wsl_source, sos_source)

    outcome = await run_refresh(db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient())

    assert outcome.house_spans == 1
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source == "usa_wa_legislature")
        )
    ).scalar_one()
    assert row.source_id == "100:chamber-house:ld-42-position-1:2025-26"
    assert row.valid_to is None and row.is_active is True  # current → open end


async def test_refresh_archives_nothing(db_session, usa_wa, wsl_source, sos_source):
    """#201: the fact rebuilds, it does not source. No live client is injectable and no
    ``FetchEvent`` is written — refreshing the archive is
    ``usa_wa_adapter_sos.results.archive_refresh``'s job, in its own unit and its own ledger
    row. This is the assertion behind dropping the `import-linter` exception."""
    await _seed_sitting_member(db_session, usa_wa, wsl_source, sos_source)
    before = await db_session.scalar(select(func.count()).select_from(FetchEvent))

    await run_refresh(db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient())

    assert await db_session.scalar(select(func.count()).select_from(FetchEvent)) == before


async def test_refresh_rebuilds_on_a_stale_archive(db_session, usa_wa, wsl_source, sos_source):
    """The failure semantics the unit split buys: when the archive half fails (a votewa outage),
    the rebuild still runs against the **last good** archive rather than being cancelled — the
    seat keeps tracking the WSL roster, which does not depend on votewa at all."""
    await _seed_sitting_member(db_session, usa_wa, wsl_source, sos_source)
    # No odd-year (2025) cohort was ever archived — exactly the state a failed archive leaves.
    outcome = await run_refresh(db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient())
    assert outcome.house_spans == 1


async def test_refresh_is_idempotent_across_two_cycles(db_session, usa_wa, wsl_source, sos_source):
    """Two consecutive refresh cycles converge — one Assignment, a stable citation count, no
    duplicate rows (the property the daily unit relies on)."""
    await _seed_sitting_member(db_session, usa_wa, wsl_source, sos_source)

    first = await run_refresh(db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient())
    second = await run_refresh(db_session, biennium=BIENNIUM, sponsor_client=_StubSponsorClient())

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
        await run_refresh(db_session, biennium="2019-20", sponsor_client=_StubSponsorClient())
    assert "sos_refresh_noncurrent_biennium" in [r.message for r in caplog.records]


def test_main_requires_database_url(monkeypatch, capsys):
    def _raise(_role="app"):
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    code = refresh_module.main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err
