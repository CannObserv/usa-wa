"""Roster-PDF staging rows (#306): the newest archived revision → member-year rows.

The roster is cumulative (1889→present), so staging parses only the newest
``legroster:<revision>`` wire by ``fetched_at`` — earlier revisions are strict
prefixes of it. The default parser runs the adapter's real extraction
(:mod:`usa_wa_adapter_legislature.roster_pdf`); tests inject a fake.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_legislature.roster_pdf.adapter import ROSTER_RESOURCE_PREFIX
from usa_wa_adapter_legislature.roster_pdf.extraction import extract_pages
from usa_wa_adapter_legislature.roster_pdf.normalize import parse_district_pages_reporting

ROSTER_COLUMNS = [
    "revision",
    "district",
    "chamber",
    "year",
    "order",
    "name",
    "party_token",
    "annotation",
]


def _parse_pdf(wire: bytes) -> list[dict[str, Any]]:
    """The real path: PDF bytes → RosterRecords as dicts (page_number dropped —
    a layout artifact, not a fact about the member)."""
    report = parse_district_pages_reporting(extract_pages(wire))
    rows = []
    for record in report.records:
        row = asdict(record)
        row.pop("page_number", None)
        rows.append(row)
    return rows


def roster_rows(
    store: RawStore, *, parse: Callable[[bytes], list[dict[str, Any]]] = _parse_pdf
) -> list[dict[str, Any]]:
    """Member-year rows from the newest archived roster revision."""
    candidates = {
        rid: entry
        for rid, entry in store.latest().items()
        if rid.startswith(ROSTER_RESOURCE_PREFIX)
    }
    if not candidates:
        return []
    newest_id = max(candidates, key=lambda rid: candidates[rid]["fetched_at"])
    revision = newest_id.removeprefix(ROSTER_RESOURCE_PREFIX)
    wire = store.object_path(candidates[newest_id]["sha256"]).read_bytes()
    return [{"revision": revision, **row} for row in parse(wire)]
