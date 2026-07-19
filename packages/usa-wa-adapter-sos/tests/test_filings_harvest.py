"""Phase A SOS harvest (#100) — archive each general election's filing cohort, no normalize."""

from __future__ import annotations

import os
from unittest.mock import patch

from sqlalchemy import func, select
from usa_wa_adapter_sos.filings import harvest as harvest_module
from usa_wa_adapter_sos.filings.harvest import HarvestSummary, general_election_years, harvest_sos
from usa_wa_adapter_sos.filings.transport import WireFetch

from clearinghouse_core.provenance import FetchEvent, RawPayload
from clearinghouse_domain_legislative.identity import Assignment


class _FakeSOSClient:
    def __init__(self):
        self.calls = []

    async def fetch_whofiled(self, year):
        self.calls.append(year)
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


async def test_main_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(harvest_module, "configure_logging"):
        code = await harvest_module._main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_dry_run_rolls_back(monkeypatch, capsys, test_engine):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    fake = HarvestSummary(years=3, cohorts_archived=3, dry_run=True)

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
    assert "dry-run, rolled back" in out
