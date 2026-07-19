"""Pure votewa **filing** CSV → House-position primitives (#100).

Parses the votewa candidate-filing export rows into the shared ``{LD: [HousePosition]}`` map the
``house/`` application consumes — the position *interface* (the row type + the ``position_for``
lookup) lives in :mod:`usa_wa_adapter_sos.positions`; the parsing here is filings-CSV-specific.
The filing export carries the House position in ``RaceName`` (``State Representative Pos. 1/2``),
the LD in ``RaceJurisdictionName``, the ballot name in ``BallotName``, and party in ``PartyName``.
No DB, no session.
"""

from __future__ import annotations

import re
from typing import Any

from usa_wa_adapter_pdc.normalize.positions import canonical_position, surname_match_set

from usa_wa_adapter_sos.positions import HousePosition, sos_party_slug

#: votewa ``RaceName`` for a House seat, carrying the ballot position digit.
_HOUSE_RACE_RE = re.compile(r"^State Representative Pos\.?\s*(\d)\b", re.IGNORECASE)

#: A ``RaceJurisdictionName`` like ``"Legislative District 15"`` → the LD number.
_LD_RE = re.compile(r"Legislative District\s+(\d+)", re.IGNORECASE)

#: Back-compat alias — the shared position row lives in :mod:`usa_wa_adapter_sos.positions`.
HouseFiling = HousePosition


def house_position_qualifier(race_name: str) -> str | None:
    """Map a votewa House ``RaceName`` to the PM seat qualifier — ``"State Representative
    Pos. 1"`` → ``"Position 1"``. A non-House / malformed race → ``None``."""
    match = _HOUSE_RACE_RE.match(race_name.strip())
    return canonical_position(match.group(1)) if match else None


def filing_ld(race_jurisdiction_name: str) -> int | None:
    """Parse the LD number from a votewa ``RaceJurisdictionName``; ``None`` if absent."""
    match = _LD_RE.search(race_jurisdiction_name or "")
    return int(match.group(1)) if match else None


def build_house_filings(rows: list[dict[str, Any]]) -> dict[int, list[HousePosition]]:
    """Group a votewa cohort's **House** filing rows by LD → ``[HousePosition]``.

    Only rows that are a State Representative race with a parseable LD + position + ballot name
    participate (Senate, statewide, judicial, and malformed rows are skipped)."""
    roster: dict[int, list[HousePosition]] = {}
    for row in rows:
        qualifier = house_position_qualifier(row.get("RaceName") or "")
        ld = filing_ld(row.get("RaceJurisdictionName") or "")
        name_keys = surname_match_set(row.get("BallotName") or "")
        if qualifier is None or ld is None or not name_keys:
            continue
        roster.setdefault(ld, []).append(
            HousePosition(
                qualifier=qualifier,
                name_keys=frozenset(name_keys),
                party_slug=sos_party_slug(row.get("PartyName")),
            )
        )
    return roster
