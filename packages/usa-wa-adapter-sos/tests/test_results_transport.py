"""Transport tests — ``results.vote.wa.gov`` ``SOSResultsClient`` (respx-mocked).

Pins the general-election date math, the ``export.html`` → Legislative-CSV traversal (clean +
certification-timestamped filenames), the lowercase redirect follow, the offline re-parse (#56),
and the two distinct failure modes the harvest keys on (no-Legislative-link vs an HTTP error).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from usa_wa_adapter_sos.results.transport import (
    RESULTS_BASE_URL,
    LegislativeExportNotFound,
    SOSResultsClient,
    general_election_date,
    legislative_href,
    parse_legislative_results,
)

_CSV = (
    b'"Race","Candidate","Party","Votes","PercentageOfTotalVotes","JurisdictionName"\r\n'
    b'"LEGISLATIVE DISTRICT 1 - State Representative Pos. 1","Davina Duerr",'
    b'"(Prefers Democratic Party)",55168,69.18,"Legislative"\r\n'
)


def test_general_election_date_first_tue_after_first_mon() -> None:
    # First Tuesday after the first Monday of November, per year.
    assert general_election_date(2024) == "20241105"
    assert general_election_date(2022) == "20221108"
    assert general_election_date(2020) == "20201103"
    assert general_election_date(2012) == "20121106"
    assert general_election_date(2008) == "20081104"


def test_legislative_href_clean_timestamped_and_absent() -> None:
    clean = '<a href="Legislative.html">x</a><a href="export/20241105_Legislative.csv">CSV</a>'
    assert legislative_href(clean) == "export/20241105_Legislative.csv"
    ts = '<a href="export/20121106_Legislative_20121205_1451.csv">CSV</a>'
    assert legislative_href(ts) == "export/20121106_Legislative_20121205_1451.csv"
    assert legislative_href('<a href="export/20241105_Congressional.csv">x</a>') is None


def test_parse_legislative_results_reads_header_and_rows() -> None:
    rows = parse_legislative_results(_CSV)
    assert rows[0]["Race"] == "LEGISLATIVE DISTRICT 1 - State Representative Pos. 1"
    assert rows[0]["Candidate"] == "Davina Duerr"
    assert rows[0]["Party"] == "(Prefers Democratic Party)"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_traverses_index_and_follows_redirect() -> None:
    d = "20241105"
    html = f'<a href="Legislative.html">x</a><a href="export/{d}_Legislative.csv">CSV</a>'
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(
        return_value=httpx.Response(200, text=html)
    )
    # The CSV href 302s to a lowercase path (as live votewa does); httpx follows it.
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export/{d}_Legislative.csv").mock(
        return_value=httpx.Response(
            302, headers={"Location": f"{RESULTS_BASE_URL}/results/{d}/export/{d}_legislative.csv"}
        )
    )
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export/{d}_legislative.csv").mock(
        return_value=httpx.Response(
            200, content=_CSV, headers={"content-type": "application/octet-stream"}
        )
    )

    fetch = await SOSResultsClient().fetch_legislative_results(2024)

    assert fetch.wire == _CSV
    assert fetch.records[0]["Candidate"] == "Davina Duerr"
    assert fetch.records[0]["Race"].endswith("Pos. 1")
    assert parse_legislative_results(fetch.wire) == fetch.records  # #56 offline re-parse


@pytest.mark.asyncio
@respx.mock
async def test_fetch_discovers_timestamped_filename() -> None:
    d = "20121106"
    fname = f"{d}_Legislative_20121205_1451.csv"
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(
        return_value=httpx.Response(200, text=f'<a href="export/{fname}">CSV</a>')
    )
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export/{fname}").mock(
        return_value=httpx.Response(200, content=_CSV)
    )

    fetch = await SOSResultsClient().fetch_legislative_results(2012)
    assert fetch.records[0]["Candidate"] == "Davina Duerr"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_raises_when_no_legislative_link() -> None:
    d = "20241105"
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(
        return_value=httpx.Response(200, text='<a href="export/20241105_Congressional.csv">x</a>')
    )
    with pytest.raises(LegislativeExportNotFound):
        await SOSResultsClient().fetch_legislative_results(2024)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_propagates_http_error_for_unheld_year() -> None:
    d = "20261103"  # Nov 2026 hasn't happened — the index 404s
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await SOSResultsClient().fetch_legislative_results(2026)
