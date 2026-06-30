"""Tests for normalize/committee_meetings.py — meeting refs → Joint/Other Orgs (#39)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from ulid import ULID as _ULID

from clearinghouse_core.adapter import FetchedPayload
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.committee_meetings import (
    joint_other_refs,
    normalize_committee_meetings,
)


@pytest.fixture
def anchors() -> BootstrapAnchors:
    return BootstrapAnchors(
        legislature_id=_ULID(),
        house_id=_ULID(),
        senate_id=_ULID(),
        biennium_session_id=_ULID(),
        regular_session_ids={2023: _ULID(), 2024: _ULID()},
    )


@pytest.fixture
def jurisdiction_id() -> _ULID:
    return _ULID()


def _meeting(agency: str, committees: list[dict]) -> dict:
    """A meeting dict in the zeep-serialized shape (nested Committees.Committee[])."""
    return {"Agency": agency, "Committees": {"Committee": committees}}


def _payload(meetings: list[dict] | None) -> FetchedPayload:
    return FetchedPayload(
        url="https://wslwebservices.leg.wa.gov/CommitteeMeetingService.asmx#GetCommitteeMeetings",
        fetched_at=datetime(2026, 6, 30, tzinfo=UTC),
        content_type="text/xml; charset=utf-8",
        body=b"<soap:Envelope/>",  # real wire is XML, not JSON — parsed is the source
        parsed=meetings,
        http_status=200,
    )


def _jtc() -> dict:
    return {
        "Id": -140,
        "Name": "Joint Transportation Committee",
        "LongName": "Joint Joint Transportation Committee",
        "Agency": "Joint",
        "Acronym": "JTC",
        "Phone": "(360) 786-7300",
    }


def _leap() -> dict:
    return {
        "Id": -12,
        "Name": "Legislative Evaluation & Accountability Program",
        "LongName": "Other Legislative Evaluation & Accountability Program",
        "Agency": "Other",
        "Acronym": "LEAP",
        "Phone": None,
    }


def _house_ref() -> dict:
    return {
        "Id": 31649,
        "Name": "Finance",
        "LongName": "House Finance",
        "Agency": "House",
        "Acronym": "FIN",
        "Phone": None,
    }


def _by_source_id(entities: list[Any]) -> dict[str, Organization]:
    return {o.source_id: o for o in entities}


async def test_joint_committee_maps_verbatim_to_other_under_legislature(anchors, jurisdiction_id):
    """Joint ref → org_type='other', name=LongName verbatim, parent=legislature anchor."""
    batch = await normalize_committee_meetings(
        _payload([_meeting("Joint", [_jtc()])]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert isinstance(org, Organization)
    assert org.source == "usa_wa_legislature"
    assert org.source_id == "-140"  # negative sentinel kept as natural key
    assert org.name == "Joint Joint Transportation Committee"  # verbatim, not cleaned
    assert org.short_name == "Joint Transportation Committee"
    assert org.org_type == "other"
    assert org.acronym == "JTC"
    assert org.phone == "(360) 786-7300"
    assert org.parent_organization_id == anchors.legislature_id
    assert org.jurisdiction_id == jurisdiction_id


async def test_other_agency_also_parents_to_legislature(anchors, jurisdiction_id):
    """Agency='Other' (LEAP et al.) → org_type='other', parent=legislature anchor."""
    batch = await normalize_committee_meetings(
        _payload([_meeting("Other", [_leap()])]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert org.org_type == "other"
    assert org.parent_organization_id == anchors.legislature_id
    assert org.phone is None


async def test_house_senate_refs_are_skipped(anchors, jurisdiction_id):
    """House/Senate committee refs belong to CommitteeService — never emitted here."""
    batch = await normalize_committee_meetings(
        _payload([_meeting("House", [_house_ref()]), _meeting("Joint", [_jtc()])]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    by_id = _by_source_id(batch.entities)
    assert set(by_id) == {"-140"}  # the House ref (31649) is absent


async def test_refs_dedup_by_id_across_meetings(anchors, jurisdiction_id):
    """A body that met repeatedly yields exactly one Organization (first ref wins)."""
    batch = await normalize_committee_meetings(
        _payload([_meeting("Joint", [_jtc()]), _meeting("Joint", [_jtc()])]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    assert len(batch.entities) == 1


async def test_blank_acronym_collapses_to_none(anchors, jurisdiction_id):
    """An empty/blank Acronym (e.g. the Civic Health committee) → None, not ''."""
    civic = {
        "Id": 35341,
        "Name": "Joint Select Committee on Civic Health",
        "LongName": "Joint Joint Select Committee on Civic Health",
        "Agency": "Joint",
        "Acronym": "   ",
        "Phone": None,
    }
    batch = await normalize_committee_meetings(
        _payload([_meeting("Joint", [civic])]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert org.acronym is None


def test_joint_other_refs_dedup_is_structural_first_wins():
    """Dedup keys on (Agency, Id) and keeps the FIRST ref for an Id verbatim — field
    completeness is the consumer's guard, not this seam's. Pins the documented behavior so a
    malformed-but-first ref claiming the slot is an intentional contract, not an accident.
    (Never hit in WSL data: a body's refs carry identical attributes across its meetings.)"""
    first = {"Id": -140, "Name": "First", "LongName": "Joint First", "Agency": "Joint"}
    second = {"Id": -140, "Name": "Second", "LongName": "Joint Second", "Agency": "Joint"}
    refs = joint_other_refs([_meeting("Joint", [first]), _meeting("Joint", [second])])
    assert set(refs) == {"-140"}
    assert refs["-140"]["Name"] == "First"  # first ref wins


async def test_missing_longname_is_skipped_with_warning(anchors, jurisdiction_id, caplog):
    """A ref without LongName is dropped (no row), and a warning is logged."""
    bad = _jtc() | {"LongName": None}
    with caplog.at_level("WARNING"):
        batch = await normalize_committee_meetings(
            _payload([_meeting("Joint", [bad, _leap()])]),
            anchors=anchors,
            jurisdiction_id=jurisdiction_id,
        )
    by_id = _by_source_id(batch.entities)
    assert set(by_id) == {"-12"}  # LEAP survives; the malformed JTC ref is gone
    assert "wsl_meeting_committee_missing_longname" in caplog.text


async def test_unparsed_payload_yields_empty_batch(anchors, jurisdiction_id, caplog):
    """parsed=None (no derived dicts) → empty batch + warning, never a crash."""
    with caplog.at_level("WARNING"):
        batch = await normalize_committee_meetings(
            _payload(None),
            anchors=anchors,
            jurisdiction_id=jurisdiction_id,
        )
    assert batch.entities == []
    assert "wsl_meetings_payload_unparsed" in caplog.text


async def test_meeting_without_committee_block_is_inert(anchors, jurisdiction_id):
    """A meeting carrying no Committees block contributes nothing (no crash)."""
    batch = await normalize_committee_meetings(
        _payload([{"Agency": "Joint", "Committees": None}, _meeting("Joint", [_jtc()])]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    assert {o.source_id for o in batch.entities} == {"-140"}
