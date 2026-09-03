"""SOS staging rows (#307): archived filings + results CSV wires.

Election date rides each resource id (``sos-whofiled:<YYYYMMDD>`` /
``sos-legresults:<YYYYMMDD>``). Columns are the verbatim CSV headers,
snake-cased; both sources corroborate seats rather than mint entities, so
these rows feed the conformed span builders (#309), not the registry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_sos import parsing
from usa_wa_adapter_sos.filings.adapter import WHOFILED_RESOURCE_PREFIX
from usa_wa_adapter_sos.results.adapter import LEGRESULTS_RESOURCE_PREFIX
from usa_wa_pipeline.staging.common import latest_wires as _latest_wires

# the adapters' exported prefixes (#302 CR): a rename upstream must break the
# import, never silently empty this staging model
_WHOFILED_PREFIX = WHOFILED_RESOURCE_PREFIX
_LEGRESULTS_PREFIX = LEGRESULTS_RESOURCE_PREFIX

Parser = Callable[[bytes], list[dict[str, Any]]]

RESULT_COLUMNS = [
    "election_date",
    "race",
    "candidate",
    "party",
    "votes",
    "percentage_of_total_votes",
    "jurisdiction_name",
]
FILING_COLUMNS = [
    "election_date",
    "ballot_name",
    "party_name",
    "race_name",
    "race_jurisdiction_name",
]


def result_rows(
    store: RawStore, *, parse: Parser = parsing.parse_legislative_results
) -> list[dict[str, Any]]:
    """One row per (election, race, candidate) from the results exports."""
    rows = []
    for resource_id, wire in _latest_wires(store, _LEGRESULTS_PREFIX):
        date = resource_id.removeprefix(_LEGRESULTS_PREFIX)
        for record in parse(wire):
            rows.append(
                {
                    "election_date": date,
                    "race": record.get("Race"),
                    "candidate": record.get("Candidate"),
                    "party": record.get("Party"),
                    "votes": record.get("Votes"),
                    "percentage_of_total_votes": record.get("PercentageOfTotalVotes"),
                    "jurisdiction_name": record.get("JurisdictionName"),
                }
            )
    return rows


def filing_rows(store: RawStore, *, parse: Parser = parsing.parse_whofiled) -> list[dict[str, Any]]:
    """One row per (election, race, filed candidate) from the WhoFiled exports."""
    rows = []
    for resource_id, wire in _latest_wires(store, _WHOFILED_PREFIX):
        date = resource_id.removeprefix(_WHOFILED_PREFIX)
        for record in parse(wire):
            rows.append(
                {
                    "election_date": date,
                    "ballot_name": record.get("BallotName"),
                    "party_name": record.get("PartyName"),
                    "race_name": record.get("RaceName"),
                    "race_jurisdiction_name": record.get("RaceJurisdictionName"),
                }
            )
    return rows
