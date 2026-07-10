"""Phase A PDC harvest (#79) — archive each election year's winner cohorts, no normalize.

Sweeps even election years, archiving ``house-winners:<Y>`` + ``senate-winners:<Y>`` through
the runner's archive-only seam so Phase B can derive era-matched spans offline. Persons/seats
are NOT touched here (era matching needs a roster the harvest doesn't hold).
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from usa_wa_adapter_pdc.harvest_pdc import election_years, harvest_pdc
from usa_wa_adapter_pdc.transport import WireFetch

from clearinghouse_core.provenance import FetchEvent, RawPayload
from clearinghouse_domain_legislative.identity import Assignment


class _FakePDCClient:
    def __init__(self):
        self.house_calls = []
        self.senate_calls = []

    async def fetch_house_winners(self, year):
        self.house_calls.append(year)
        body = json.dumps([{"person_id": f"h{year}"}]).encode()
        return WireFetch(
            records=[{"person_id": f"h{year}"}], wire=body, content_type="application/json"
        )

    async def fetch_senate_winners(self, year):
        self.senate_calls.append(year)
        body = json.dumps([{"person_id": f"s{year}"}]).encode()
        return WireFetch(
            records=[{"person_id": f"s{year}"}], wire=body, content_type="application/json"
        )


def test_election_years_are_even_and_inclusive():
    assert election_years(2010, 2016) == [2010, 2012, 2014, 2016]
    assert election_years(2011, 2016) == [2012, 2014, 2016]  # bumps an odd floor up


async def test_harvest_archives_both_chambers_per_year_without_normalizing(db_session, usa_wa):
    client = _FakePDCClient()
    summary = await harvest_pdc(db_session, years=[2012, 2014], pdc_client=client, dry_run=False)

    assert client.house_calls == [2012, 2014]
    assert client.senate_calls == [2012, 2014]
    assert summary.cohorts_archived == 4  # 2 chambers × 2 years
    resource_ids = {r for (r,) in (await db_session.execute(select(FetchEvent.resource_id))).all()}
    assert resource_ids == {
        "house-winners:2012",
        "house-winners:2014",
        "senate-winners:2012",
        "senate-winners:2014",
    }
    assert (await db_session.execute(select(func.count()).select_from(RawPayload))).scalar() == 4
    # archive-only: no canonical rows
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0


async def test_reharvest_is_cache_hit(db_session, usa_wa):
    client = _FakePDCClient()
    await harvest_pdc(db_session, years=[2012], pdc_client=client, dry_run=False)
    second = await harvest_pdc(db_session, years=[2012], pdc_client=client, dry_run=False)

    assert second.cohorts_archived == 0  # within TTL → cache hit, no re-fetch
    assert client.house_calls == [2012]  # only the first run fetched
