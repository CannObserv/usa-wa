"""End-to-end Phase B SOS-fed House Position span build (#100), fully offline.

Archives a **pre-2018** PDC House cohort (no ``position`` — the dataset gap #98 found), the
seating biennium's WSL sponsor roster, and the SOS filing cohort that carries the ballot
position, then drives :func:`build_sos_house_spans`: the SOS fallback supplies the qualifier
PDC omitted, so a seat that would otherwise be ``missing_position`` is materialized.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import func, select
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.build_pdc_spans import PdcSpanResult
from usa_wa_adapter_sos import build_sos_house_spans as build_module
from usa_wa_adapter_sos.build_sos_house_spans import build_sos_house_spans

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person

CURRENT = "2025-26"


async def _source(session, usa_wa, slug, kind):
    row = Source(jurisdiction_id=usa_wa.id, name=slug, slug=slug, kind=kind)
    session.add(row)
    await session.flush()
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


async def _add_person(session, mid):
    session.add(Person(source="usa_wa_legislature", source_id=str(mid), name_full=f"M{mid}"))
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


def _pdc_house(pid, ld, filer, position=""):
    """A PDC House winner row with an (optionally absent) position — pre-2018 omits it."""
    return json.dumps(
        [
            {
                "person_id": pid,
                "legislative_district": str(ld),
                "position": position,
                "filer_name": filer,
                "party_code": "DEMOCRAT",
            }
        ]
    ).encode()


def _sponsor_wire(*rows):
    return json.dumps(
        [
            {
                "Id": mid,
                "FirstName": "M",
                "LastName": last,
                "District": str(ld),
                "Agency": ag,
                "Party": "D",
            }
            for mid, ld, last, ag in rows
        ]
    ).encode()


def _sos_csv(*rows):
    """A votewa filing CSV wire — (race_name, ld, ballot, party) tuples."""
    header = "RaceName,RaceJurisdictionName,BallotName,PartyName\r\n"
    body = "".join(
        f"{race},Legislative District {ld},{ballot},{party}\r\n" for race, ld, ballot, party in rows
    )
    return (header + body).encode()


class _StubSponsorClient:
    async def fetch_sponsors(self, biennium):  # pragma: no cover
        raise AssertionError(f"live sponsor pull for {biennium}; roster must be archive-first")

    async def parse_sponsors(self, wire):
        return json.loads(wire.decode())


async def test_sos_fallback_seats_pre_2018_house_position(db_session, usa_wa):
    """2016 PDC cohort (no position) → member 100 matched via roster, position supplied by the
    SOS 2016 filing (Pos. 2) → a House Position span at ld-5-position-2, all offline."""
    wsl = await _source(db_session, usa_wa, "usa_wa_legislature", "soap")
    pdc = await _source(db_session, usa_wa, "usa_wa_pdc", "rest")
    sos = await _source(db_session, usa_wa, "usa_wa_sos", "rest")
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)

    await _archive(db_session, pdc, "house-winners:2016", _pdc_house("900", 5, "M100 Smith"))
    await _archive(db_session, wsl, "sponsors:2017-18", _sponsor_wire((100, 5, "Smith", "House")))
    await _archive(
        db_session,
        sos,
        "sos-whofiled:201611",
        _sos_csv(("State Representative Pos. 2", 5, "Jane Smith", "(Prefers Democratic Party)")),
    )

    result = await build_sos_house_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.house_spans == 1
    row = (
        await db_session.execute(select(Assignment).where(Assignment.source == "usa_wa_pdc"))
    ).scalar_one()
    assert row.source_id == "100:chamber-house:ld-5-position-2:2017-18"
    assert row.valid_to is not None and row.is_active is False  # 2017-18 closed vs current


async def test_without_sos_archive_pre_2018_seat_is_not_materialized(db_session, usa_wa):
    """Control: same PDC cohort + roster but NO SOS archive → no position, no House span
    (the #98 status quo — the fallback has nothing to supply)."""
    wsl = await _source(db_session, usa_wa, "usa_wa_legislature", "soap")
    pdc = await _source(db_session, usa_wa, "usa_wa_pdc", "rest")
    await _source(db_session, usa_wa, "usa_wa_sos", "rest")
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)

    await _archive(db_session, pdc, "house-winners:2016", _pdc_house("900", 5, "M100 Smith"))
    await _archive(db_session, wsl, "sponsors:2017-18", _sponsor_wire((100, 5, "Smith", "House")))

    result = await build_sos_house_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.house_spans == 0
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0
    # No SOS cohort for 2016 → the factory yields no fallback, so a position-less winner is
    # ``incomplete`` (the pure PDC path), not ``missing_position``.
    assert result.coverage[2016]["incomplete"] == 1
    assert result.coverage[2016]["missing_position"] == 0


async def test_main_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(build_module, "configure_logging"):
        code = await build_module._main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back(monkeypatch, capsys, test_engine):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    fake = PdcSpanResult(house_spans=7, identifiers=3)

    async def _fake_build(session, **_kwargs):
        return fake

    with (
        patch.object(build_module, "configure_logging"),
        patch.object(build_module, "build_sos_house_spans", _fake_build),
    ):
        code = await build_module._main(["--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "house_spans=7" in out
    assert "dry-run, rolled back" in out
