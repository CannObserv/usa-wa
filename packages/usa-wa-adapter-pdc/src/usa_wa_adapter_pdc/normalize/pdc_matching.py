"""Pure PDC↔WSL roster matching (#79) — shared by the daily normalizer and the span projector.

Extracted from the retired per-biennium house-positions normalizer so the archive-first span
projector (#79) can reuse the *same* #69/#75 matching logic without a circular import. No DB
access — everything here operates on the injected WSL rosters (``{LD: [entry]}``) built from a
``GetSponsors`` pull.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from usa_wa_adapter_legislature.normalize.members import canonicalize_party, district_number
from usa_wa_adapter_pdc.normalize.positions import fold_token, surname_match_set


@dataclass(frozen=True)
class HouseRosterEntry:
    """One WSL House member for the within-LD match: the stable member id, the folded
    surname tested against a winner's name tokens, and the party for a tiebreak."""

    member_id: str
    folded_last: str
    party_slug: str | None


@dataclass(frozen=True)
class SenateEntry:
    """One WSL Senator for the #74 confirming signal — the stable member id (to cross-link
    a mover's PDC id onto their current Person) + the folded surname (to match a deferred
    House winner who moved to this LD's Senate seat)."""

    member_id: str
    folded_last: str


def build_house_roster(sponsor_members: list[dict[str, Any]]) -> dict[int, list[HouseRosterEntry]]:
    """Group WSL ``GetSponsors`` **House** rows by LD number for the winner match.

    Only House rows with a parseable district + last name participate (Senate rows,
    name-blanked stubs, and blank districts are skipped — they can't seat a House member)."""
    roster: dict[int, list[HouseRosterEntry]] = {}
    for member in sponsor_members:
        if member.get("Agency") != "House":
            continue
        last = (member.get("LastName") or "").strip()
        ld = district_number(member.get("District"))
        if not last or ld is None:
            continue
        roster.setdefault(ld, []).append(
            HouseRosterEntry(
                member_id=str(member["Id"]),
                folded_last=fold_token(last),
                party_slug=canonicalize_party(member.get("Party")),
            )
        )
    return roster


def build_senate_roster(sponsor_members: list[dict[str, Any]]) -> dict[int, list[SenateEntry]]:
    """``{LD: [SenateEntry]}`` — the confirming signal for the #74 replacement inference. A
    deferred House winner who reappears as their LD's sitting Senator is a genuine
    mid-biennium House→Senate mover, which *explains* the vacated House seat; the entry's
    member id lets us cross-link the mover's PDC identity onto their current Person."""
    out: dict[int, list[SenateEntry]] = {}
    for member in sponsor_members:
        if member.get("Agency") != "Senate":
            continue
        last = (member.get("LastName") or "").strip()
        ld = district_number(member.get("District"))
        if not last or ld is None:
            continue
        out.setdefault(ld, []).append(
            SenateEntry(member_id=str(member["Id"]), folded_last=fold_token(last))
        )
    return out


def match_house_member(
    roster: dict[int, list[HouseRosterEntry]],
    ld: int,
    winner_tokens: set[str],
    winner_party: str | None,
) -> HouseRosterEntry | None:
    """Resolve a PDC winner to a WSL House member in its LD: the member whose folded
    surname is among the winner's name tokens; a shared surname is broken by party. A
    zero-or-ambiguous match returns ``None`` (the winner is left unresolved, not guessed)."""
    candidates = [e for e in roster.get(ld, []) if e.folded_last in winner_tokens]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1 and winner_party is not None:
        by_party = [e for e in candidates if e.party_slug == winner_party]
        if len(by_party) == 1:
            return by_party[0]
    return None


def find_confirming_senator(
    filer_name: str, ld: int, senate_roster: dict[int, list[SenateEntry]]
) -> SenateEntry | None:
    """The LD's Senator whose folded surname matches a deferred House winner — the genuine
    mid-biennium House→Senate mover that explains the vacant House seat (#74). Without this
    signal an unmatched winner could be a name-match miss, so we don't infer. Returns the
    single matching Senator (so their id can carry the mover's PDC cross-link), or ``None``
    when there is no unique match."""
    keys = surname_match_set(filer_name)
    matches = [s for s in senate_roster.get(ld, []) if s.folded_last in keys]
    return matches[0] if len(matches) == 1 else None
