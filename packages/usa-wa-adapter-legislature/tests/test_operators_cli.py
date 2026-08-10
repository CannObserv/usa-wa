"""Operator-event CLI (#107) — validation + record + supersede + batch."""

from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import select

from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.identity import Person
from clearinghouse_domain_legislative.operator_events import OperatorEvent
from usa_wa_adapter_legislature.operators import cli
from usa_wa_adapter_legislature.operators.cli import (
    EventSpec,
    OperatorEventError,
    load_specs,
    validate_and_record,
)
from usa_wa_adapter_legislature.operators.store import get_or_create_operator_source
from usa_wa_common.jurisdiction import resolve_jurisdiction


async def _source(session):
    return await get_or_create_operator_source(session, await resolve_jurisdiction(session))


async def _person(session, mid):
    session.add(Person(source="usa_wa_legislature", source_id=mid, name_full="M"))
    await session.flush()


def _departed(member="100", d=date(2025, 4, 19)):
    return EventSpec(
        member_id=member,
        kind="departed",
        reason="died",
        effective_date=d,
        evidence_url="https://example.gov/x",
    )


async def test_records_a_valid_departed_event(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    event = await validate_and_record(db_session, source, _departed())
    assert event.kind == "departed" and event.member_id == "100"


async def test_unknown_member_rejected(db_session, usa_wa):
    source = await _source(db_session)
    with pytest.raises(OperatorEventError, match="resolves to no"):
        await validate_and_record(db_session, source, _departed(member="999"))


async def test_records_a_vacated_defeated_event(db_session, usa_wa):
    """A member defeated at an election vacates the seat (#152) — the reason an
    appointee's loss of the ensuing special/general election needs (Grant-Herriot, #144)."""
    await _person(db_session, "100")
    source = await _source(db_session)
    spec = EventSpec(
        member_id="100",
        kind="vacated",
        reason="defeated",
        effective_date=date(2009, 11, 3),
        evidence_url="https://example.gov/x",
        seat_kind="chamber-house",
        seat_discriminator="ld-16-position-2",
    )
    event = await validate_and_record(db_session, source, spec)
    assert event.kind == "vacated" and event.reason == "defeated"


async def test_bad_reason_for_kind_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    bad = EventSpec(
        member_id="100",
        kind="departed",
        reason="appointed",
        effective_date=date(2025, 4, 19),
        evidence_url="https://x",
    )
    with pytest.raises(OperatorEventError, match="reason"):
        await validate_and_record(db_session, source, bad)


async def test_seated_without_seat_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    bad = EventSpec(
        member_id="100",
        kind="seated",
        reason="appointed",
        effective_date=date(2025, 6, 3),
        evidence_url="https://x",
    )
    with pytest.raises(OperatorEventError, match="requires --seat"):
        await validate_and_record(db_session, source, bad)


async def test_departed_with_seat_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    bad = EventSpec(
        member_id="100",
        kind="departed",
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://x",
        seat_kind="chamber-senate",
        seat_discriminator="5",
    )
    with pytest.raises(OperatorEventError, match="must not carry a seat"):
        await validate_and_record(db_session, source, bad)


async def test_unknown_seat_kind_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    bad = EventSpec(
        member_id="100",
        kind="seated",
        reason="appointed",
        effective_date=date(2025, 6, 3),
        evidence_url="https://x",
        seat_kind="chamber-hosue",  # typo — no builder owns it
        seat_discriminator="5",
    )
    with pytest.raises(OperatorEventError, match="not a known seat kind"):
        await validate_and_record(db_session, source, bad)


async def test_supersede_with_mismatched_kind_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _departed(d=date(2025, 4, 19)))
    mismatched = EventSpec(
        member_id="100",
        kind="vacated",  # differs from prior's departed — would silently apply reason to prior.kind
        reason="moved",
        effective_date=date(2025, 4, 20),
        evidence_url="https://x",
        seat_kind="chamber-senate",
        seat_discriminator="5",
        supersede_id=str(prior.id),
    )
    with pytest.raises(OperatorEventError, match="differs from the prior"):
        await validate_and_record(db_session, source, mismatched)


async def test_supersede_with_mismatched_seat_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    prior = await validate_and_record(
        db_session,
        source,
        EventSpec(
            member_id="100",
            kind="seated",
            reason="appointed",
            effective_date=date(2025, 6, 3),
            evidence_url="https://x",
            seat_kind="chamber-senate",
            seat_discriminator="5",
        ),
    )
    mismatched = EventSpec(
        member_id="100",
        kind="seated",
        reason="appointed",
        effective_date=date(2025, 6, 4),
        evidence_url="https://x",
        seat_kind="chamber-senate",
        seat_discriminator="6",  # differs from prior's LD 5
        supersede_id=str(prior.id),
    )
    with pytest.raises(OperatorEventError, match="seat differs"):
        await validate_and_record(db_session, source, mismatched)


async def test_supersede_records_correction(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _departed(d=date(2025, 4, 19)))
    corrected = await validate_and_record(
        db_session,
        source,
        EventSpec(
            member_id="100",
            kind="departed",
            reason="died",
            effective_date=date(2025, 4, 20),
            evidence_url="https://x",
            supersede_id=str(prior.id),
        ),
    )
    assert corrected.effective_date == date(2025, 4, 20)
    refreshed = (
        await db_session.execute(select(OperatorEvent).where(OperatorEvent.id == prior.id))
    ).scalar_one()
    assert refreshed.superseded_by_id == corrected.id


def test_load_specs_parses_batch():
    specs = load_specs(
        [
            {
                "member_id": "29091",
                "kind": "departed",
                "reason": "died",
                "effective_date": "2025-04-19",
                "evidence_url": "https://a",
            },
            {
                "member_id": "35410",
                "kind": "seated",
                "reason": "appointed",
                "effective_date": "2025-06-03",
                "evidence_url": "https://b",
                "seat_kind": "chamber-senate",
                "seat_discriminator": "5",
            },
        ]
    )
    assert [s.kind for s in specs] == ["departed", "seated"]
    assert specs[1].seat_discriminator == "5"


def test_load_specs_rejects_non_list():
    with pytest.raises(OperatorEventError, match="JSON array"):
        load_specs({"member_id": "1"})


# --- CLI (#179b: the shared job harness) --------------------------------------


def test_main_validation_failure_is_still_exit_two(monkeypatch, capsys):
    """Documented contract (COMMANDS-SUCCESSION.md): exit 2 on a validation failure."""
    recording = patch_job_runtime(monkeypatch)

    async def _reject(_session, _args):
        raise OperatorEventError("--member-id, --kind and --evidence-url are required")

    with patch.object(cli, "_run", _reject):
        assert cli.main(["--member-id", "100"]) == 2

    assert (recording.committed, recording.rolled_back) == (0, 1)
    assert "--member-id" in capsys.readouterr().err


def test_main_list_commits_even_under_dry_run(monkeypatch):
    """Preserves the pre-#179b ``dry_run and not list`` branch exactly."""
    recording = patch_job_runtime(monkeypatch)

    async def _fake_run(_session, _args):
        return 0

    with patch.object(cli, "_run", _fake_run):
        assert cli.main(["--dry-run", "--list"]) == 0

    assert (recording.committed, recording.rolled_back) == (1, 0)
