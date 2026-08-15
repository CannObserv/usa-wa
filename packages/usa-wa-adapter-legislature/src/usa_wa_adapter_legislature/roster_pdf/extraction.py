"""PDF bytes → word geometry, and the edition's own revision date (#225).

The single boundary where ``pdfplumber`` is used, so :mod:`normalize` stays pure over geometry
and both the adapter (which must verify what it archived) and the cohort provider (which
re-parses offline) can reach it without importing each other.

:func:`extract_revision_date` is what makes the archive key trustworthy. The key
``legroster:<revision>`` is meant to name *the edition whose bytes these are*; if the revision
came only from an operator flag, the next published edition fetched under a stale flag would be
archived under the old key, and every citation minted from it would name an edition that never
attested the fact (CR finding 1).
"""

from __future__ import annotations

import io
import re
from datetime import datetime

import pdfplumber

from usa_wa_adapter_legislature.roster_pdf.normalize import PageWords, Word

#: The document stamps its own edition on the front matter: ``Revision Date: June 5, 2025``.
_REVISION = re.compile(r"Revision\s+Date:\s*([A-Z][a-z]+\s+\d{1,2},\s*\d{4})")

#: How many leading pages to scan for the stamp — it sits on the title page.
_FRONT_MATTER_PAGES = 4


def extract_pages(wire: bytes) -> list[PageWords]:
    """Extract every page's word geometry from the archived PDF bytes.

    The whole document is handed to the parser; it bounds the *by district* section itself from
    the district banners. A hard-coded page range would silently truncate the tail — the
    districts run 1-60 historically, and the section sits at PDF pages 20-154 in the 2025
    revision, which is not where the printed page numbers say it is.
    """
    pages: list[PageWords] = []
    with pdfplumber.open(io.BytesIO(wire)) as pdf:
        for index, page in enumerate(pdf.pages):
            words = [
                Word(text=w["text"], x0=w["x0"], x1=w["x1"], top=w["top"])
                for w in page.extract_words()
            ]
            pages.append(
                PageWords(
                    page_number=index + 1,
                    width=page.width,
                    height=page.height,
                    words=words,
                )
            )
    return pages


def extract_revision_date(wire: bytes) -> str | None:
    """The edition's own ``Revision Date`` as ``YYYY-MM-DD``, or ``None`` if not stamped.

    ``None`` means *unreadable*, not *mismatched* — callers warn and proceed rather than
    refusing to archive a document whose front matter this pattern does not recognise.
    """
    with pdfplumber.open(io.BytesIO(wire)) as pdf:
        for page in pdf.pages[:_FRONT_MATTER_PAGES]:
            match = _REVISION.search(page.extract_text() or "")
            if match:
                return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
    return None
