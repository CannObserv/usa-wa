"""Phase A SOS harvest (#100) — archive each general election's filing cohort, no normalize."""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from clearinghouse_core.provenance import FetchEvent, RawPayload
from clearinghouse_domain_legislative.identity import Assignment
from usa_wa_adapter_sos.filings import harvest as harvest_module
from usa_wa_adapter_sos.filings.harvest import (
    DEFAULT_ELECTION_CEILING,
    HarvestSummary,
    general_election_years,
    harvest_sos,
)
from usa_wa_adapter_sos.filings.transport import WireFetch


class _FakeSOSClient:
    """A votewa stand-in that can fail a chosen year the way the live source does.

    ``status_fail_years`` → HTTP 500 (what votewa serves for 2020+ since the Power BI
    retirement); ``transport_fail_years`` → a connect timeout; ``db_fail_years`` → a
    SQLAlchemy error, which is *not* an httpx error and must abort the whole sweep.
    """

    def __init__(self, status_fail_years=(), transport_fail_years=(), db_fail_years=()):
        self.calls: list[int] = []
        self._status_fail = set(status_fail_years)
        self._transport_fail = set(transport_fail_years)
        self._db_fail = set(db_fail_years)

    async def fetch_whofiled(self, year):
        self.calls.append(year)
        if year in self._status_fail:
            request = httpx.Request("GET", "https://eledataweb.votewa.gov/Candidates/ExportToExcel")
            raise httpx.HTTPStatusError(
                f"500 for {year}",
                request=request,
                response=httpx.Response(500, request=request),
            )
        if year in self._transport_fail:
            raise httpx.ConnectTimeout(f"connection timed out for {year}")
        if year in self._db_fail:
            raise SQLAlchemyError(f"database went away on {year}")
        body = f"RaceName,BallotName\r\nState Senator,M{year}\r\n".encode()
        return WireFetch(
            records=[{"RaceName": "State Senator"}], wire=body, content_type="text/csv"
        )


def test_general_election_years_are_even_and_inclusive():
    assert general_election_years(2008, 2016) == [2008, 2010, 2012, 2014, 2016]
    assert general_election_years(2009, 2016) == [2010, 2012, 2014, 2016]


async def test_harvest_archives_each_year_without_normalizing(db_session, usa_wa):
    client = _FakeSOSClient()
    summary = await harvest_sos(db_session, years=[2012, 2016], sos_client=client, dry_run=False)

    assert client.calls == [2012, 2016]
    assert summary.cohorts_archived == 2
    assert summary.cohorts_skipped == 0
    resource_ids = {r for (r,) in (await db_session.execute(select(FetchEvent.resource_id))).all()}
    assert resource_ids == {"sos-whofiled:201211", "sos-whofiled:201611"}
    # archive-only — no canonical rows emitted
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0
    assert (await db_session.execute(select(func.count()).select_from(RawPayload))).scalar() == 2


async def test_closed_year_is_cache_hit_on_rerun(db_session, usa_wa):
    client = _FakeSOSClient()
    await harvest_sos(db_session, years=[2016], sos_client=client, dry_run=False)
    # second pass without --force: freshness cache short-circuits the re-fetch
    await harvest_sos(db_session, years=[2016], sos_client=client, dry_run=False)
    assert client.calls == [2016]  # only fetched once


async def test_harvest_is_per_year_resilient(db_session, usa_wa):
    """A year votewa 500s is skipped-and-logged inside its own SAVEPOINT, and the years the
    sweep *reached* still archive — the all-or-nothing sweep discarded them (#169)."""
    client = _FakeSOSClient(status_fail_years=[2020])
    summary = await harvest_sos(db_session, years=[2016, 2020, 2024], sos_client=client)

    assert client.calls == [2016, 2020, 2024]  # all attempted; the 500 didn't abort the sweep
    assert summary.cohorts_archived == 2
    assert summary.cohorts_skipped == 1
    resource_ids = {r for (r,) in (await db_session.execute(select(FetchEvent.resource_id))).all()}
    # 2016 + 2024 persisted; 2020 rolled back to its savepoint, not the whole sweep.
    assert resource_ids == {"sos-whofiled:201611", "sos-whofiled:202411"}


async def test_harvest_survives_transport_error(db_session, usa_wa):
    """A transport error (connect/read timeout, reset) — the likeliest outage symptom against a
    low-QPS government host — is skipped per year like a status error. Regression guard against an
    ``HTTPStatusError``-only except, which would let a timeout roll the whole sweep back."""
    client = _FakeSOSClient(transport_fail_years=[2020])
    summary = await harvest_sos(db_session, years=[2016, 2020, 2024], sos_client=client)

    assert client.calls == [2016, 2020, 2024]
    assert summary.cohorts_archived == 2
    assert summary.cohorts_skipped == 1


async def test_database_error_still_aborts_the_sweep(db_session, usa_wa):
    """The boundary the per-year SAVEPOINT must preserve: a DB/SQLAlchemy error is **not** an
    httpx error, so it is not a skippable year — it aborts, and the CLI exits 1."""
    client = _FakeSOSClient(db_fail_years=[2020])
    try:
        await harvest_sos(db_session, years=[2016, 2020, 2024], sos_client=client)
    except SQLAlchemyError:
        pass
    else:  # pragma: no cover - the assertion below reports the failure
        raise AssertionError("a SQLAlchemyError must abort the sweep, not skip the year")
    assert client.calls == [2016, 2020]  # 2024 never attempted


async def test_harvest_warns_distinctly_on_total_outage(db_session, usa_wa, caplog):
    """When *every* year is skipped, a single distinct warning fires: per-year resilience keeps
    the run exit 0, so ``cohorts_archived=0`` would otherwise read as 'nothing to do' (#169)."""
    client = _FakeSOSClient(status_fail_years=[2016, 2020])
    with caplog.at_level(logging.WARNING):
        summary = await harvest_sos(db_session, years=[2016, 2020], sos_client=client)

    assert summary.cohorts_archived == 0 and summary.cohorts_skipped == 2
    assert "sos_harvest_total_outage" in [r.message for r in caplog.records]

    # a partial outage (some archived) does NOT fire the total-outage signal
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await harvest_sos(
            db_session, years=[2012, 2020], sos_client=_FakeSOSClient(status_fail_years=[2020])
        )
    assert "sos_harvest_total_outage" not in [r.message for r in caplog.records]


async def test_main_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(harvest_module, "configure_logging"):
        code = await harvest_module._main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back(monkeypatch, capsys, test_engine):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    fake = HarvestSummary(years=3, cohorts_archived=3, cohorts_skipped=0, dry_run=True)

    async def _fake_harvest(session, **_kwargs):
        return fake

    with (
        patch.object(harvest_module, "configure_logging"),
        patch.object(harvest_module, "harvest_sos", _fake_harvest),
    ):
        code = await harvest_module._main(["--from-year", "2010", "--to-year", "2014", "--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "cohorts_archived=3" in out
    assert "cohorts_skipped=0" in out
    assert "dry-run, rolled back" in out


async def test_main_caps_the_default_to_year_at_the_election_ceiling(
    monkeypatch, capsys, test_engine
):
    """votewa retired the ``ExportToExcel`` export to Power BI after the 2018 general; 2020+
    returns HTTP 500 permanently. The bare invocation must stop at the ceiling rather than sweep
    into years guaranteed to fail (#169)."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    captured: dict[str, list[int]] = {}

    async def _fake_harvest(session, *, years, **_kwargs):
        captured["years"] = years
        return HarvestSummary(years=len(years), cohorts_archived=0, cohorts_skipped=0, dry_run=True)

    with (
        patch.object(harvest_module, "configure_logging"),
        patch.object(harvest_module, "harvest_sos", _fake_harvest),
    ):
        code = await harvest_module._main(["--dry-run"])

    assert code == 0
    assert DEFAULT_ELECTION_CEILING == 2018
    assert captured["years"][-1] == DEFAULT_ELECTION_CEILING


async def test_main_does_not_cap_an_explicit_to_year(monkeypatch, capsys, test_engine):
    """The ceiling governs the **computed** default only. An explicit ``--to-year`` is an operator
    assertion — a probe of whether votewa ever restores the export — and stays honoured; per-year
    resilience is what makes a wrong one survivable rather than fatal. The two are complementary
    (#169): the ceiling makes the no-flag invocation correct, resilience covers the explicit one."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    captured: dict[str, list[int]] = {}

    async def _fake_harvest(session, *, years, **_kwargs):
        captured["years"] = years
        return HarvestSummary(years=len(years), cohorts_archived=0, cohorts_skipped=0, dry_run=True)

    with (
        patch.object(harvest_module, "configure_logging"),
        patch.object(harvest_module, "harvest_sos", _fake_harvest),
    ):
        await harvest_module._main(["--from-year", "2014", "--to-year", "2024", "--dry-run"])

    assert captured["years"] == [2014, 2016, 2018, 2020, 2022, 2024]


async def test_main_leaves_the_env_rate_limit_alone_without_the_flag(
    monkeypatch, capsys, test_engine
):
    """``--pause-seconds`` defaults to ``None`` so the flag's own default stops overwriting the
    value ``_SOS_LIMITER`` was seeded with from ``USA_WA_SOS_MIN_REQUEST_INTERVAL`` (#169) —
    which made that env var dead config, since this CLI is its only production caller."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])

    async def _fake_harvest(session, **_kwargs):
        return HarvestSummary(years=0, cohorts_archived=0, cohorts_skipped=0, dry_run=True)

    with (
        patch.object(harvest_module, "configure_logging"),
        patch.object(harvest_module, "harvest_sos", _fake_harvest),
        patch.object(harvest_module, "configure_sos_rate_limit") as configure,
    ):
        await harvest_module._main(["--dry-run"])
        assert configure.call_count == 0

        await harvest_module._main(["--dry-run", "--pause-seconds", "2.5"])
        configure.assert_called_once_with(2.5)
