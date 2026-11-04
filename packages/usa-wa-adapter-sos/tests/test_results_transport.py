"""Transport tests — ``results.vote.wa.gov`` ``SOSResultsClient`` (respx-mocked).

Pins the general-election date math, the ``export.html`` → Legislative-CSV traversal (clean +
certification-timestamped filenames), the lowercase redirect follow, the offline re-parse (#56),
and the two distinct failure modes the harvest keys on (no-Legislative-link vs an HTTP error).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from usa_wa_adapter_sos.results.transport import (
    PLATFORM_BASE_URL,
    RESULTS_BASE_URL,
    LegislativeExportNotFound,
    SOSResultsClient,
    general_election_date,
    legislative_href,
    parse_legislative_results,
    workbook_report,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "results_20261103_all_results.xlsx"
_JURISDICTION = "01000000-f977-1a83-1ed4-08ddd69e9e69"
_ALL = "All Results_3c8977b5-5200-47af-bfea-3237dec12f54.xlsx"
_SUMMARY_AND_PRECINCT = "Results_fbdeaae4-2eb5-45e0-9461-0075d8c19985.xlsx"

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


def _election(reports: list[dict[str, object]]) -> dict[str, object]:
    """The slice of the platform's election JSON the client reads."""
    return {
        "jurisdictionId": _JURISDICTION,
        "electionDate": "2026-11-03",
        "publicReportCategories": [{"categoryName": None, "reports": reports}],
    }


def _report(name: str, blob: str) -> dict[str, object]:
    return {"reportName": name, "blobName": blob, "order": 0, "versions": None, "county": None}


def _platform_api(d: str) -> str:
    return f"{PLATFORM_BASE_URL}/results/public/api/elections/washington/{d}"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_propagates_http_error_for_unheld_year() -> None:
    d = "20301105"  # held on neither site: the old index and the platform both 404
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(return_value=httpx.Response(404))
    respx.get(_platform_api(d)).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await SOSResultsClient().fetch_legislative_results(2030)


@pytest.mark.asyncio
@respx.mock
async def test_an_old_site_404_falls_back_to_the_platform_workbook() -> None:
    d = "20261103"  # 2026+ is published only on results.votewa.gov (#575)
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(return_value=httpx.Response(404))
    respx.get(_platform_api(d)).mock(
        return_value=httpx.Response(
            200,
            json=_election(
                [
                    _report("Summary and Precinct Results Excel", _SUMMARY_AND_PRECINCT),
                    _report("All Results Excel", _ALL),
                ]
            ),
        )
    )
    workbook = _FIXTURE.read_bytes()
    cdn = respx.get(
        f"{PLATFORM_BASE_URL}/cdn/results/{_JURISDICTION}/All%20Results_3c8977b5-5200-47af-bfea-3237dec12f54.xlsx"
    ).mock(return_value=httpx.Response(200, content=workbook))

    fetch = await SOSResultsClient().fetch_legislative_results(2026)

    assert cdn.called
    assert fetch.wire == workbook  # the pristine workbook is the archived wire (#54)
    assert fetch.url == _platform_api(d)  # the traversal's real entry point, not the old index
    assert fetch.records[0]["Race"].startswith("Legislative District 1 - ")
    assert parse_legislative_results(fetch.wire) == fetch.records  # #56 offline re-parse


@pytest.mark.asyncio
@respx.mock
async def test_an_old_index_redirected_off_host_falls_back_too() -> None:
    """The old host already 302s /results/current/ to the platform; an unheld date sent the same
    way lands on the platform's HTML shell with no Legislative link. That is "not held here",
    not "a year with no legislative race", so it falls back like a 404 (#575)."""
    d = "20261103"
    landing = f"{PLATFORM_BASE_URL}/results/public/washington/elections/{d}"
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(
        return_value=httpx.Response(302, headers={"Location": landing})
    )
    respx.get(landing).mock(return_value=httpx.Response(200, text="<html><app-root></app-root>"))
    respx.get(_platform_api(d)).mock(
        return_value=httpx.Response(200, json=_election([_report("All Results Excel", _ALL)]))
    )
    workbook = _FIXTURE.read_bytes()
    respx.get(url__startswith=f"{PLATFORM_BASE_URL}/cdn/results/{_JURISDICTION}/").mock(
        return_value=httpx.Response(200, content=workbook)
    )

    fetch = await SOSResultsClient().fetch_legislative_results(2026)

    assert fetch.wire == workbook
    assert fetch.url == _platform_api(d)


@pytest.mark.asyncio
@respx.mock
async def test_an_old_site_fetch_reports_no_url_of_its_own() -> None:
    d = "20241105"
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(
        return_value=httpx.Response(200, text=f'<a href="export/{d}_Legislative.csv">CSV</a>')
    )
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export/{d}_Legislative.csv").mock(
        return_value=httpx.Response(200, content=_CSV)
    )
    # The harvest's export.html URL stays the recorded one, so past provenance doesn't move.
    assert (await SOSResultsClient().fetch_legislative_results(2024)).url is None


@pytest.mark.asyncio
@respx.mock
async def test_an_old_site_server_error_does_not_fall_back() -> None:
    d = "20241105"
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(return_value=httpx.Response(503))
    platform = respx.get(_platform_api(d)).mock(
        return_value=httpx.Response(200, json=_election([]))
    )
    with pytest.raises(httpx.HTTPStatusError):
        await SOSResultsClient().fetch_legislative_results(2024)
    assert not platform.called


@pytest.mark.asyncio
@respx.mock
async def test_a_platform_election_with_no_workbook_raises_not_found() -> None:
    d = "20261103"
    respx.get(f"{RESULTS_BASE_URL}/results/{d}/export.html").mock(return_value=httpx.Response(404))
    respx.get(_platform_api(d)).mock(
        return_value=httpx.Response(200, json=_election([_report("Canvass PDF", "canvass.pdf")]))
    )
    with pytest.raises(LegislativeExportNotFound):
        await SOSResultsClient().fetch_legislative_results(2026)


def test_workbook_report_prefers_all_results_then_any_workbook() -> None:
    both = _election(
        [
            _report("Summary and Precinct Results Excel", _SUMMARY_AND_PRECINCT),
            _report("ALL RESULTS (xlsx)", _ALL),
        ]
    )
    assert workbook_report(both) == _ALL
    renamed = _election([_report("Canvass PDF", "c.pdf"), _report("Results Workbook", "r.XLSX")])
    assert workbook_report(renamed) == "r.XLSX"
    assert workbook_report(_election([])) is None
    assert workbook_report({}) is None
