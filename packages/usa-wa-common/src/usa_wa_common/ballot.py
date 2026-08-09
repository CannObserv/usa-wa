"""Shared ballot interfaces for the WA SOS sources (source-agnostic).

The ``house/`` application consumes these; each SOS **source** produces them. A
:class:`HousePosition` row (ballot ``qualifier`` + folded ballot-name keys + party slug) and the
within-LD :func:`position_for` lookup that resolves a WSL member's clean folded surname + party to
their ballot Position. A source's ``normalize`` turns its own wire into ``{LD: [HousePosition]}``;
the projector consumes that map without knowing which source produced it. Also the shared
``(Prefers X Party)`` party canonicaliser both sources' CSVs carry, and :class:`SenateWinner` —
the Senate half of a legislative-results wire (#106 A′), attestation rather than structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from usa_wa_adapter_legislature.normalize.members import canonicalize_party

#: WA SOS **ballot** party synonyms the WSL canonicaliser doesn't fold — e.g. the ``GOP``
#: abbreviation ``results.vote.wa.gov`` sometimes prints for Republican candidates (audited #101).
_BALLOT_PARTY_SYNONYMS = {"gop": "republican"}


def sos_party_slug(party_name: str | None) -> str | None:
    """Canonicalize a WA SOS ballot party string (``"(Prefers Republican Party)"``) to a party
    slug, reusing the WSL canonicaliser on the embedded token plus a small SOS-ballot synonym map.
    Non-partisan / blank / unrecognised → ``None``."""
    if not party_name:
        return None
    for token in re.split(r"[\s(),]+", party_name):
        if not token:
            continue
        slug = canonicalize_party(token) or _BALLOT_PARTY_SYNONYMS.get(token.lower())
        if slug is not None:
            return slug
    return None


@dataclass(frozen=True)
class HousePosition:
    """One SOS House candidacy reduced to the position-lookup fields: the ballot ``qualifier``
    (Position 1/2), the folded ``name_keys`` of the ballot name (the messy side of the match, via
    :func:`~usa_wa_adapter_pdc.normalize.positions.surname_match_set`), and the ``party_slug``
    tiebreak. Produced by each source's ``normalize``, consumed by ``house/``."""

    qualifier: str
    name_keys: frozenset[str]
    party_slug: str | None


@dataclass(frozen=True)
class SenateWinner:
    """The winning Senate candidacy of one LD in one general election (#106 A′).

    The Senate seat carries no ballot ``qualifier`` (one seat per LD, ``Role.qualifier`` NULL), so
    unlike :class:`HousePosition` this supplies no *structural* fact — it is **attestation**: the
    ballot evidence that a sitting senator was elected (an odd-year special winner such as Hunt,
    LD5, Nov 2025), and the independent signal that a senator seated by an operator succession
    event is corroborated upstream. Consumed by Phase B; produced by any SOS source whose wire
    names Senate contests."""

    ld: int
    ballot_name: str
    name_keys: frozenset[str]
    party_slug: str | None
    votes: int | None


def position_for(
    positions_by_ld: dict[int, list[HousePosition]],
    ld: int,
    folded_last: str,
    party_slug: str | None,
) -> str | None:
    """The ballot ``Position`` qualifier for a WSL member (clean ``folded_last`` + party) in an
    LD, per that election's SOS positions. Candidacies whose ballot-name fold set contains the
    member's surname are considered; if they agree on one position, return it; a surname shared
    across positions is broken by party. Zero-or-ambiguous → ``None`` (never guessed)."""
    hits = [p for p in positions_by_ld.get(ld, []) if folded_last in p.name_keys]
    positions = {p.qualifier for p in hits}
    if len(positions) == 1:
        return next(iter(positions))
    if len(positions) > 1 and party_slug is not None:
        by_party = {p.qualifier for p in hits if p.party_slug == party_slug}
        if len(by_party) == 1:
            return next(iter(by_party))
    return None
