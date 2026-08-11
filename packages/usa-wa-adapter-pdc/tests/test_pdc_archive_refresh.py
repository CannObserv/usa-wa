"""Daily Phase-A archive refresh for the PDC winner cohorts (#201).

The half `usa_wa_facts_seats.pdc.refresh` used to run in-process: archive every winner cohort
the current biennium's membership can be decided by (#121) — both House generals plus the three
staggered/special Senate cohorts — forced past the freshness TTL, each in its own SAVEPOINT. It
lives with the source now; the fact keeps only the identifier re-drive.
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
from clearinghouse_core.provenance import FetchEvent, Source
from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.identity import Assignment
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_pdc import archive_refresh as refresh_module
from usa_wa_adapter_pdc.archive_refresh import refresh_archive
from usa_wa_adapter_pdc.harvest import ArchiveSummary, biennium_resource_ids
from usa_wa_adapter_pdc.transport import WireFetch

BIENNIUM = "2025-26"

#: The five cohorts 2025-26 membership can be decided by (#121): both House generals (even
#: seating + odd mid-biennium special) and the three Senate cohorts (staggered evens + odd).
DECISIVE_2025_26 = {
    "house-winners:2024",
    "house-winners:2025",
    "senate-winners:2024",
    "senate-winners:2022",
    "senate-winners:2025",
}


class _FakePDCClient:
    def __init__(self, *, fail_years=()):
        self.house_calls: list[int] = []
        self.senate_calls: list[int] = []
        self._fail = set(fail_years)

    async def fetch_house_winners(self, election_year):
        self.house_calls.append(election_year)
        return self._wire("h", election_year)

    async def fetch_senate_winners(self, election_year):
        self.senate_calls.append(election_year)
        return self._wire("s", election_year)

    def _wire(self, chamber, year):
        if year in self._fail:
            raise httpx.ConnectError(f"socrata down for {year}")
        rows = [{"person_id": f"{chamber}{year}"}]
        return WireFetch(
            records=rows, wire=json.dumps(rows).encode(), content_type="application/json"
        )


async def _resource_ids(session):
    return {r for (r,) in (await session.execute(select(FetchEvent.resource_id))).all()}


def test_biennium_resource_ids_name_every_decisive_cohort():
    """The cohort selection is the source's, not the fact's (#201): House generals from the
    shared era helper, Senate from the staggered one."""
    assert set(biennium_resource_ids(BIENNIUM)) == DECISIVE_2025_26
    assert biennium_resource_ids(BIENNIUM)[0] == "house-winners:2024"  # seating cohort first


async def test_refresh_archives_every_decisive_cohort(db_session, usa_wa):
    client = _FakePDCClient()

    summary = await refresh_archive(db_session, biennium=BIENNIUM, pdc_client=client)

    assert client.house_calls == [2024, 2025]
    assert client.senate_calls == [2024, 2022, 2025]
    assert summary.cohorts_archived == 5 and summary.cohorts_skipped == 0
    assert await _resource_ids(db_session) == DECISIVE_2025_26
    # archive-only: no canonical rows are written by the Phase-A half
    assert (await db_session.execute(select(Assignment))).scalars().all() == []


async def test_refresh_forces_past_the_freshness_ttl(db_session, usa_wa):
    """Daily determinism (the pre-split behaviour) — ``--force`` is the ARCHIVE half's flag."""
    client = _FakePDCClient()

    await refresh_archive(db_session, biennium=BIENNIUM, pdc_client=client)
    await refresh_archive(db_session, biennium=BIENNIUM, pdc_client=client)

    assert client.house_calls == [2024, 2025, 2024, 2025]  # re-fetched, not cache-hit


async def test_refresh_survives_one_failing_cohort(db_session, usa_wa, caplog):
    """#121: each cohort archives in its OWN SAVEPOINT — a transient Socrata failure skips one
    cohort, not the whole daily archive."""
    client = _FakePDCClient(fail_years=[2022])

    with caplog.at_level(logging.WARNING):
        summary = await refresh_archive(db_session, biennium=BIENNIUM, pdc_client=client)

    assert summary.cohorts_archived == 4 and summary.cohorts_skipped == 1
    assert await _resource_ids(db_session) == DECISIVE_2025_26 - {"senate-winners:2022"}
    assert "pdc_cohort_skipped" in [r.message for r in caplog.records]


async def test_refresh_completion_log_carries_the_decisive_year_lists(db_session, usa_wa, caplog):
    """#121 CR-3, kept across the split: the completion line self-describes the five-cohort
    cycle, so a ``cohorts_archived`` shortfall is triageable from one line."""
    with caplog.at_level(logging.INFO):
        await refresh_archive(db_session, biennium=BIENNIUM, pdc_client=_FakePDCClient())

    record = next(r for r in caplog.records if r.message == "pdc_archive_refresh_complete")
    assert record.house_years == [2024, 2025]
    assert record.senate_years == (2024, 2022, 2025)


async def test_refresh_defaults_to_the_current_biennium(db_session, usa_wa, monkeypatch, caplog):
    monkeypatch.delenv("USA_WA_BIENNIUM", raising=False)

    with caplog.at_level(logging.INFO):
        summary = await refresh_archive(db_session, pdc_client=_FakePDCClient())

    record = next(r for r in caplog.records if r.message == "pdc_archive_refresh_complete")
    assert record.biennium == biennium_for_date(datetime.now(UTC).date())
    assert summary.cohorts_archived == 5


async def test_refresh_reuses_the_existing_source(db_session, usa_wa):
    """Provisioning moved with the archive half — two cycles get-or-create one Source row."""
    for _ in range(2):
        await refresh_archive(db_session, biennium=BIENNIUM, pdc_client=_FakePDCClient())

    sources = (
        (await db_session.execute(select(Source).where(Source.slug == "usa_wa_pdc")))
        .scalars()
        .all()
    )
    assert len(sources) == 1
    assert sources[0].kind == "rest"


async def test_refresh_warns_on_noncurrent_biennium(db_session, usa_wa, caplog):
    with caplog.at_level(logging.WARNING):
        await refresh_archive(db_session, biennium="2019-20", pdc_client=_FakePDCClient())
    assert "pdc_archive_refresh_noncurrent_biennium" in [r.message for r in caplog.records]


# --- CLI ----------------------------------------------------------------------


def test_main_requires_database_url(monkeypatch, capsys):
    def _raise(_role="app"):
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    assert refresh_module.main([]) == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_main_exits_degraded_when_no_cohort_could_be_archived(monkeypatch, capsys):
    """Every cohort failed = a Socrata outage, not one flaky cohort. ``degraded`` (exit 4) so
    the archive unit alerts on its own, while the rebuild unit re-derives from the last good
    archive. Pre-split this exited 0 with a WARNING nothing consumed."""
    patch_job_runtime(monkeypatch)

    async def _outage(session, **_kwargs):
        return ArchiveSummary(cohorts=5, cohorts_archived=0, cohorts_skipped=5)

    with patch.object(refresh_module, "refresh_archive", _outage):
        code = refresh_module.main(["--json"])

    assert code == EXIT_DEGRADED
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["outcome"] == "degraded"
    assert payload["counters"]["cohorts_skipped"] == 5


def test_main_is_not_degraded_by_one_failed_cohort(monkeypatch):
    patch_job_runtime(monkeypatch)

    async def _partial(session, **_kwargs):
        return ArchiveSummary(cohorts=5, cohorts_archived=4, cohorts_skipped=1)

    with patch.object(refresh_module, "refresh_archive", _partial):
        assert refresh_module.main([]) == 0
