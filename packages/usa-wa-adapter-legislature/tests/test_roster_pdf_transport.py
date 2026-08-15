"""Roster-PDF transport, coverage claim and adapter (#225)."""

from __future__ import annotations

import hashlib

import httpx
import pytest
import respx

from clearinghouse_core.source_coverage import CoverageStatus
from usa_wa_adapter_legislature.roster_pdf.adapter import (
    ROSTER_RESOURCE_PREFIX,
    RosterPdfAdapter,
    revision_from_resource_id,
    roster_resource_id,
)
from usa_wa_adapter_legislature.roster_pdf.coverage import (
    MEMBER_ROSTER,
    ROSTER_COVERAGE,
    ROSTER_SOURCE_SLUG,
)
from usa_wa_adapter_legislature.roster_pdf.transport import (
    DEFAULT_ROSTER_URL,
    RosterPdfClient,
    RosterUnavailable,
    roster_href,
)


class TestCoverageClaim:
    def test_declares_the_full_verified_span(self) -> None:
        claim = next(c for c in ROSTER_COVERAGE if c.dimension == MEMBER_ROSTER)
        assert claim.source_slug == ROSTER_SOURCE_SLUG
        assert claim.status is CoverageStatus.verified
        assert claim.range_start == "1889"
        assert claim.range_end == "2025"

    def test_span_is_closed_not_open_ended(self) -> None:
        """The document is a snapshot of a revision, so its claim has an explicit ceiling.
        Leaving it open-ended would assert coverage of a biennium the revision cannot see --
        it is stamped June 2025 and never authoritative for the current biennium."""
        claim = next(c for c in ROSTER_COVERAGE if c.dimension == MEMBER_ROSTER)
        assert claim.range_end is not None


class TestResourceId:
    def test_round_trips_the_revision_date(self) -> None:
        assert roster_resource_id("2025-06-05") == f"{ROSTER_RESOURCE_PREFIX}2025-06-05"
        assert revision_from_resource_id(roster_resource_id("2025-06-05")) == "2025-06-05"

    def test_rejects_a_foreign_resource_id(self) -> None:
        with pytest.raises(ValueError):
            revision_from_resource_id("sos-legresults:20241105")


class TestHrefRediscovery:
    """The URL carries an opaque CMS media key that rotates on re-publish, so a 404 means
    *re-discover the href*, not *outage*."""

    def test_finds_the_roster_href_in_an_index_page(self) -> None:
        html = """
        <ul><li><a href="/media/abcd1234/some-other-report.pdf">Other</a></li>
        <li><a href="/media/s4gf4suc/members-of-the-legislature-1889-2025.pdf">Members</a></li>
        </ul>
        """
        assert roster_href(html) == "/media/s4gf4suc/members-of-the-legislature-1889-2025.pdf"

    def test_matches_a_rotated_media_key_and_a_later_edition(self) -> None:
        html = '<a href="/media/ZZZZZZZZ/members-of-the-legislature-1889-2027.pdf">M</a>'
        assert roster_href(html) == "/media/ZZZZZZZZ/members-of-the-legislature-1889-2027.pdf"

    def test_returns_none_when_absent(self) -> None:
        assert roster_href("<a href='/media/x/budget.pdf'>Budget</a>") is None


class TestFetch:
    @respx.mock
    async def test_fetches_and_hashes_the_wire(self) -> None:
        body = b"%PDF-1.7 fake"
        respx.get(DEFAULT_ROSTER_URL).mock(
            return_value=httpx.Response(
                200, content=body, headers={"content-type": "application/pdf"}
            )
        )
        fetched = await RosterPdfClient().fetch_roster()
        assert fetched.wire == body
        assert fetched.sha256 == hashlib.sha256(body).hexdigest()
        assert fetched.content_type == "application/pdf"
        assert fetched.url == DEFAULT_ROSTER_URL

    @respx.mock
    async def test_a_404_triggers_href_rediscovery(self) -> None:
        """The whole point of the discovery step: a rotated media key must self-heal rather
        than fail the harvest forever."""
        moved = "/media/NEWKEY22/members-of-the-legislature-1889-2025.pdf"
        respx.get(DEFAULT_ROSTER_URL).mock(return_value=httpx.Response(404))
        respx.get(RosterPdfClient().index_url).mock(
            return_value=httpx.Response(200, html=f"<a href='{moved}'>Members</a>")
        )
        respx.get(f"https://leg.wa.gov{moved}").mock(
            return_value=httpx.Response(200, content=b"%PDF-1.7 moved")
        )
        fetched = await RosterPdfClient().fetch_roster()
        assert fetched.wire == b"%PDF-1.7 moved"
        assert fetched.url.endswith(moved)

    @respx.mock
    async def test_a_404_with_no_discoverable_href_raises_unavailable(self) -> None:
        respx.get(DEFAULT_ROSTER_URL).mock(return_value=httpx.Response(404))
        respx.get(RosterPdfClient().index_url).mock(
            return_value=httpx.Response(200, html="<a href='/media/x/budget.pdf'>Budget</a>")
        )
        with pytest.raises(RosterUnavailable):
            await RosterPdfClient().fetch_roster()

    @respx.mock
    async def test_a_non_404_error_is_not_swallowed_as_rediscovery(self) -> None:
        """A 500 is an outage; treating it as a rotated key would mask a real failure."""
        respx.get(DEFAULT_ROSTER_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            await RosterPdfClient().fetch_roster()


class TestAdapterShape:
    def test_is_archive_only(self) -> None:
        adapter = RosterPdfAdapter(revision="2025-06-05")
        assert adapter.source_slug == ROSTER_SOURCE_SLUG

    async def test_normalize_refuses(self) -> None:
        """Archive-only, symmetric with the SOS sources: Phase B parses from the archive."""
        with pytest.raises(NotImplementedError):
            await RosterPdfAdapter(revision="2025-06-05").normalize(None)  # type: ignore[arg-type]

    async def test_discover_yields_one_cohort_per_revision(self) -> None:
        adapter = RosterPdfAdapter(revision="2025-06-05")
        refs = [ref async for ref in adapter.discover(None)]
        assert [r.resource_id for r in refs] == [roster_resource_id("2025-06-05")]
