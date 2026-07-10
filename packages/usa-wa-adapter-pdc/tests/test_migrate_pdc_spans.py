"""PDC House-seat migration (#79): collapse the pre-#79 per-biennium rows onto spans.

Before #79 the daily PDC path emitted one House Assignment per member **per biennium**
(``{member}:chamber-house:{biennium}``, ``source=usa_wa_pdc``). The span builder emits one merged
Assignment per contiguous House Position tenure, keyed ``{member}:chamber-house:{ld}-pos:{start}``
(4-part vs the legacy 3-part). So every legacy row is stranded and gets retired onto its covering
span, transferring the PM anchor — the #82 stranded-row pattern, scoped to the PDC source.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from ulid import ULID as _ULID
from usa_wa_adapter_pdc import migrate_pdc_spans as migrate_module
from usa_wa_adapter_pdc.migrate_pdc_spans import MigrationResult, migrate_pdc_spans

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role

PDC = "usa_wa_pdc"
WSL = "usa_wa_legislature"


@pytest.fixture
async def house_org(db_session, usa_wa):
    org = Organization(
        source=WSL,
        source_id="house",
        jurisdiction_id=usa_wa.id,
        name="House",
        short_name="House",
        org_type="chamber",
    )
    db_session.add(org)
    await db_session.flush()
    return org


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
    row = Person(source=WSL, source_id=str(mid), name_full=f"M{mid}")
    session.add(row)
    await session.flush()
    return row


async def _add_role(session, house_org, ld, qualifier):
    from usa_wa_adapter_pdc.normalize.positions import house_seat_role_source_id

    row = Role(
        source=WSL,
        source_id=house_seat_role_source_id(ld, qualifier),
        organization_id=house_org.id,
        name="State Representative",
        role_type="state_representative",
        qualifier=qualifier,
    )
    session.add(row)
    await session.flush()
    return row


async def _add_assignment(
    session, *, source_id, person_id, role_id, valid_from, valid_to, is_active, pm_id
):
    row = Assignment(
        source=PDC,
        source_id=source_id,
        person_id=person_id,
        role_id=role_id,
        valid_from=valid_from,
        valid_to=valid_to,
        is_active=is_active,
        pm_assignment_id=pm_id,
    )
    session.add(row)
    await session.flush()
    return row


async def _count(session, model, **where):
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalar()


async def test_legacy_row_retired_onto_covering_span_with_its_anchor(db_session, usa_wa, house_org):
    person = await _add_person(db_session, 100)
    role = await _add_role(db_session, house_org, 5, "Position 1")
    # The span (from the backfill): merged tenure since 2013-14, still open.
    span = await _add_assignment(
        db_session,
        source_id="100:chamber-house:ld-5-position-1:2013-14",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2013, 1, 1),
        valid_to=None,
        is_active=True,
        pm_id=None,
    )
    # The shipped per-biennium row for the current biennium, carrying the PM anchor.
    pm_id = _ULID()
    await _add_assignment(
        db_session,
        source_id="100:chamber-house:2025-26",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2025, 1, 1),
        valid_to=None,
        is_active=True,
        pm_id=pm_id,
    )

    result = await migrate_pdc_spans(db_session)

    assert (result.legacy_found, result.anchors_transferred, result.legacy_retired) == (1, 1, 1)
    assert result.orphans_no_span == 0
    assert await _count(db_session, Assignment, source_id="100:chamber-house:2025-26") == 0
    await db_session.refresh(span)
    assert span.pm_assignment_id == pm_id  # anchor moved to the span


async def test_shallow_state_nothing_stranded(db_session, usa_wa, house_org):
    """Only per-biennium rows, no spans yet → each is legacy but has no covering span, so it's
    left as an orphan (safe — nothing deleted; re-run after build_pdc_spans)."""
    person = await _add_person(db_session, 100)
    role = await _add_role(db_session, house_org, 5, "Position 1")
    await _add_assignment(
        db_session,
        source_id="100:chamber-house:2025-26",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2025, 1, 1),
        valid_to=None,
        is_active=True,
        pm_id=_ULID(),
    )

    result = await migrate_pdc_spans(db_session)

    assert result.legacy_found == 1 and result.orphans_no_span == 1 and result.legacy_retired == 0
    assert await _count(db_session, Assignment, source_id="100:chamber-house:2025-26") == 1


async def test_wsl_rows_are_untouched(db_session, usa_wa, house_org):
    """Only usa_wa_pdc chamber-house rows are in scope — WSL party/senate/committee rows aren't."""
    person = await _add_person(db_session, 100)
    role = await _add_role(db_session, house_org, 5, "Position 1")
    wsl = Assignment(
        source=WSL,
        source_id="100:chamber-senate:5:2025-26",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2025, 1, 1),
        is_active=True,
        pm_assignment_id=_ULID(),
    )
    db_session.add(wsl)
    await db_session.flush()

    result = await migrate_pdc_spans(db_session)

    assert result.legacy_found == 0
    assert await _count(db_session, Assignment, source=WSL) == 1


async def test_idempotent(db_session, usa_wa, house_org):
    person = await _add_person(db_session, 100)
    role = await _add_role(db_session, house_org, 5, "Position 1")
    await _add_assignment(
        db_session,
        source_id="100:chamber-house:ld-5-position-1:2013-14",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2013, 1, 1),
        valid_to=None,
        is_active=True,
        pm_id=None,
    )
    await _add_assignment(
        db_session,
        source_id="100:chamber-house:2025-26",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2025, 1, 1),
        valid_to=None,
        is_active=True,
        pm_id=_ULID(),
    )

    first = await migrate_pdc_spans(db_session)
    second = await migrate_pdc_spans(db_session)

    assert first.legacy_retired == 1
    assert second.legacy_found == 0 and second.legacy_retired == 0


async def test_main_requires_owner_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    with patch.object(migrate_module, "configure_logging"):
        code = await migrate_module._main([])
    assert code == 2
    assert "DATABASE_URL_OWNER is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back(monkeypatch, capsys, test_engine):
    monkeypatch.setenv("DATABASE_URL_OWNER", os.environ["TEST_DATABASE_URL"])
    fake = MigrationResult(
        legacy_found=2, anchors_transferred=2, legacy_retired=2, orphans_no_span=0
    )

    async def _fake(session, **_):
        return fake

    with (
        patch.object(migrate_module, "configure_logging"),
        patch.object(migrate_module, "migrate_pdc_spans", _fake),
    ):
        code = await migrate_module._main(["--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "legacy_found=2 anchors_transferred=2 retired=2" in out
    assert "dry-run, rolled back" in out
