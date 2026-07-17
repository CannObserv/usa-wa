"""Pure SOS filing → House-position primitives (#100).

Projects the votewa candidate-filing CSV rows into a within-LD House **position lookup**: given
a WSL member's clean folded surname + party (both known at PDC-match time), return the ballot
``Position 1``/``Position 2`` qualifier PDC's dataset omitted before 2018. No DB, no session.

The lookup is the join PDC can't make on its own: PDC gives us *who won* (name + LD + party,
matched to a WSL :class:`Person`); SOS gives us *which position* that person's seat was. We key
on the **WSL** member's folded surname (clean) tested against the SOS ballot name's fold set
(messy — reusing PDC's :func:`surname_match_set`), disambiguated by party. A zero-or-ambiguous
result returns ``None`` (never guessed) — symmetric with :func:`match_house_member`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from usa_wa_adapter_pdc.normalize.positions import canonical_position, surname_match_set

from usa_wa_adapter_legislature.normalize.members import canonicalize_party

#: votewa ``RaceName`` for a House seat, carrying the ballot position digit.
_HOUSE_RACE_RE = re.compile(r"^State Representative Pos\.?\s*(\d)\b", re.IGNORECASE)

#: A ``RaceJurisdictionName`` like ``"Legislative District 15"`` → the LD number.
_LD_RE = re.compile(r"Legislative District\s+(\d+)", re.IGNORECASE)


def house_position_qualifier(race_name: str) -> str | None:
    """Map a votewa House ``RaceName`` to the PM seat qualifier — ``"State Representative
    Pos. 1"`` → ``"Position 1"``. A non-House / malformed race → ``None``."""
    match = _HOUSE_RACE_RE.match(race_name.strip())
    return canonical_position(match.group(1)) if match else None


def filing_ld(race_jurisdiction_name: str) -> int | None:
    """Parse the LD number from a votewa ``RaceJurisdictionName``; ``None`` if absent."""
    match = _LD_RE.search(race_jurisdiction_name or "")
    return int(match.group(1)) if match else None


def sos_party_slug(party_name: str | None) -> str | None:
    """Canonicalize a votewa ``PartyName`` (``"(Prefers Republican Party)"``) to a party slug,
    reusing the WSL canonicaliser on the embedded party token. Non-partisan / blank → ``None``."""
    if not party_name:
        return None
    for token in re.split(r"[\s(),]+", party_name):
        slug = canonicalize_party(token)
        if slug is not None:
            return slug
    return None


@dataclass(frozen=True)
class HouseFiling:
    """One votewa House candidate filing, reduced to the position-lookup fields: the ballot
    ``qualifier`` (Position 1/2), the folded name keys of the ballot name (the messy side of the
    match, via :func:`surname_match_set`), and the party slug (the tiebreak)."""

    qualifier: str
    name_keys: frozenset[str]
    party_slug: str | None


def build_house_filings(rows: list[dict[str, Any]]) -> dict[int, list[HouseFiling]]:
    """Group a votewa cohort's **House** filing rows by LD → ``[HouseFiling]``.

    Only rows that are a State Representative race with a parseable LD + position + ballot name
    participate (Senate, statewide, judicial, and malformed rows are skipped)."""
    roster: dict[int, list[HouseFiling]] = {}
    for row in rows:
        qualifier = house_position_qualifier(row.get("RaceName") or "")
        ld = filing_ld(row.get("RaceJurisdictionName") or "")
        name_keys = surname_match_set(row.get("BallotName") or "")
        if qualifier is None or ld is None or not name_keys:
            continue
        roster.setdefault(ld, []).append(
            HouseFiling(
                qualifier=qualifier,
                name_keys=frozenset(name_keys),
                party_slug=sos_party_slug(row.get("PartyName")),
            )
        )
    return roster


def position_for(
    filings_by_ld: dict[int, list[HouseFiling]],
    ld: int,
    folded_last: str,
    party_slug: str | None,
) -> str | None:
    """The ballot ``Position`` qualifier for a WSL member (clean ``folded_last`` + party) in an
    LD, per that election's SOS filings. Candidates whose ballot-name fold set contains the
    member's surname are considered; if they agree on one position, return it; a surname shared
    across positions is broken by party. Zero-or-ambiguous → ``None`` (never guessed)."""
    hits = [f for f in filings_by_ld.get(ld, []) if folded_last in f.name_keys]
    positions = {f.qualifier for f in hits}
    if len(positions) == 1:
        return next(iter(positions))
    if len(positions) > 1 and party_slug is not None:
        by_party = {f.qualifier for f in hits if f.party_slug == party_slug}
        if len(by_party) == 1:
            return next(iter(by_party))
    return None
