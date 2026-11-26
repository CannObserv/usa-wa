"""SOS staging rows (#307): archived filings + results CSV wires.

Election date rides each resource id (``sos-whofiled:<YYYYMMDD>`` /
``sos-legresults:<YYYYMMDD>``). Columns are the verbatim CSV headers,
snake-cased; both sources corroborate seats rather than mint entities, so
these rows feed the conformed span builders (#309), not the registry.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_sos import parsing
from usa_wa_adapter_sos.filings.resources import WHOFILED_RESOURCE_PREFIX
from usa_wa_adapter_sos.results.resources import (
    ELECTION_RESOURCE_PREFIX,
    LEGRESULTS_RESOURCE_PREFIX,
)
from usa_wa_pipeline.staging.common import PROVENANCE_SCHEMA, first_fetches
from usa_wa_pipeline.staging.common import latest_wires as _latest_wires
from usa_wa_pipeline.staging.common import provenance as _provenance

# the adapters' exported prefixes (#302 CR): a rename upstream must break the
# import, never silently empty this staging model
_WHOFILED_PREFIX = WHOFILED_RESOURCE_PREFIX
_LEGRESULTS_PREFIX = LEGRESULTS_RESOURCE_PREFIX

Parser = Callable[[bytes], list[dict[str, Any]]]

RESULT_SCHEMA = {
    "election_date": "VARCHAR",
    "race": "VARCHAR",
    "candidate": "VARCHAR",
    "party": "VARCHAR",
    "votes": "VARCHAR",
    "percentage_of_total_votes": "VARCHAR",
    "jurisdiction_name": "VARCHAR",
    **PROVENANCE_SCHEMA,
}
RESULT_COLUMNS = list(RESULT_SCHEMA)
FILING_SCHEMA = {
    "election_date": "VARCHAR",
    "ballot_name": "VARCHAR",
    "party_name": "VARCHAR",
    "race_name": "VARCHAR",
    "race_jurisdiction_name": "VARCHAR",
    **PROVENANCE_SCHEMA,
}
FILING_COLUMNS = list(FILING_SCHEMA)
#: #600: one row per distinct platform election JSON, at its first fetch. The first row
#: whose ``is_official_results`` is true dates certification for that general.
ELECTION_SCHEMA = {
    "election_date": "VARCHAR",
    "fetched_at": "VARCHAR",
    "sha256": "VARCHAR",
    "is_official_results": "BOOLEAN",
    "last_updated": "VARCHAR",
    **PROVENANCE_SCHEMA,
}
ELECTION_COLUMNS = list(ELECTION_SCHEMA)


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
                    **_provenance(store, resource_id),
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
                    **_provenance(store, resource_id),
                }
            )
    return rows


def election_rows(store: RawStore) -> list[dict[str, Any]]:
    """Every distinct ``sos-election:<YYYYMMDD>`` wire, at its earliest fetch (#600).

    The flag is read with ``.get`` and kept only when it is a real boolean: a body that
    isn't the election JSON (an HTML error page served 200) asserts nothing, so its row
    carries null rather than a guess."""
    rows = []
    for (resource_id, sha), fetched_at in sorted(
        first_fetches(store, ELECTION_RESOURCE_PREFIX).items()
    ):
        election = _json_object(store.object_path(sha).read_bytes())
        official = election.get("isOfficialResults")
        rows.append(
            {
                "election_date": resource_id.removeprefix(ELECTION_RESOURCE_PREFIX),
                "fetched_at": fetched_at,
                "sha256": sha,
                "is_official_results": official if isinstance(official, bool) else None,
                "last_updated": election.get("lastUpdated"),
                **_provenance(store, resource_id),
            }
        )
    return rows


def _json_object(wire: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(wire)
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}
