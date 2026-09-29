"""The roster edition check (#421): pure, so both archive tiers share one definition."""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.roster_pdf.edition import (
    RosterRevisionMismatch,
    verify_edition,
)


def _blank_pdf() -> bytes:
    """A minimal one-page PDF with no front matter — the "cannot read the stamp" case."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )


def test_a_newer_edition_refuses_the_old_revision(roster_pdf_bytes) -> None:
    """The message names the stamped edition, so the operator's next command is in the alert."""
    with pytest.raises(RosterRevisionMismatch, match="re-run with --revision 2025-06-05"):
        verify_edition(roster_pdf_bytes, "2019-01-01", url="https://example.test/roster.pdf")


def test_an_unreadable_stamp_warns_and_returns_none() -> None:
    """Unreadable means *unknown*, not *mismatched*: the caller decides whether that blocks."""
    assert verify_edition(_blank_pdf(), "2025-06-05", url="https://example.test/roster.pdf") is None


def test_a_matching_stamp_is_returned(roster_pdf_bytes) -> None:
    url = "https://example.test/roster.pdf"
    assert verify_edition(roster_pdf_bytes, "2025-06-05", url=url) == "2025-06-05"
