"""Pure PDC↔WSL roster matching (#79) — shared by the daily normalizer and the span projector.

Extracted from the retired per-biennium house-positions normalizer so the archive-first span
projector (#79) can reuse the *same* #69/#75 matching logic without a circular import. No DB
access — everything here operates on the injected WSL rosters (``{LD: [entry]}``) built from a
``GetSponsors`` pull.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any

from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.normalize.members import canonicalize_party, district_number
from usa_wa_adapter_pdc.normalize.positions import fold_token, surname_match_set

logger = get_logger(__name__)


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


def _senate_named_ids(sponsor_members: list[dict[str, Any]]) -> set[str]:
    """Member ids of the fully-named Senate rows in one wire — the mover signal (a House row
    sharing one of these ids is a mid-biennium House→Senate mover). Name-blanked stubs excluded."""
    return {
        str(member["Id"])
        for member in sponsor_members
        if member.get("Agency") == "Senate" and (member.get("LastName") or "").strip()
    }


def _parse_house_row(member: dict[str, Any]) -> tuple[str, int] | None:
    """A House row's ``(stripped surname, LD number)`` if it can seat a member, else ``None``
    (non-House agency, name-blanked stub, or unparseable district). The **one** definition of
    "a House row that participates" — shared by :func:`build_house_roster` (uses the values) and
    :func:`house_mover_ids` (checks membership), so the mover set the overlay gates on and the set
    the roster excludes cannot drift (#145)."""
    if member.get("Agency") != "House":
        return None
    last = (member.get("LastName") or "").strip()
    ld = district_number(member.get("District"))
    if not last or ld is None:
        return None
    return last, ld


def house_mover_ids(sponsor_members: list[dict[str, Any]]) -> set[str]:
    """The #105 (a) mover set for one biennium — parseable House rows whose stable ``Id`` also
    appears in a named Senate row (#145). Identical to the set :func:`build_house_roster`
    excludes (both route through :func:`_parse_house_row` + :func:`_senate_named_ids`), exposed so
    the operator overlay can synthesize a mover's closed House tenure (`vacated`) **without**
    re-including them in the roster (which perturbs the #103 elimination and splits the
    backfiller). Pure; no keep_ids exemption — the true mover set, gate-only."""
    senate_ids = _senate_named_ids(sponsor_members)
    return {
        str(member["Id"])
        for member in sponsor_members
        if _parse_house_row(member) is not None and str(member["Id"]) in senate_ids
    }


def build_house_roster(
    sponsor_members: list[dict[str, Any]],
    exclude_ids: AbstractSet[str] = frozenset(),
    keep_ids: AbstractSet[str] = frozenset(),
) -> dict[int, list[HouseRosterEntry]]:
    """Group WSL ``GetSponsors`` **House** rows by LD number for the winner match.

    Only House rows with a parseable district + last name participate (Senate rows,
    name-blanked stubs, and blank districts are skipped — they can't seat a House member).

    **Mover exclusion (#105 (a)).** A mid-biennium House→Senate mover keeps a fully-named
    House row under the *same* stable ``Id`` as their Senate row (Alvarado ``34024``, Hunt
    ``35410`` — the same wire identifies the move), so any House row whose ``Id`` also appears
    in a named Senate row is dropped: the LD then reads 2-member and the #103 elimination can
    seat the real appointed replacement. Id-keyed (no name folding), so it survives a mover who
    also changed LDs. Directional by design — WA mid-biennium chamber moves are House→Senate
    appointments; the per-exclusion log line is the tripwire if the reverse ever appears.

    ``exclude_ids`` drops additional member ids the caller has corroborated as stale
    (:func:`usa_wa_adapter_legislature.roster_hygiene.stale_member_ids`, #105 (b)).

    ``keep_ids`` **exempts** ids from BOTH the mover and stale exclusions. Since #145 the House
    builder passes ``event_members − house_mover_ids(...)`` here, so keep_ids serves only the
    **non-mover** event members (a stale/departed member the overlay dates, whose span must be
    built): an operator-touched **mover** is deliberately NOT kept — re-including them would
    re-run the #103 elimination and split the backfiller, so the overlay synthesizes their closed
    House tenure from the ``vacated`` instead (:func:`house_mover_ids` is that gate signal)."""
    senate_ids = _senate_named_ids(sponsor_members)
    roster: dict[int, list[HouseRosterEntry]] = {}
    for member in sponsor_members:
        parsed = _parse_house_row(member)
        if parsed is None:
            continue
        last, ld = parsed
        member_id = str(member["Id"])
        if member_id not in keep_ids and member_id in senate_ids:
            logger.info(
                "house_roster_mover_excluded",
                extra={"member_id": member_id, "member_name": member.get("Name"), "ld": ld},
            )
            continue
        if member_id not in keep_ids and member_id in exclude_ids:
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
