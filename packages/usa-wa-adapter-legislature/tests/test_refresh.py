"""Default-tier tests for the refresh entrypoint."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import vcr
from sqlalchemy import select

from clearinghouse_core.provenance import Source
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature import refresh as refresh_module
from usa_wa_adapter_legislature.refresh import (
    biennium_for_date,
    biennium_start_date,
    previous_biennium,
    run_refresh,
)

CASSETTE_DIR = Path(__file__).parent / "cassettes"
CASSETTE = "committee_service_get_active_committees_2025-26.yaml"


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2025, 1, 13), "2025-26"),
        (date(2025, 12, 31), "2025-26"),
        (date(2026, 6, 18), "2025-26"),
        (date(2026, 12, 31), "2025-26"),
        (date(2027, 1, 1), "2027-28"),
        (date(2030, 7, 4), "2029-30"),
    ],
)
def test_biennium_for_date_rolls_on_odd_years(today, expected):
    """WA bienniums start on odd years; even-year dates roll back to the start."""
    assert biennium_for_date(today) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2025-26", date(2025, 1, 1)),
        ("2027-28", date(2027, 1, 1)),
        ("2099-00", date(2099, 1, 1)),
    ],
)
def test_biennium_start_date_is_jan1_of_the_odd_year(label, expected):
    """The window boundary for a rename = the biennium's start (Jan 1 of the odd year).

    WSL exposes no real name-change date, so the boundary is the documented
    biennium-start approximation."""
    assert biennium_start_date(label) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2025-26", "2023-24"),
        ("2027-28", "2025-26"),
        ("2001-02", "1999-00"),
    ],
)
def test_previous_biennium_steps_back_two_years(label, expected):
    """The prior biennium is the rename diff's "before" side."""
    assert previous_biennium(label) == expected


async def test_run_refresh_seeds_source_and_runs_adapter(db_session, usa_wa):
    """The entrypoint lazy-creates Source, bootstraps anchors, runs refresh."""
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with (
        recorder.use_cassette(CASSETTE),
        patch(
            "usa_wa_adapter_legislature.refresh.biennium_for_date",
            return_value="2025-26",
        ),
    ):
        summary = await run_refresh(db_session, biennium="2025-26")

    assert summary.discovered == 1
    assert summary.fetched == 1
    assert summary.upserted_entities == 34
    assert summary.errors == 0

    sources = (await db_session.execute(select(Source))).scalars().all()
    assert len(sources) == 1
    assert sources[0].slug == "usa_wa_legislature"

    committees = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "committee")))
        .scalars()
        .all()
    )
    assert len(committees) == 34


async def test_run_refresh_is_idempotent_on_source_creation(db_session, usa_wa):
    """A second call reuses the existing Source (no duplicate slug violation)."""
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with recorder.use_cassette(CASSETTE):
        await run_refresh(db_session, biennium="2025-26")

    # Second invocation hits the cache (no new SOAP call); Source row is reused.
    await run_refresh(db_session, biennium="2025-26")

    sources = (await db_session.execute(select(Source))).scalars().all()
    assert len(sources) == 1


async def test_run_refresh_raises_when_jurisdiction_missing(db_session):
    """A clean DB without the usa-wa jurisdiction row → explicit error."""
    with pytest.raises(LookupError, match="usa-wa"):
        await run_refresh(db_session, biennium="2025-26")


async def test_main_returns_2_when_database_url_unset(monkeypatch, capsys):
    """Missing DATABASE_URL → stderr message + exit code 2 (config error)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(refresh_module, "configure_logging"):
        # Patched no-op: configure_logging mutates root-logger handlers
        # globally; leaving it untouched would persist a stdout JSON handler
        # for every subsequent test in the session.
        code = await refresh_module._main()
    assert code == 2
    captured = capsys.readouterr()
    assert "DATABASE_URL is not set" in captured.err


async def test_main_returns_1_when_run_refresh_raises(monkeypatch, capsys, test_engine):
    """An exception from run_refresh is caught, logged, and produces exit 1.

    Depends on ``test_engine`` (not ``db_session``) because we only need the
    schema setup side effect — ``_main`` opens its own engine against
    TEST_DATABASE_URL and ``run_refresh`` is patched to raise before any
    queries fire, so a savepointed session would be unused.
    """
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])

    async def boom(*_args, **_kwargs):
        raise RuntimeError("simulated WSL failure")

    with (
        patch.object(refresh_module, "configure_logging"),
        patch.object(refresh_module, "run_refresh", boom),
        patch.object(refresh_module.logger, "exception") as mock_exception,
    ):
        code = await refresh_module._main()

    assert code == 1
    mock_exception.assert_called_once_with("wsl_refresh_failed")
    # The success-path summary line must not have printed.
    captured = capsys.readouterr()
    assert "WSL refresh:" not in captured.out
