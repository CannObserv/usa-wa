"""Phase A results harvest (#101) — archive each cohort, per-year resilient (no all-or-nothing)."""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import httpx
from sqlalchemy import func, select
from usa_wa_adapter_sos.results import harvest as harvest_module
from usa_wa_adapter_sos.results.harvest import (
    HarvestSummary,
    general_election_years,
    harvest_results,
)
from usa_wa_adapter_sos.results.transport import LegislativeExportNotFound, WireFetch

from clearinghouse_core.provenance import FetchEvent
from clearinghouse_domain_legislative.identity import Assignment


class _FakeResultsClient:
    def __init__(self, fail_years=(), transport_fail_years=()):
        self.calls: list[int] = []
        self._fail = set(fail_years)
        self._transport_fail = set(transport_fail_years)

    async def fetch_legislative_results(self, year):
        self.calls.append(year)
        if year in self._fail:
            raise LegislativeExportNotFound(f"no Legislative CSV for {year}")
        if year in self._transport_fail:
            raise httpx.ConnectTimeout(f"connection timed out for {year}")
        body = (
            b'"Race","Candidate"\r\n'
            b'"LEGISLATIVE DISTRICT 1 - State Representative Pos. 1","M'
            + str(year).encode()
            + b'"\r\n'
        )
        return WireFetch(
            records=[{"Race": "x"}], wire=body, content_type="application/octet-stream"
        )


def test_general_election_years_are_even_and_inclusive():
    assert general_election_years(2008, 2016) == [2008, 2010, 2012, 2014, 2016]
    assert general_election_years(2009, 2016) == [2010, 2012, 2014, 2016]


async def test_harvest_archives_each_year(db_session, usa_wa):
    client = _FakeResultsClient()
    summary = await harvest_results(db_session, years=[2020, 2024], results_client=client)

    assert client.calls == [2020, 2024]
    assert summary.cohorts_archived == 2 and summary.cohorts_skipped == 0
    rids = {r for (r,) in (await db_session.execute(select(FetchEvent.resource_id))).all()}
    assert rids == {"sos-legresults:20201103", "sos-legresults:20241105"}
    # archive-only — no canonical rows
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0


async def test_harvest_is_per_year_resilient(db_session, usa_wa):
    """A year the source can't serve is skipped-and-logged in its own SAVEPOINT; the reached
    years still archive — the fix for the all-or-nothing sweep the votewa 500 exposed."""
    client = _FakeResultsClient(fail_years=[2020])
    summary = await harvest_results(db_session, years=[2012, 2020, 2024], results_client=client)

    assert client.calls == [2012, 2020, 2024]  # all attempted
    assert summary.cohorts_archived == 2 and summary.cohorts_skipped == 1
    rids = {r for (r,) in (await db_session.execute(select(FetchEvent.resource_id))).all()}
    # 2012 + 2024 persisted; 2020 rolled back to its savepoint (no event), not the whole sweep.
    assert rids == {"sos-legresults:20121106", "sos-legresults:20241105"}


async def test_harvest_survives_transport_error(db_session, usa_wa):
    """A transport error (connect/read timeout, reset) — the likeliest 'outage' symptom against a
    low-QPS government host — is skipped-and-logged per year like an HTTP status error; the reached
    years still archive. Regression guard: an ``HTTPStatusError``-only except let a timeout escape
    and roll the whole sweep back (the all-or-nothing failure this design exists to prevent)."""
    client = _FakeResultsClient(transport_fail_years=[2020])
    summary = await harvest_results(db_session, years=[2012, 2020, 2024], results_client=client)

    assert client.calls == [2012, 2020, 2024]  # all attempted, timeout didn't abort the sweep
    assert summary.cohorts_archived == 2 and summary.cohorts_skipped == 1
    rids = {r for (r,) in (await db_session.execute(select(FetchEvent.resource_id))).all()}
    assert rids == {"sos-legresults:20121106", "sos-legresults:20241105"}


async def test_harvest_warns_distinctly_on_total_outage(db_session, usa_wa, caplog):
    """When *every* reached year is skipped (a whole-source outage, not one bad year), a single
    distinct warning fires so the run doesn't read as 'nothing to do'. Per-year resilience keeps
    the harvest exit 0 (no year crashed it), but the whole-source failure stays loud in the logs."""
    client = _FakeResultsClient(transport_fail_years=[2012, 2024])
    with caplog.at_level(logging.WARNING):
        summary = await harvest_results(db_session, years=[2012, 2024], results_client=client)

    assert summary.cohorts_archived == 0 and summary.cohorts_skipped == 2
    messages = [r.message for r in caplog.records]
    assert "results_harvest_total_outage" in messages
    # a partial outage (some archived) does NOT fire the total-outage signal
    ok_client = _FakeResultsClient(transport_fail_years=[2012])
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await harvest_results(db_session, years=[2012, 2024], results_client=ok_client)
    assert "results_harvest_total_outage" not in [r.message for r in caplog.records]


async def test_main_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(harvest_module, "configure_logging"):
        code = await harvest_module._main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back(monkeypatch, capsys, test_engine):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    fake = HarvestSummary(years=2, cohorts_archived=2, cohorts_skipped=0, dry_run=True)

    async def _fake_harvest(session, **_kwargs):
        return fake

    with (
        patch.object(harvest_module, "configure_logging"),
        patch.object(harvest_module, "harvest_results", _fake_harvest),
    ):
        code = await harvest_module._main(["--from-year", "2020", "--to-year", "2024", "--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "archived=2" in out
    assert "dry-run, rolled back" in out
