"""Tests for harvest_committee_meetings.py — backfill sweep + seed freeze (#39)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from clearinghouse_core.seed_manifest import verify
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature import harvest_committee_meetings as harvest_module
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


async def test_seed_is_scoped_to_this_runs_windows(db_session, usa_wa, tmp_path):
    """An org_type='other' row already in the DB but not discovered in the swept windows
    (e.g. added by the daily refresh) is excluded from the seed — the seed reflects this
    run's windows, not the whole DB."""
    await db_session.execute(
        pg_insert(Organization).values(
            source="usa_wa_legislature",
            source_id="-999",
            jurisdiction_id=usa_wa.id,
            name="Pre-existing Joint Body",
            org_type="other",
        )
    )
    jtc = _ref(-140, "Joint", "Joint Transportation Committee", "JTC")
    seed_path = tmp_path / "seed.json"
    summary = await harvest_committee_meetings(
        db_session,
        bienniums=["2025-26"],
        seed_path=seed_path,
        meeting_client=_ScriptedMeetingClient({2025: [jtc]}),
    )
    seeded = {c.source_id for c in deserialize_seed(seed_path.read_bytes())}
    assert seeded == {"-140"}  # -999 was never in the swept window
    assert summary.committees == 1


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


async def test_main_returns_2_when_database_url_unset(monkeypatch, capsys):
    """Missing DATABASE_URL → stderr message + exit 2 (config error)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(harvest_module, "configure_logging"):
        code = await harvest_module._main(
            ["--from-biennium", "2025-26", "--to-biennium", "2025-26"]
        )
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_returns_2_on_reversed_biennium_range(monkeypatch, capsys):
    """A from-biennium after to-biennium is a config error → exit 2 (no DB touched)."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    with patch.object(harvest_module, "configure_logging"):
        code = await harvest_module._main(
            ["--from-biennium", "2025-26", "--to-biennium", "2023-24"]
        )
    assert code == 2
    assert "after" in capsys.readouterr().err


async def test_main_returns_1_when_harvest_raises(monkeypatch, capsys, test_engine):
    """An exception from the harvest is caught, logged, and produces exit 1."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])

    async def boom(*_args, **_kwargs):
        raise RuntimeError("simulated harvest failure")

    with (
        patch.object(harvest_module, "configure_logging"),
        patch.object(harvest_module, "harvest_committee_meetings", boom),
        patch.object(harvest_module.logger, "exception") as mock_exception,
    ):
        code = await harvest_module._main(
            ["--from-biennium", "2025-26", "--to-biennium", "2025-26"]
        )
    assert code == 1
    mock_exception.assert_called_once_with("wsl_committee_harvest_failed")
