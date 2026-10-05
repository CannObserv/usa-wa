"""Source-agnostic ballot interfaces for the WA seat facts (#189).

The row shapes an *application* consumes and every WA ballot **source** produces: a
:class:`HousePosition` (ballot ``qualifier`` + folded ballot-name keys + party slug), the
within-LD :func:`position_for` lookup that resolves a WSL member to their ballot Position, and
:class:`SenateWinner` — the Senate half of a legislative-results wire (#106 A′), attestation
rather than structure.

A source's ``normalize`` turns its own wire into ``{LD: [HousePosition]}``; the projector
consumes that map without knowing which source produced it. (The cohort-provider Protocol
that named that seam, #189, left with the providers in #412 PR F: the pipeline stages each
source from the raw store instead.)

The file lived in `usa_wa_adapter_sos` and its docstring already said "source-agnostic" — it
just had no source-agnostic package to live in, so `usa-wa-adapter-pdc` and every SOS module
reached into the SOS *target* package for it.
"""

from __future__ import annotations

from dataclasses import dataclass


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
