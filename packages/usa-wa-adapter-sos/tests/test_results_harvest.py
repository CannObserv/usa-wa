"""Phase A results harvest (#101) — archive each cohort, per-year resilient (no all-or-nothing)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
from sqlalchemy import func, select

from clearinghouse_core import job as job_module
from clearinghouse_core.job import EXIT_DEGRADED
from clearinghouse_core.provenance import FetchEvent
from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.identity import Assignment
from usa_wa_adapter_sos.results import harvest as harvest_module
from usa_wa_adapter_sos.results.harvest import (
    HarvestSummary,
    general_election_years,
    harvest_results,
)
from usa_wa_adapter_sos.results.transport import LegislativeExportNotFound, WireFetch


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


def test_general_election_years_include_odd_years():
    """WA holds a general election EVERY November (#106): an odd-year general seats legislators
    via specials (Hunt won the LD5 Senate seat in Nov 2025), so an even-only sweep never archives
    their ballot evidence."""
    assert general_election_years(2008, 2013) == [2008, 2009, 2010, 2011, 2012, 2013]
    assert general_election_years(2009, 2011) == [2009, 2010, 2011]


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
    assert summary.cohorts_archived == 2 and summary.cohorts_absent == 1
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


async def test_absent_legislative_csv_is_not_an_outage(db_session, usa_wa, caplog):
    """A year whose general was held but ran no legislative race (2021 + 2023 — no specials) has
    no Legislative CSV in its export index. That is an **expected** outcome once the sweep covers
    odd years (#106), not a source failure: it is tallied separately and must not fire the
    whole-source outage warning even when every reached year is such a year."""
    client = _FakeResultsClient(fail_years=[2021, 2023])
    with caplog.at_level(logging.WARNING):
        summary = await harvest_results(db_session, years=[2021, 2023], results_client=client)

    assert summary.cohorts_archived == 0
    assert summary.cohorts_absent == 2 and summary.cohorts_skipped == 0
    assert "results_harvest_total_outage" not in [r.message for r in caplog.records]


def test_main_defaults_through_the_current_calendar_year(monkeypatch, capsys):
    """The default ``--to-year`` must be the current calendar year, not the biennium's *seating*
    election year: in 2025-26 the latter is 2024, so the odd-year sweep would stop before the very
    cohort #106 exists to archive."""
    patch_job_runtime(monkeypatch)
    captured: dict[str, list[int]] = {}

    async def _fake_harvest(session, *, years, **_kwargs):
        captured["years"] = years
        return HarvestSummary(
            years=len(years), cohorts_archived=0, cohorts_absent=0, cohorts_skipped=0, dry_run=True
        )

    with (
        patch.object(harvest_module, "harvest_results", _fake_harvest),
    ):
        code = harvest_module.main(["--from-year", "2024", "--dry-run"])

    assert code == 0
    assert captured["years"][-1] == datetime.now(UTC).year


def test_main_requires_database_url(monkeypatch, capsys):
    def _raise(_role="app"):
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    assert harvest_module.main([]) == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_main_dry_run_rolls_back(monkeypatch, capsys):
    patch_job_runtime(monkeypatch)
    fake = HarvestSummary(
        years=2, cohorts_archived=2, cohorts_absent=0, cohorts_skipped=0, dry_run=True
    )

    async def _fake_harvest(session, **_kwargs):
        return fake

    with (
        patch.object(harvest_module, "harvest_results", _fake_harvest),
    ):
        code = harvest_module.main(["--from-year", "2020", "--to-year", "2024", "--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "cohorts_archived=2" in out
    assert "dry_run=true" in out  # the harness's own dry-run marker


def test_main_exits_degraded_on_a_total_outage(monkeypatch, capsys):
    """CR #196 finding 22, closed here.

    ``results_harvest_total_outage`` was logged as a WARNING and the CLI still returned
    **0** — the exact "exits 0 having done nothing" failure #178 exists to make visible,
    in the very module ``clearinghouse_core.runs`` names as its example. Its sibling
    ``filings.harvest`` already returned EXIT_DEGRADED for the identical condition, so
    this is bringing two halves of one source into line, not inventing a policy.

    The test is "every year skipped", not "archived == 0": ``archive_only`` returns False
    on a cache hit, so a sweep whose served years all cache-hit is not an outage. And a
    sweep of only race-less years (all ``absent``) is not one either — that is the odd-year
    sweep working.
    """
    patch_job_runtime(monkeypatch)

    async def _outage(session, *, years, **_kwargs):
        return HarvestSummary(
            years=len(years),
            cohorts_archived=0,
            cohorts_absent=0,
            cohorts_skipped=len(years),
            dry_run=False,
        )

    with patch.object(harvest_module, "harvest_results", _outage):
        code = harvest_module.main(["--from-year", "2020", "--to-year", "2024", "--json"])

    assert code == EXIT_DEGRADED
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["outcome"] == "degraded"
    assert payload["counters"]["cohorts_skipped"] == 5  # 2020..2024, odd years included (#106)


def test_main_absent_only_sweep_is_not_degraded(monkeypatch):
    """A general with no legislative race is an expected absence, not an outage (#106)."""
    patch_job_runtime(monkeypatch)

    async def _absent(session, *, years, **_kwargs):
        return HarvestSummary(
            years=len(years),
            cohorts_archived=0,
            cohorts_absent=len(years),
            cohorts_skipped=0,
            dry_run=False,
        )

    with patch.object(harvest_module, "harvest_results", _absent):
        assert harvest_module.main(["--from-year", "2021", "--to-year", "2023"]) == 0


def test_main_leaves_the_env_rate_limit_alone_without_the_flag(monkeypatch, capsys):
    """``--pause-seconds`` defaults to ``None`` so the flag's own default stops overwriting the
    value the central results limiter was seeded with from
    ``USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL`` (#169) — the shape
    :mod:`membership.harvest` already uses."""
    patch_job_runtime(monkeypatch)

    async def _fake_harvest(session, **_kwargs):
        return HarvestSummary(
            years=0, cohorts_archived=0, cohorts_absent=0, cohorts_skipped=0, dry_run=True
        )

    with (
        patch.object(harvest_module, "harvest_results", _fake_harvest),
        patch.object(harvest_module, "configure_results_rate_limit") as configure,
    ):
        harvest_module.main(["--dry-run"])
        assert configure.call_count == 0

        harvest_module.main(["--dry-run", "--pause-seconds", "2.0"])
        configure.assert_called_once_with(2.0)
