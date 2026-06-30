"""Tests for harvest_committee_meetings.py — backfill sweep + seed freeze (#39)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from clearinghouse_core.seed_manifest import verify
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.committee_seed import deserialize_seed
from usa_wa_adapter_legislature.harvest_committee_meetings import (
    bienniums_in_range,
    harvest_committee_meetings,
)
from usa_wa_adapter_legislature.transport import WireFetch


def test_bienniums_in_range_walks_odd_years_inclusive():
    assert bienniums_in_range("2021-22", "2025-26") == ["2021-22", "2023-24", "2025-26"]
    assert bienniums_in_range("2025-26", "2025-26") == ["2025-26"]


def test_bienniums_in_range_rejects_reversed():
    with pytest.raises(ValueError, match="after"):
        bienniums_in_range("2025-26", "2023-24")


def _ref(committee_id: int, agency: str, name: str, acronym: str) -> dict:
    return {
        "Id": committee_id,
        "Name": name,
        "LongName": f"{agency} {name}",
        "Agency": agency,
        "Acronym": acronym,
        "Phone": None,
    }


class _ScriptedMeetingClient:
    """Returns a different docket per window keyed on the window's begin year."""

    def __init__(self, by_year: dict[int, list[dict]]) -> None:
        self._by_year = by_year

    async def fetch_committee_meetings(self, begin, end) -> WireFetch:  # noqa: ANN001
        records = [
            {"Agency": r["Agency"], "Committees": {"Committee": [r]}}
            for r in self._by_year.get(begin.year, [])
        ]
        return WireFetch(
            records=records, wire=f"<docket y={begin.year}/>".encode(), content_type="text/xml"
        )


async def test_harvest_dedups_cohort_and_freezes_verified_seed(db_session, usa_wa, tmp_path):
    """Two windows: a body present in both dedups to one row; a body present in only
    the first survives in the frozen seed (window-absence is not retirement)."""
    jtc = _ref(-140, "Joint", "Joint Transportation Committee", "JTC")
    leap = _ref(-12, "Other", "Legislative Evaluation & Accountability Program", "LEAP")
    client = _ScriptedMeetingClient({2023: [jtc, leap], 2025: [jtc]})  # LEAP dormant in 2025
    seed_path = tmp_path / "joint_other_committees_seed.json"

    summary = await harvest_committee_meetings(
        db_session,
        bienniums=["2023-24", "2025-26"],
        seed_path=seed_path,
        meeting_client=client,
    )

    assert summary.windows == 2
    assert summary.upserted == 3  # 2023: JTC+LEAP, 2025: JTC (idempotent)
    assert summary.committees == 2  # deduped durable cohort

    # Seed written + sidecars verify against its bytes.
    content = seed_path.read_bytes()
    assert verify(seed_path, content)
    seeded = {c.source_id: c for c in deserialize_seed(content)}
    assert set(seeded) == {"-140", "-12"}  # LEAP persists despite 2025 absence
    assert seeded["-140"].name == "Joint Joint Transportation Committee"  # LongName verbatim

    # The cohort is in the DB as org_type='other'.
    rows = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "other")))
        .scalars()
        .all()
    )
    assert {o.source_id for o in rows} == {"-140", "-12"}


async def test_harvest_dry_run_writes_no_seed(db_session, usa_wa, tmp_path):
    """--dry-run harvests (upserts) but leaves no seed file on disk."""
    jtc = _ref(-140, "Joint", "Joint Transportation Committee", "JTC")
    seed_path = tmp_path / "seed.json"
    summary = await harvest_committee_meetings(
        db_session,
        bienniums=["2025-26"],
        seed_path=seed_path,
        meeting_client=_ScriptedMeetingClient({2025: [jtc]}),
        dry_run=True,
    )
    assert summary.dry_run is True
    assert summary.committees == 1
    assert not seed_path.exists()
