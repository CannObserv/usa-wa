"""Tests for normalize/committees.py — SOAP committee dict → canonical Org."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from ulid import ULID as _ULID

from clearinghouse_core.adapter import FetchedPayload
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.committees import normalize_committees


def _payload(committees: list[dict]) -> FetchedPayload:
    return FetchedPayload(
        url="https://wslwebservices.leg.wa.gov/CommitteeService.asmx",
        fetched_at=datetime(2026, 6, 18, tzinfo=UTC),
        content_type="application/json",
        body=json.dumps(committees).encode("utf-8"),
        http_status=200,
    )


@pytest.fixture
def anchors() -> BootstrapAnchors:
    return BootstrapAnchors(
        legislature_id=_ULID(),
        house_id=_ULID(),
        senate_id=_ULID(),
        biennium_session_id=_ULID(),
        regular_session_ids={2025: _ULID(), 2026: _ULID()},
    )


@pytest.fixture
def jurisdiction_id() -> _ULID:
    return _ULID()


def _house_committee() -> dict:
    return {
        "Id": 31649,
        "Name": "Agriculture & Natural Resources",
        "LongName": "House Committee on Agriculture & Natural Resources",
        "Agency": "House",
        "Acronym": "AGNR",
        "Phone": "(360) 786-7292",
    }


def _senate_committee() -> dict:
    return {
        "Id": 31700,
        "Name": "Ways & Means",
        "LongName": "Senate Committee on Ways & Means",
        "Agency": "Senate",
        "Acronym": "WM",
        "Phone": "(360) 786-7715",
    }


async def test_normalize_house_committee_maps_to_canonical_row(anchors, jurisdiction_id):
    """Field mapping: Id → source_id, LongName → name, Phone → phone, etc."""
    batch = await normalize_committees(
        _payload([_house_committee()]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )

    assert len(batch.entities) == 1
    org = batch.entities[0]
    assert isinstance(org, Organization)
    assert org.source == "usa_wa_legislature"
    assert org.source_id == "31649"
    assert org.name == "House Committee on Agriculture & Natural Resources"
    assert org.short_name == "Agriculture & Natural Resources"
    assert org.acronym == "AGNR"
    assert org.phone == "(360) 786-7292"
    assert org.org_type == "committee"
    assert org.jurisdiction_id == jurisdiction_id
    assert org.parent_organization_id == anchors.house_id


async def test_normalize_senate_committee_attaches_to_senate(anchors, jurisdiction_id):
    """Agency='Senate' resolves the parent chamber to anchors.senate_id."""
    batch = await normalize_committees(
        _payload([_senate_committee()]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert org.parent_organization_id == anchors.senate_id


async def test_normalize_handles_multiple_committees(anchors, jurisdiction_id):
    """A list payload yields one Organization per committee."""
    batch = await normalize_committees(
        _payload([_house_committee(), _senate_committee()]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    assert len(batch.entities) == 2
    assert {o.source_id for o in batch.entities} == {"31649", "31700"}


async def test_normalize_skips_committee_missing_longname(anchors, jurisdiction_id, caplog):
    """Missing LongName → row is dropped and a warning is logged."""
    bad = _house_committee() | {"LongName": None}
    batch = await normalize_committees(
        _payload([bad, _senate_committee()]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    assert len(batch.entities) == 1
    assert batch.entities[0].source_id == "31700"


async def test_normalize_unknown_agency_yields_null_parent(anchors, jurisdiction_id, caplog):
    """An Agency value the anchors don't cover → parent=None (warning logged)."""
    weird = _house_committee() | {"Agency": "Executive", "Id": 99999}
    with caplog.at_level("WARNING"):
        batch = await normalize_committees(
            _payload([weird]),
            anchors=anchors,
            jurisdiction_id=jurisdiction_id,
        )
    [org] = batch.entities
    assert org.parent_organization_id is None
    assert "wsl_committee_unknown_agency" in caplog.text


async def test_normalize_joint_committee_attaches_to_legislature(anchors, jurisdiction_id, caplog):
    """Agency='Joint' parents to the WA Legislature anchor — not a chamber, not NULL.

    Joint committees (cross-chamber, e.g. Joint Transportation) have no single
    chamber parent; the legislature Org is their natural common ancestor. The path
    is expected, so it must not emit the unknown-agency warning.
    """
    joint = _house_committee() | {
        "Agency": "Joint",
        "Id": 12345,
        "Name": "Transportation",
        "LongName": "Joint Transportation Committee",
        "Acronym": "JTC",
    }
    with caplog.at_level("WARNING"):
        batch = await normalize_committees(
            _payload([joint]),
            anchors=anchors,
            jurisdiction_id=jurisdiction_id,
        )
    [org] = batch.entities
    assert org.parent_organization_id == anchors.legislature_id
    assert "wsl_committee_unknown_agency" not in caplog.text


async def test_normalize_strips_phone_whitespace(anchors, jurisdiction_id):
    """Whitespace-padded Phone strings are trimmed for storage stability."""
    padded = _house_committee() | {"Phone": "  (360) 786-7292  "}
    batch = await normalize_committees(
        _payload([padded]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert org.phone == "(360) 786-7292"


async def test_normalize_handles_unicode_in_name(anchors, jurisdiction_id):
    """Unicode in committee names round-trips without coercion."""
    unicode_committee = _house_committee() | {
        "Name": "Salish — Tribal Affairs",
        "LongName": "House Committee on Salish — Tribal Affairs",
    }
    batch = await normalize_committees(
        _payload([unicode_committee]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert org.short_name == "Salish — Tribal Affairs"
    assert org.name == "House Committee on Salish — Tribal Affairs"


async def test_normalize_handles_missing_phone(anchors, jurisdiction_id):
    """Phone is optional — missing or None yields phone=None on the row."""
    no_phone = _house_committee() | {"Phone": None}
    batch = await normalize_committees(
        _payload([no_phone]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert org.phone is None


async def test_normalize_whitespace_only_phone_becomes_none(anchors, jurisdiction_id):
    """All-whitespace Phone collapses to None after strip (no "" vs None ambiguity)."""
    whitespace = _house_committee() | {"Phone": "   "}
    batch = await normalize_committees(
        _payload([whitespace]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert org.phone is None


async def test_normalize_uppercases_acronym(anchors, jurisdiction_id):
    """Acronym is forced uppercase for consistency."""
    lc = _house_committee() | {"Acronym": "agnr"}
    batch = await normalize_committees(
        _payload([lc]),
        anchors=anchors,
        jurisdiction_id=jurisdiction_id,
    )
    [org] = batch.entities
    assert org.acronym == "AGNR"
