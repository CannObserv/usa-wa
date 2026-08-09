"""Source-agnostic ballot interfaces for the WA seat facts (#189).

The row shapes an *application* consumes and every WA ballot **source** produces: a
:class:`HousePosition` (ballot ``qualifier`` + folded ballot-name keys + party slug), the
within-LD :func:`position_for` lookup that resolves a WSL member to their ballot Position, and
:class:`SenateWinner` — the Senate half of a legislative-results wire (#106 A′), attestation
rather than structure.

This module also carries the seam `docs/ARCHITECTURE.md` describes and #189 asked to be made
real: :class:`HousePositionCohortProvider`, the Protocol a fact package depends on instead of
a concrete SOS provider class. A source's ``normalize`` turns its own wire into
``{LD: [HousePosition]}``; the projector consumes that map without knowing which source
produced it.

The file lived in `usa_wa_adapter_sos` and its docstring already said "source-agnostic" — it
just had no source-agnostic package to live in, so `usa-wa-adapter-pdc` and every SOS module
reached into the SOS *target* package for it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from clearinghouse_domain_legislative.cohorts import CitationTarget


@dataclass(frozen=True)
class HousePosition:
    """One WA House candidacy reduced to the position-lookup fields: the ballot ``qualifier``
    (Position 1/2), the folded ``name_keys`` of the ballot name (the messy side of the match,
    via :func:`~usa_wa_common.names.surname_match_set`), and the ``party_slug`` tiebreak.
    Produced by each source's ``normalize``, consumed by the House-position fact."""

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


#: ``{LD: [HousePosition]}`` for one election year — the map a source's ``normalize`` yields.
HousePositionsByLd = dict[int, list[HousePosition]]


@runtime_checkable
class HousePositionCohortProvider(Protocol):
    """**The seam.** A provider of ``{election_year: {LD: [HousePosition]}}`` plus the
    per-year archived FetchEvent attesting it.

    `docs/ARCHITECTURE.md` says the House-position application "depends on a cohort interface
    …, not on a concrete source", and that swapping which archive feeds the fact is "a one-line
    provider change". Until #189 that interface was informal — duck-typed across six provider
    classes with no shared name — so the fact package's only way to say what it needed was to
    import `usa_wa_adapter_sos`. This Protocol is that sentence, in code.

    Satisfied by `SosResultsCohortProvider` (results.vote.wa.gov) and, since #189,
    `SosFilingCohortProvider` (votewa filings) — whose accessor was named `house_filings`, so
    the two archives the architecture doc presents as interchangeable were in fact **not**
    substitutable for one another. It now carries both names.
    """

    async def citation_events(self) -> Mapping[int, CitationTarget]:
        """``{election_year: (fetch_event_id, fetched_at, resource_id)}`` — the per-year
        provenance a positioned seat span cites."""
        ...

    async def house_positions(self) -> Mapping[int, HousePositionsByLd]:
        """``{election_year: {LD: [HousePosition]}}``, re-parsed offline from the archive."""
        ...


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
