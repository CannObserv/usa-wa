"""PDC staging rows (#307): archived SODA winner wires → per-cohort rows.

Chamber + election year ride the resource id (``house-winners:<year>`` /
``senate-winners:<year>``); the SODA row's identifier trio (``person_id`` —
the value canonical links as the ``wa_pdc`` scheme — plus ``filer_id`` and
``filer_name``) is what the matching tier keys on. The full SODA record
carries ~50 columns; staging keeps the identity- and seat-relevant subset and
never keys on the rest.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_pdc import parsing
from usa_wa_adapter_pdc.harvest import (
    HOUSE_WINNERS_RESOURCE_PREFIX,
    SENATE_WINNERS_RESOURCE_PREFIX,
)
from usa_wa_pipeline.staging.common import PROVENANCE_COLUMNS
from usa_wa_pipeline.staging.common import latest_wires as _latest_wires
from usa_wa_pipeline.staging.common import provenance as _provenance
from usa_wa_pipeline.staging.common import text as _text

# the adapter's exported prefixes (#302 CR): a rename upstream must break the
# import, never silently empty this staging model
_HOUSE_PREFIX = HOUSE_WINNERS_RESOURCE_PREFIX
_SENATE_PREFIX = SENATE_WINNERS_RESOURCE_PREFIX

Parser = Callable[[bytes], list[dict[str, Any]]]

WINNER_COLUMNS = [
    "chamber",
    "election_year",
    "person_id",
    "filer_id",
    "filer_name",
    "party",
    "legislative_district",
    "office",
    "general_election_status",
    "candidacy_id",
    *PROVENANCE_COLUMNS,
]


def winner_rows(
    store: RawStore,
    *,
    parse_house: Parser = parsing.parse_house_winners,
    parse_senate: Parser = parsing.parse_senate_winners,
) -> list[dict[str, Any]]:
    """Both chambers' winner cohorts, one row per (cohort, filer)."""
    rows = []
    for prefix, chamber, parse in (
        (_HOUSE_PREFIX, "house", parse_house),
        (_SENATE_PREFIX, "senate", parse_senate),
    ):
        for resource_id, wire in _latest_wires(store, prefix):
            year = int(resource_id.removeprefix(prefix))
            for record in parse(wire):
                rows.append(
                    {
                        "chamber": chamber,
                        "election_year": year,
                        "person_id": _text(record.get("person_id")),
                        "filer_id": record.get("filer_id"),
                        "filer_name": record.get("filer_name"),
                        "party": record.get("party"),
                        "legislative_district": _text(record.get("legislative_district")),
                        "office": record.get("office"),
                        "general_election_status": record.get("general_election_status"),
                        "candidacy_id": _text(record.get("candidacy_id")),
                        **_provenance(store, resource_id),
                    }
                )
    return rows
