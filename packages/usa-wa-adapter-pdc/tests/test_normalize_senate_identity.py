"""Tests for normalize/senate_identity.py — PDC Senate winner → person_wa_pdc (#75).

The Senate path is identifier-only: each PDC Senate winner is matched to the existing WSL
Senate :class:`Person` (within its LD, by folded surname — single seat per LD) and gains a
`person_wa_pdc` child identifier. No Role/Assignment (WSL's P1b already emits the Senate
seat). Unmatched/absent winners are the robustness signal on WSL.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from usa_wa_adapter_pdc.normalize.house_positions import build_senate_roster
from usa_wa_adapter_pdc.normalize.senate_identity import normalize_senate_identities

from clearinghouse_core.adapter import FetchedPayload
from clearinghouse_domain_legislative.identity import Person, PersonIdentifier


def _sponsor(id_, first, last, *, district, agency="Senate"):
    return {
        "Id": id_,
        "Agency": agency,
        "Party": "D",
        "District": district,
        "FirstName": first,
        "LastName": last,
    }


def _senate_winner(person_id, filer_name, *, ld, party_code="D"):
    return {
        "person_id": person_id,
        "filer_name": filer_name,
        "legislative_district": ld,
        "party_code": party_code,
        "office": "STATE SENATOR",
        "general_election_status": "Won in general",
    }


def _payload(winners):
    return FetchedPayload(
        url="https://data.wa.gov/resource/3h9x-7bvm.json#senate-winners:2024",
        fetched_at=datetime.now(UTC),
        content_type="application/json",
        body=b"[]",
        parsed=winners,
    )


async def _add_wsl_person(session, member_id, name) -> Person:
    row = Person(source="usa_wa_legislature", source_id=str(member_id), name_full=name)
    session.add(row)
    await session.flush()
    return row


async def _run(session, winners, sponsors):
    return await normalize_senate_identities(
        _payload(winners),
        senate_roster=build_senate_roster(sponsors),
        session=session,
    )


async def test_matched_winner_attaches_pdc_identifier(db_session, usa_wa) -> None:
    person = await _add_wsl_person(db_session, "897", "Derek Stanford")
    batch = await _run(
        db_session,
        [_senate_winner("897", "Derek Stanford", ld="01")],
        [_sponsor("897", "Derek", "Stanford", district="1")],
    )
    idents = [e for e in batch.entities if isinstance(e, PersonIdentifier)]
    assert len(idents) == 1
    assert idents[0].scheme == "wa_pdc"
    assert idents[0].value == "897"
    assert idents[0].person_id == person.id
    # Identifier-only: no Role/Assignment rows.
    assert all(isinstance(e, PersonIdentifier) for e in batch.entities)
    assert batch.citations == []


async def test_matched_winner_messy_filer_name(db_session, usa_wa) -> None:
    # PDC's filer_name is inconsistently formatted; the folded surname still matches.
    person = await _add_wsl_person(db_session, "27193", "Jim McCune")
    batch = await _run(
        db_session,
        [_senate_winner("27193", "MCCUNE JAMES G (Jim McCune)", ld="02", party_code="R")],
        [_sponsor("27193", "Jim", "McCune", district="2")],
    )
    idents = [e for e in batch.entities if isinstance(e, PersonIdentifier)]
    assert len(idents) == 1
    assert idents[0].person_id == person.id


async def test_unmatched_winner_logs_unresolved(db_session, usa_wa, caplog) -> None:
    # A PDC Senate winner with no matching WSL senator in the LD — the WSL robustness signal.
    await _add_wsl_person(db_session, "897", "Derek Stanford")
    with caplog.at_level(logging.INFO):
        batch = await _run(
            db_session,
            [_senate_winner("999", "Nemo Nobody", ld="05")],
            [_sponsor("897", "Derek", "Stanford", district="1")],
        )
    assert not [e for e in batch.entities if isinstance(e, PersonIdentifier)]
    assert "pdc_senate_unresolved" in [r.message for r in caplog.records]


async def test_matched_but_person_not_ingested_logs_absent(db_session, usa_wa, caplog) -> None:
    # Roster matches, but the WSL Person row doesn't exist yet (WSL refresh hasn't run).
    with caplog.at_level(logging.WARNING):
        batch = await _run(
            db_session,
            [_senate_winner("897", "Derek Stanford", ld="01")],
            [_sponsor("897", "Derek", "Stanford", district="1")],
        )
    assert not batch.entities
    assert "pdc_senate_person_absent" in [r.message for r in caplog.records]


async def test_incomplete_row_skipped(db_session, usa_wa, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        batch = await _run(
            db_session,
            [_senate_winner("", "No Id", ld="01"), _senate_winner("897", "No LD", ld="")],
            [_sponsor("897", "Derek", "Stanford", district="1")],
        )
    assert not batch.entities
    assert "pdc_senate_row_incomplete" in [r.message for r in caplog.records]


async def test_summary_tally_logged(db_session, usa_wa, caplog) -> None:
    # The run-level robustness tally: one matched senator + one departed-senator miss.
    await _add_wsl_person(db_session, "897", "Derek Stanford")
    with caplog.at_level(logging.INFO):
        await _run(
            db_session,
            [
                _senate_winner("897", "Derek Stanford", ld="01"),
                _senate_winner("999", "Departed Senator", ld="05"),
            ],
            [_sponsor("897", "Derek", "Stanford", district="1")],
        )
    summary = next(r for r in caplog.records if r.message == "pdc_senate_summary")
    assert summary.winners == 2
    assert summary.matched == 1
    assert summary.unresolved == 1


async def test_duplicate_person_id_deduped(db_session, usa_wa) -> None:
    # A senator appearing in both election cohorts (special-election overlap) or a repeated
    # row must not mint two identifiers — the collector dedups by (type, source_id).
    person = await _add_wsl_person(db_session, "897", "Derek Stanford")
    batch = await _run(
        db_session,
        [
            _senate_winner("897", "Derek Stanford", ld="01"),
            _senate_winner("897", "Derek Stanford", ld="01"),
        ],
        [_sponsor("897", "Derek", "Stanford", district="1")],
    )
    idents = [e for e in batch.entities if isinstance(e, PersonIdentifier)]
    assert len(idents) == 1
    assert idents[0].person_id == person.id
