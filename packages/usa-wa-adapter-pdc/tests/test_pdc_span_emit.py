"""PDC ``person_wa_pdc`` identifier links (#79; identifier-only since #101).

The idempotent ``person_wa_pdc`` child-identifier upsert — PDC's demoted contribution. The House
Position seat emission moved to :mod:`usa_wa_adapter_sos.house_span_emit` (#101); see
``packages/usa-wa-adapter-sos/tests/test_house_span_emit.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.normalize.pdc_span_emit import emit_pdc_identifiers

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_domain_legislative.identity import Person, PersonIdentifier

CURRENT = "2025-26"


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
    row = Person(source="usa_wa_legislature", source_id=str(mid), name_full="Ann Rivers")
    session.add(row)
    await session.flush()
    return row


async def _count(session, model, **where):
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalar()


async def test_emit_pdc_identifiers_idempotent(db_session, usa_wa):
    person = await _add_person(db_session, 100)
    added = await emit_pdc_identifiers(db_session, [("100", "900"), ("100", "900")])
    again = await emit_pdc_identifiers(db_session, [("100", "900")])

    assert added == 1 and again == 0  # dedup within a call + across calls
    row = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.source_id == "900:wa_pdc")
        )
    ).scalar_one()
    assert row.person_id == person.id and row.scheme == "wa_pdc" and row.value == "900"


async def test_emit_pdc_identifiers_skips_absent_person(db_session, usa_wa):
    added = await emit_pdc_identifiers(db_session, [("999", "900")])  # no such WSL Person
    assert added == 0
    assert await _count(db_session, PersonIdentifier) == 0
