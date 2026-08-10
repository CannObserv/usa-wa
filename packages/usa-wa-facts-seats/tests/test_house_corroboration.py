"""House odd-year special-winner corroboration (#149).

The House sibling of ``senate_corroboration.missing_winner_lds`` (#123 §2b): an odd-year House
**special** winner with no open ``state_representative`` Position seat is a silent unseated member —
the LD30 Pos 2 2015-16 / Teri Hickel shape, found by manual audit because the daily refresh runs
``restrict_to_biennium=current`` and never re-emits a historical biennium. Read-only (no citation
half — House Position spans already cite the odd wire in ``build.py``). Fully offline: an archived
results wire + hand-built open House seats.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from unittest.mock import patch

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_common.jurisdiction import resolve_jurisdiction
from usa_wa_common.seats import house_seat_role_source_id
from usa_wa_facts_seats import house_corroboration as corroboration_module
from usa_wa_facts_seats.house_corroboration import (
    HouseCorroborationResult,
    HouseSweepOutcome,
    _seat_from_role_source_id,
    corroborate_house_winners,
    house_occupants,
    sweep_exit_code,
    sweep_house_winners,
)

CURRENT = "2025-26"
LD30_BIENNIUM = "2015-16"
ODD_2015 = "sos-legresults:20151103"
ODD_2025 = "sos-legresults:20251104"


async def _archive(session, source, resource_id, body):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes(32),
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(RawPayload(fetch_event_id=ev.id, content_type="x", body=body, size_bytes=len(body)))
    await session.flush()
    return ev


def _house_csv(*rows):
    """``(ld, position, candidate, party)`` → a House-only legislative-results CSV."""
    header = '"Race","Candidate","Party"\r\n'
    body = "".join(
        f'"LEGISLATIVE DISTRICT {ld} - State Representative Pos. {pos}","{name}","{party}"\r\n'
        for ld, pos, name, party in rows
    )
    return (header + body).encode()


async def _open_house_seat(
    session, anchors, *, ld, position, person_name, member_id, valid_from=None, valid_to=None
):
    """A ``state_representative`` Position seat with one occupant. ``valid_to``/``is_active`` let a
    test build a *closed* historical span (for the sweep) or an *open* current one (the daily gate).
    """
    person = Person(source="usa_wa_legislature", source_id=str(member_id), name_full=person_name)
    session.add(person)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id=house_seat_role_source_id(ld, position),
        organization_id=anchors.house_id,
        name="State Representative",
        role_type="state_representative",
        jurisdiction_id=None,
        qualifier=position,
    )
    session.add(role)
    await session.flush()
    assignment = Assignment(
        source="usa_wa_legislature",
        source_id=f"{member_id}:chamber-house:ld-{ld}-{position.lower().replace(' ', '-')}:x",
        person_id=person.id,
        role_id=role.id,
        valid_from=valid_from or date(2015, 11, 4),
        valid_to=valid_to,
        is_active=valid_to is None,
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def _setup(session, usa_wa, biennium=CURRENT):
    jurisdiction = await resolve_jurisdiction(session)
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=biennium, jurisdiction_id=jurisdiction.id
    )
    sos = await get_or_create_results_source(session, jurisdiction)
    return jurisdiction, anchors, sos


async def test_missing_house_special_seat_is_a_violation(db_session, usa_wa):
    """The LD30/Hickel shape: an odd-year special winner archived + named, but NO open
    ``state_representative`` Pos 2 seat exists at that LD → named + gate fails (exit 1). This is the
    operational gap (a backfill that wasn't run) the unit guard (#148) cannot detect."""
    _jurisdiction, _anchors, sos = await _setup(db_session, usa_wa, LD30_BIENNIUM)
    await _archive(db_session, sos, ODD_2015, _house_csv((30, 2, "Teri Hickel", "(Prefers GOP)")))

    result = await corroborate_house_winners(db_session, biennium=LD30_BIENNIUM)

    assert result.odd_year == 2015 and result.winners == 1
    assert result.missing_seats == [(30, "Position 2")] and not result.ok


async def test_correctly_seated_odd_winner_passes(db_session, usa_wa):
    """A special winner with an open Pos 2 seat held by her is a clean pass — no missing seat, no
    mismatch."""
    _jurisdiction, anchors, sos = await _setup(db_session, usa_wa, LD30_BIENNIUM)
    await _open_house_seat(
        db_session, anchors, ld=30, position="Position 2", person_name="Teri Hickel", member_id=201
    )
    await _archive(db_session, sos, ODD_2015, _house_csv((30, 2, "Teri Hickel", "(Prefers GOP)")))

    result = await corroborate_house_winners(db_session, biennium=LD30_BIENNIUM)

    assert result.winners == 1
    assert result.missing_seats == [] and result.mismatched_seats == [] and result.ok


async def test_mismatched_occupant_is_reported_not_a_violation(db_session, usa_wa):
    """A seat occupied by someone other than the ballot winner (a name change or a not-yet-succeeded
    predecessor) is surfaced as ``mismatched`` but does NOT fail the gate — the seat exists. Mirrors
    ``senate_corroboration`` §2b: gate on seat existence, not occupant identity."""
    _jurisdiction, anchors, sos = await _setup(db_session, usa_wa, LD30_BIENNIUM)
    await _open_house_seat(
        db_session, anchors, ld=30, position="Position 2", person_name="Roger Freeman", member_id=9
    )
    await _archive(db_session, sos, ODD_2015, _house_csv((30, 2, "Teri Hickel", "(Prefers GOP)")))

    result = await corroborate_house_winners(db_session, biennium=LD30_BIENNIUM)

    assert result.mismatched_seats == [(30, "Position 2")]
    assert result.missing_seats == [] and result.ok  # occupied, so not a missing-event violation


async def test_no_odd_cohort_is_a_clean_no_op(db_session, usa_wa):
    """Before the odd November (or a race-less biennium) there is no archived odd cohort — a clean
    zero-winner pass, never a false violation."""
    await _setup(db_session, usa_wa)
    result = await corroborate_house_winners(db_session, biennium=CURRENT)
    assert result.winners == 0 and result.ok and result.missing_seats == []


async def test_only_odd_year_winners_are_gated(db_session, usa_wa):
    """The gate consumes only the mid-biennium odd special (``election_years_for_biennium[-1]``);
    the even seating year's House winners are dated by the sponsor roster and must not be probed —
    an even-only archive is a clean no-op."""
    _jurisdiction, _anchors, sos = await _setup(db_session, usa_wa, LD30_BIENNIUM)
    # Even seating cohort only (2014), no odd 2015 special archived.
    await _archive(
        db_session,
        sos,
        "sos-legresults:20141104",
        _house_csv((30, 1, "Linda Kochmar", "(Prefers Republican Party)")),
    )
    result = await corroborate_house_winners(db_session, biennium=LD30_BIENNIUM)
    assert result.odd_year == 2015 and result.winners == 0 and result.ok


async def test_noncurrent_biennium_pin_warns(db_session, usa_wa, caplog):
    """A biennium differing from the date-current one (a stale $USA_WA_BIENNIUM / --biennium pin)
    logs a non-current WARNING breadcrumb — parity with the WSL/PDC/SOS refreshes + Senate
    corroboration. A current-biennium run stays quiet."""
    await _setup(db_session, usa_wa)
    with caplog.at_level(logging.WARNING):
        await corroborate_house_winners(db_session, biennium="2019-20")
    assert "house_corroboration_noncurrent_biennium" in [r.message for r in caplog.records]

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await corroborate_house_winners(db_session, biennium=CURRENT)
    assert "house_corroboration_noncurrent_biennium" not in [r.message for r in caplog.records]


async def test_sweep_reports_a_missing_historical_special_seat(db_session, usa_wa):
    """``--sweep-biennia`` (report-only, the #119 pattern): a historical odd special with no
    covering seat is reported across the whole archive — the LD30-2015-16-as-history regression the
    daily current-biennium gate can't reach (it only probes the current odd cohort)."""
    _jurisdiction, _anchors, sos = await _setup(db_session, usa_wa)
    await _archive(db_session, sos, ODD_2015, _house_csv((30, 2, "Teri Hickel", "(Prefers GOP)")))

    outcome = await sweep_house_winners(db_session)

    assert (2015, 30, "Position 2") in outcome.missing


async def test_sweep_is_clean_when_the_historical_seat_is_covered(db_session, usa_wa):
    """A historical special whose (now-closed) seat covers the odd year's occupancy date is not
    reported — the post-backfill clean state."""
    _jurisdiction, anchors, sos = await _setup(db_session, usa_wa)
    await _open_house_seat(
        db_session,
        anchors,
        ld=30,
        position="Position 2",
        person_name="Teri Hickel",
        member_id=201,
        valid_from=date(2015, 1, 1),
        valid_to=date(2016, 12, 31),  # closed but covering the 2015 special date
    )
    await _archive(db_session, sos, ODD_2015, _house_csv((30, 2, "Teri Hickel", "(Prefers GOP)")))

    outcome = await sweep_house_winners(db_session)

    assert outcome.missing == []


async def test_sweep_reports_a_mismatched_historical_occupant(db_session, usa_wa):
    """A historical special whose covering seat is held by someone other than the ballot winner is
    surfaced as ``mismatched`` (not ``missing``) — the sweep's identity side, report-only."""
    _jurisdiction, anchors, sos = await _setup(db_session, usa_wa)
    await _open_house_seat(
        db_session,
        anchors,
        ld=30,
        position="Position 2",
        person_name="Roger Freeman",  # covering occupant, wrong person
        member_id=9,
        valid_from=date(2015, 1, 1),
        valid_to=date(2016, 12, 31),
    )
    await _archive(db_session, sos, ODD_2015, _house_csv((30, 2, "Teri Hickel", "(Prefers GOP)")))

    outcome = await sweep_house_winners(db_session)

    assert outcome.missing == []
    assert (2015, 30, "Position 2") in outcome.mismatched


async def test_non_seat_representative_role_is_ignored(db_session, usa_wa):
    """A ``state_representative`` Role with a title key (not a ``seat:house:ld-`` key) must not
    pollute the occupant cohort — the LD30 winner still reads as unseated."""
    _jurisdiction, anchors, sos = await _setup(db_session, usa_wa, LD30_BIENNIUM)
    person = Person(source="usa_wa_legislature", source_id="777", name_full="Speaker")
    db_session.add(person)
    await db_session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id="role:house:speaker",  # a title-keyed role, not a seat
        organization_id=anchors.house_id,
        name="Speaker",
        role_type="state_representative",
        jurisdiction_id=None,
        qualifier=None,
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        Assignment(
            source="usa_wa_legislature",
            source_id="777:role:house-speaker:x",
            person_id=person.id,
            role_id=role.id,
            valid_from=date(2015, 1, 1),
            valid_to=None,
            is_active=True,
        )
    )
    await db_session.flush()

    occupants = await house_occupants(db_session)
    assert occupants == {}  # the title-keyed role is excluded


async def test_sweep_skips_an_odd_year_with_no_house_special(db_session, usa_wa):
    """An archived odd year whose results wire carries no House Rep race (a Senate-only special —
    e.g. 2025, Hunt LD5) has zero House winners and is skipped cleanly, not crashed on."""
    _jurisdiction, _anchors, sos = await _setup(db_session, usa_wa)
    senate_only = (
        b'"Race","Candidate","Party"\r\n'
        b'"LEGISLATIVE DISTRICT 5 - State Senator","Victoria Hunt","(Prefers Democratic Party)"\r\n'
    )
    await _archive(db_session, sos, ODD_2025, senate_only)

    outcome = await sweep_house_winners(db_session)

    assert outcome.missing == [] and outcome.mismatched == []


def test_seat_from_role_source_id_parses_and_rejects():
    """The seat-key parser: a valid House seat key → ``(LD, qualifier)``; a Senate seat, a
    title-keyed role, and a malformed position all → ``None`` (never mis-keyed into the cohort)."""
    assert _seat_from_role_source_id("seat:house:ld-30:position-2") == (30, "Position 2")
    assert _seat_from_role_source_id("seat:senate:ld-5") is None
    assert _seat_from_role_source_id("role:house:speaker") is None
    assert _seat_from_role_source_id("seat:house:ld-x:position-1") is None  # non-numeric LD
    assert _seat_from_role_source_id("seat:house:ld-30:position-3") is None  # not a WA position


def test_sweep_exit_code_contract():
    """The sweep exit contract (#119): 1 only under ``--strict`` with a missing seat, else 0 (a
    historical gap is not a daily-gate failure). The pure, unit-testable operator contract."""
    assert sweep_exit_code(strict=True, missing_count=2) == 1
    assert sweep_exit_code(strict=True, missing_count=0) == 0  # strict but clean
    assert sweep_exit_code(strict=False, missing_count=2) == 0  # report-only default
    assert sweep_exit_code(strict=False, missing_count=0) == 0


# --- CLI (#179b: the shared job harness) --------------------------------------


def test_main_exit_one_on_a_missing_winner_seat(monkeypatch, capsys):
    """Unchanged daily contract (COMMANDS-SUCCESSION.md): 0 clean / 1 on a missing seat."""
    patch_job_runtime(monkeypatch)

    async def _missing(_session, **_kwargs):
        return HouseCorroborationResult(
            odd_year=2025, winners=2, missing_seats=[("5", "Position 1")]
        )

    with patch.object(corroboration_module, "corroborate_house_winners", _missing):
        code = corroboration_module.main(["--json"])

    assert code == 1
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["job"] == corroboration_module.JOB_SLUG
    assert payload["outcome"] == "failed"
    assert payload["counters"]["missing_seats"] == 1


def test_main_sweep_mode_honours_strict(monkeypatch):
    """#119 report-only default, escalated by --strict — both survive the move."""
    patch_job_runtime(monkeypatch)

    async def _sweep(_session, **_kwargs):
        return HouseSweepOutcome(missing=[(2021, "5", "Position 1")], mismatched=[])

    with patch.object(corroboration_module, "sweep_house_winners", _sweep):
        assert corroboration_module.main(["--sweep-biennia"]) == 0
        assert corroboration_module.main(["--sweep-biennia", "--strict"]) == 1
