"""Daily Phase-A archive refresh for the SOS results source (#201).

The half `usa_wa_facts_seats.house.refresh` used to run in-process: archive every results
cohort the current biennium's membership can be decided by (#106), forced past the freshness
TTL for daily determinism, each cohort in its own SAVEPOINT. It lives with the source now —
the fact keeps only the rebuild — so `usa-wa-facts-seats` no longer names a transport.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
from sqlalchemy import select

from clearinghouse_core import job as job_module
from clearinghouse_core.job import EXIT_DEGRADED
from clearinghouse_core.provenance import FetchEvent
from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_sos.results import archive_refresh as refresh_module
from usa_wa_adapter_sos.results.archive_refresh import refresh_archive
from usa_wa_adapter_sos.results.harvest import HarvestSummary
from usa_wa_adapter_sos.results.transport import LegislativeExportNotFound, WireFetch

BIENNIUM = "2025-26"


class _FakeResultsClient:
    def __init__(self, *, fail_years=(), absent_years=()):
        self.calls: list[int] = []
        self._fail = set(fail_years)
        self._absent = set(absent_years)

    async def fetch_legislative_results(self, election_year):
        self.calls.append(election_year)
        if election_year in self._fail:
            raise httpx.ConnectTimeout(f"connection timed out for {election_year}")
        if election_year in self._absent:
            raise LegislativeExportNotFound(f"no Legislative CSV for {election_year}")
        body = (
            b'"Race","Candidate"\r\n'
            b'"LEGISLATIVE DISTRICT 42 - State Representative Pos. 1","M'
            + str(election_year).encode()
            + b'"\r\n'
        )
        return WireFetch(records=[], wire=body, content_type="application/octet-stream")


async def _resource_ids(session):
    return {
        r
        for (r,) in (
            await session.execute(
                select(FetchEvent.resource_id).where(
                    FetchEvent.resource_id.like("sos-legresults:%")
                )
            )
        ).all()
    }


async def test_refresh_archives_every_decisive_cohort(db_session, usa_wa):
    """#106: both generals a biennium's membership can be decided by — the even seating year
    and the odd mid-biennium special — seating year first."""
    client = _FakeResultsClient()

    summary = await refresh_archive(db_session, biennium=BIENNIUM, results_client=client)

    assert client.calls == [2024, 2025]
    assert summary.cohorts_archived == 2
    assert await _resource_ids(db_session) == {
        "sos-legresults:20241105",
        "sos-legresults:20251104",
    }


async def test_refresh_forces_past_the_freshness_ttl(db_session, usa_wa):
    """Daily determinism (the pre-split behaviour): the refresh re-pulls inside the Source's
    cache TTL rather than cache-hitting, so the day's archive is the day's wire. ``--force`` is
    the ARCHIVE half's flag — the rebuild has no cache to bypass."""
    client = _FakeResultsClient()

    await refresh_archive(db_session, biennium=BIENNIUM, results_client=client)
    await refresh_archive(db_session, biennium=BIENNIUM, results_client=client)

    assert client.calls == [2024, 2025, 2024, 2025]  # re-fetched, not cache-hit


async def test_refresh_respects_an_explicit_no_force(db_session, usa_wa):
    """``force=False`` leaves the freshness TTL in charge — the second cycle cache-hits."""
    client = _FakeResultsClient()

    await refresh_archive(db_session, biennium=BIENNIUM, results_client=client, force=False)
    await refresh_archive(db_session, biennium=BIENNIUM, results_client=client, force=False)

    assert client.calls == [2024, 2025]  # second cycle served from the freshness cache


async def test_refresh_survives_an_unserved_odd_year_cohort(db_session, usa_wa, caplog):
    """An odd-year cohort 404s from January until that November's election is certified. It
    archives in its OWN SAVEPOINT and is skipped-and-logged at **INFO** — a daily WARNING here
    would page the operator every morning for eleven months (this project alerts on WARNING
    rises, #85)."""
    client = _FakeResultsClient(fail_years=[2025])

    with caplog.at_level(logging.INFO):
        summary = await refresh_archive(db_session, biennium=BIENNIUM, results_client=client)

    assert client.calls == [2024, 2025]
    assert summary.cohorts_archived == 1  # the seating cohort still landed
    assert await _resource_ids(db_session) == {"sos-legresults:20241105"}
    skips = [r for r in caplog.records if r.message == "results_cohort_year_skipped"]
    assert [(r.year, r.levelno) for r in skips] == [(2025, logging.INFO)]


async def test_refresh_warns_when_the_seating_cohort_fails(db_session, usa_wa, caplog):
    """The even SEATING cohort is a past election that *should* serve — its failure is a genuine
    WARNING (the seat rebuild is now running on a stale archive)."""
    client = _FakeResultsClient(fail_years=[2024])

    with caplog.at_level(logging.INFO):
        await refresh_archive(db_session, biennium=BIENNIUM, results_client=client)

    skips = [r for r in caplog.records if r.message == "results_cohort_year_skipped"]
    assert [(r.year, r.levelno) for r in skips] == [(2024, logging.WARNING)]


async def test_refresh_treats_a_missing_legislative_csv_as_an_expected_absence(db_session, usa_wa):
    """A general held with no legislative race carries no Legislative CSV (2021, 2023) — an
    expected absence, tallied apart from a source failure."""
    client = _FakeResultsClient(absent_years=[2025])

    summary = await refresh_archive(db_session, biennium=BIENNIUM, results_client=client)

    assert summary.cohorts_archived == 1
    assert summary.cohorts_absent == 1 and summary.cohorts_skipped == 0


async def test_refresh_defaults_to_the_current_biennium(db_session, usa_wa, monkeypatch, caplog):
    monkeypatch.delenv("USA_WA_BIENNIUM", raising=False)
    client = _FakeResultsClient()

    with caplog.at_level(logging.INFO):
        await refresh_archive(db_session, results_client=client)

    record = next(r for r in caplog.records if r.message == "sos_archive_refresh_complete")
    assert record.biennium == biennium_for_date(datetime.now(UTC).date())


async def test_refresh_warns_on_noncurrent_biennium(db_session, usa_wa, caplog):
    """A stale ``USA_WA_BIENNIUM`` pin redirects the daily archive at closed history — loud,
    because the current cohorts then never refresh."""
    with caplog.at_level(logging.WARNING):
        await refresh_archive(db_session, biennium="2019-20", results_client=_FakeResultsClient())
    assert "sos_archive_refresh_noncurrent_biennium" in [r.message for r in caplog.records]


# --- CLI ----------------------------------------------------------------------


def test_main_requires_database_url(monkeypatch, capsys):
    def _raise(_role="app"):
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    assert refresh_module.main([]) == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_main_exits_degraded_when_no_cohort_could_be_archived(monkeypatch, capsys):
    """Every decisive cohort failed = a whole-source outage on the day's archive, not one bad
    cohort. ``degraded`` (exit 4) so the archive unit's own ``OnFailure=`` fires — the rebuild
    unit stays green and re-derives from the last good archive, which is the point of the
    split."""
    patch_job_runtime(monkeypatch)

    async def _outage(session, **_kwargs):
        return HarvestSummary(
            years=2, cohorts_archived=0, cohorts_absent=0, cohorts_skipped=2, dry_run=False
        )

    with patch.object(refresh_module, "refresh_archive", _outage):
        code = refresh_module.main(["--json"])

    assert code == EXIT_DEGRADED
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["outcome"] == "degraded"
    assert payload["counters"]["cohorts_skipped"] == 2


def test_main_is_not_degraded_by_an_absent_odd_cohort(monkeypatch):
    """The routine mid-biennium state — seating cohort archived, odd cohort not yet certified —
    is a healthy run."""
    patch_job_runtime(monkeypatch)

    async def _partial(session, **_kwargs):
        return HarvestSummary(
            years=2, cohorts_archived=1, cohorts_absent=0, cohorts_skipped=1, dry_run=False
        )

    with patch.object(refresh_module, "refresh_archive", _partial):
        assert refresh_module.main([]) == 0
