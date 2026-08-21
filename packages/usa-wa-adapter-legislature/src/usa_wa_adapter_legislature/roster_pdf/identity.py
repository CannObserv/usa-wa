"""Pre-1991 roster identity resolution (#228, epic #219 Phase 3b). Pure.

The roster carries no member ids: identity must come from the names themselves, and the
design (``docs/specs/2026-08-20-pre-1991-identity-design.md``) settles how. This module is
that design as code, recalibrated by the #252 parse corrections:

* **The identity fold** strips what is not a name before applying the shared
  ``usa-wa-common`` fold: position suffixes (``– 19B`` — seat metadata printed into names on
  the early split districts), parenthetical segments (``(Mrs. Joseph E.)`` marital forms,
  ``(Judy)`` nicknames), quoted nicknames (``“L.L.”`` appears in some of Westfall's listings
  and not others), and honorific tokens. Each class was measured splitting a real person's
  tenure into two folds. Generational suffixes stay — ``Jr`` distinguishes real people.
* **Grouping is fold modulo the checked-in adjudication tables, with no heuristic
  splitter.** The spec's candidate splitter — same-session-year records on different seats —
  measures as **44 chamber movers and roughly zero true simultaneities**: the roster indexes
  rows by *term-start* year, so a mid-term successor legitimately appears under two seats in
  one session year (Ted Haley's 1977 House row and 1977-term Senate appointment are one
  career). A rule that split on it would fork ~44 real people to catch ~0 impostors.
* **Odd evidence refuses; it never merges and never splits silently.** A group whose
  consecutive listing years gap more than :data:`WIDE_GAP_YEARS` with no adjudicated split
  is refused with its subject named — 15 groups measured, adjudicable by hand. The one
  near-certain two-person fold (``elmerejohnston``: an 1899 Populist in LD44 and a 1947-65
  Republican in LD6) is split by :data:`IDENTITY_SPLITS`.
* **A fold crossing the 1991 floor joins the existing WSL member** — the roster key is a
  fallback for identities with no WSL counterpart, never a rival space. The join is
  seat-scoped surname match over the group's 1991+ rows, then the #240 given-name-initial
  guard, then the year-corroboration tie-breaker (**at least two** distinct session years
  *and* strictly more than any rival — without the floor the rule accepts ``1 > 0``, which
  is exactly the #240 shape), then :data:`JOIN_ADJUDICATIONS`. The guard's failure mode
  inverts here relative to #226: a false rejection there refuses an event, here it mints a
  duplicate Person for someone who already has a WSL identity.

**The adjudication tables are versioned data, deliberately in code.** The spec's
re-derivability argument — a rebuild from ``RawPayload`` reproduces the same ids — holds
only because the decisions that are *not* in the archive are checked in beside it.

Nothing here touches a database. The write side belongs to the Phase B builder.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from usa_wa_adapter_legislature.roster_pdf.audit import TERM_YEARS
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.resolve import Seating
from usa_wa_common.names import fold_token, folded_tokens, surname_match_set

#: First session year of the WSL sponsor archive — the identity floor. Records from this
#: year on already have WSL-sourced Persons; the roster mints identities only below it.
ROSTER_IDENTITY_FLOOR = 1991

#: A consecutive-listing gap wider than this refuses the group (absent an adjudicated
#: split). Calibrated on the corrected corpus: every real career break measures ≤ 4 years;
#: the 15 groups beyond 20 are all disjoint-lineage and belong to human adjudication.
WIDE_GAP_YEARS = 20

#: Corroboration accepts a guard-rejected candidate only at this many distinct session
#: years or more. Load-bearing: without it the tie-breaker accepts one candidate seen once —
#: possibly the wrong person, the #240 shape.
CORROBORATION_FLOOR = 2

# Dispositions.
IDENTITY_MINTED = "minted"
IDENTITY_WSL = "wsl"

# Refusal reasons — report-don't-drop, as everywhere in this source. A reason also becomes a
# *ledger and log key* downstream (``refusals_<reason>`` in the Phase B build's counters,
# passed as logging ``extra``), so keep them snake_case and short; the prefix is what keeps a
# new reason from colliding with a reserved LogRecord attribute (CR #97 — ``created`` is the
# precedent that made this a rule).
REFUSED_WIDE_GAP = "wide_gap"
REFUSED_JOIN_UNRESOLVED = "join_unresolved"
REFUSED_JOIN_AMBIGUOUS = "join_ambiguous"

#: Spelling variants the source itself contains, variant fold → canonical fold. Measured by
#: the adjacent-same-seat-same-surname sweep on the corrected corpus; each entry is one
#: person the fold alone would split. The genuine spousal/family successions that sweep also
#: surfaces (Joseph → Margaret Hurley, the Swayzes) are correctly distinct folds and are
#: deliberately NOT here.
IDENTITY_ALIASES: dict[str, str] = {
    "phillipmcdonough": "philipmcdonough",  # LD25 House, one tenure spelled both ways
    "josepharrasmith": "josephwarrasmith",  # LD7 House 1891-93, middle initial comes and goes
    "frankgmarzano": "frankmarzano",  # LD27 House 1965-71
    "jamesaweir": "jamesweir",  # LD44 House 1903-09
    "linneuslwestfall": "linneuslincolnwestfall",  # LD2/LD3, middle name vs initial
}

#: Adjudicated splits: fold → boundary session year. Records before the boundary form one
#: identity, records at or after it another — each keyed by its *own* earliest year.
IDENTITY_SPLITS: dict[str, int] = {
    # An 1899 Populist in LD44 and a 1947-65 Republican in LD6: a 48-year gap, fully
    # disjoint seat lineage, and a party era apart. Near-certainly two people.
    "elmerejohnston": 1947,
}

#: The §3 join residue, adjudicated: fold → WSL member id, or ``None`` to mint a roster
#: Person. These are the crossing names the guard rejects and the corroboration floor
#: (correctly) declines to rescue.
JOIN_ADJUDICATIONS: dict[str, str | None] = {
    "billdayjr": "103",  # William Day — LD3 House, sole 'day' on that seat 1991-92
    "bobbasich": "23",  # Robert Basich — LD19 House, sole 'basich' on that seat 1991-96
    "shirleygalloway": None,  # absent from the WSL sponsor index entirely; mint
}

#: Seat metadata printed into names on the early split districts (LD19/LD39, 1983-91):
#: ``Bob Basich – 19B``. The suffix is #229's Position signal, not part of the name.
_POSITION_SUFFIX = re.compile(r"\s*[–—-]\s*\d{1,2}\s*[AB]?\s*$")

#: Parenthetical segments — marital forms, printed nicknames, the odd leaked annotation.
_PARENTHETICAL = re.compile(r"\([^)]*\)")

#: Quoted nicknames: ``“Red”``, ``"Slim"``. The same person carries them in some listings
#: and not others, so they cannot participate in identity.
_QUOTED = re.compile(r"[“\"][^”\"]*[”\"]")

#: Honorifics carry no identity. Generational suffixes (``jr``/``sr``) are NOT here.
_HONORIFICS = frozenset({"mr", "mrs", "dr", "rev", "hon"})


def strip_position_suffix(name: str) -> str:
    """``"Bob Basich – 19B"`` → ``"Bob Basich"``. Public so the display-name minter
    (:mod:`persons`) shares one definition of what counts as seat metadata (CR #88)."""
    return _POSITION_SUFFIX.sub("", name)


def identity_fold(name: str) -> str:
    """The identity key's name half: the shared fold over the cleaned name."""
    cleaned = _QUOTED.sub(" ", _PARENTHETICAL.sub(" ", name))
    cleaned = strip_position_suffix(cleaned)
    tokens = [t for t in folded_tokens(cleaned) if t and t not in _HONORIFICS]
    return "".join(tokens)


@dataclass(frozen=True)
class RosterIdentity:
    """One resolved pre-1991 identity: minted under a roster key, or joined to WSL."""

    disposition: str
    fold: str
    #: ``<fold>:<first-session-year>`` when minted; ``None`` when joined (the WSL Person
    #: already has its id and the roster asserts nothing about it).
    key: str | None
    wsl_member_id: str | None
    #: Pre-floor records only. 1991+ rows belong to the WSL sponsor era and are consumed
    #: here solely as join evidence.
    records: tuple[RosterRecord, ...]


@dataclass(frozen=True)
class RefusedIdentity:
    """A group that cannot be resolved yet, with its subject named (oracle item 2)."""

    reason: str
    fold: str
    records: tuple[RosterRecord, ...]
    detail: str


@dataclass(frozen=True)
class IdentityReport:
    """Identities plus refusals. Every pre-floor input record lands in exactly one."""

    identities: tuple[RosterIdentity, ...]
    refused: tuple[RefusedIdentity, ...]

    def summary(self) -> dict[str, int]:
        """Counts by disposition and refusal reason — the shape a CLI prints."""
        counts: dict[str, int] = {}
        for identity in self.identities:
            counts[identity.disposition] = counts.get(identity.disposition, 0) + 1
        for refusal in self.refused:
            key = f"refused:{refusal.reason}"
            counts[key] = counts.get(key, 0) + 1
        return counts


def _wide_gap(years: list[int]) -> tuple[int, int] | None:
    """The first consecutive-listing gap wider than :data:`WIDE_GAP_YEARS`, if any."""
    for a, b in zip(years, years[1:], strict=False):
        if b - a > WIDE_GAP_YEARS:
            return (a, b)
    return None


class _Join:
    """The 1991 join for one crossing group: guard, then corroboration, then refusal."""

    def __init__(self, seatings: Iterable[Seating]) -> None:
        self._by_seat: dict[tuple[str, int, int], list[Seating]] = defaultdict(list)
        for seating in seatings:
            self._by_seat[(seating.chamber, seating.district, seating.year)].append(seating)

    def resolve(self, post_rows: list[RosterRecord]) -> tuple[str | None, str | None]:
        """``(member_id, refusal_reason)`` — exactly one is non-``None``.

        Mirrors :meth:`SuccessionResolver._member_ids` (#240): a surname match whose
        given-name initials share nothing with the roster row's own tokens is *rejected*,
        not matched — the single surviving surname match may be a different person.
        Rejected candidates then get the corroboration tie-breaker: accepted only at
        :data:`CORROBORATION_FLOOR` distinct session years or more AND strictly ahead of
        every rival.
        """
        compatible: set[str] = set()
        rejected_years: dict[str, set[int]] = defaultdict(set)
        for row in post_rows:
            keys = surname_match_set(row.name)
            tokens = {t[0] for t in folded_tokens(row.name) if t}
            for seating in self._by_seat.get((row.chamber, row.district, row.year), ()):
                if fold_token(seating.surname) not in keys:
                    continue
                initials = {t[0] for t in folded_tokens(seating.given_name) if t}
                # No given name on the WSL side is no signal — never evidence against.
                if not initials or initials & tokens:
                    compatible.add(seating.member_id)
                else:
                    rejected_years[seating.member_id].add(row.year)
        if len(compatible) == 1:
            return compatible.pop(), None
        if len(compatible) > 1:
            return None, REFUSED_JOIN_AMBIGUOUS
        if rejected_years:
            ranked = sorted(rejected_years.items(), key=lambda kv: len(kv[1]), reverse=True)
            best_id, best_years = ranked[0]
            runner_up = len(ranked[1][1]) if len(ranked) > 1 else 0
            if len(best_years) >= CORROBORATION_FLOOR and len(best_years) > runner_up:
                return best_id, None
        return None, REFUSED_JOIN_UNRESOLVED

    def describe(self, post_rows: list[RosterRecord]) -> str:
        """The candidate landscape, for an actionable refusal detail."""
        rejected_years: dict[str, set[int]] = defaultdict(set)
        for row in post_rows:
            keys = surname_match_set(row.name)
            for seating in self._by_seat.get((row.chamber, row.district, row.year), ()):
                if fold_token(seating.surname) in keys:
                    rejected_years[seating.member_id].add(row.year)
        if not rejected_years:
            return "no seat-scoped WSL candidate"
        parts = [f"{mid}: {len(years)} yr" for mid, years in sorted(rejected_years.items())]
        return "candidates " + ", ".join(parts)


def _boundary_probes(pre: list[RosterRecord], floor: int) -> list[RosterRecord]:
    """Rows to ask the join about when a fold has no listing at or above ``floor`` (#259).

    The floor is a *listing-year* floor, but tenure crosses it. The roster indexes a row by
    the year the term **begins**, so a member whose final term started below the floor and
    ran past it has no listing there at all — while WSL, which covers the biennium, holds
    them. Keying the join on "has a post-floor listing" therefore misses them and mints a
    duplicate of an identity WSL already has: the §2 fork.

    A term at ``year`` covers ``[year, year + TERM_YEARS[chamber])``, so it reaches the floor
    iff ``year + TERM_YEARS > floor``. With the 1991 floor that admits exactly the 1989
    Senate terms (1989 + 4 = 1993 > 1991) and excludes 1989 House terms (1989 + 2 = 1991,
    ending the year the floor begins) and 1987 Senate terms (1987 + 4 = 1991, likewise) —
    all 14 measured cases are 1989 senators, and the rule is the term length rather than a
    hardcoded year.

    Each surviving row is projected onto the floor year, on its own seat, so
    :meth:`_Join.resolve` can index it against the WSL seatings there. The projection is a
    *probe*, never an identity record: it carries no party and is discarded after the join.
    """
    return [
        replace(row, year=floor) for row in pre if row.year + TERM_YEARS.get(row.chamber, 0) > floor
    ]


def resolve_identities(
    records: Iterable[RosterRecord],
    *,
    seatings: Iterable[Seating],
    aliases: Mapping[str, str] = IDENTITY_ALIASES,
    splits: Mapping[str, int] = IDENTITY_SPLITS,
    adjudications: Mapping[str, str | None] = JOIN_ADJUDICATIONS,
    floor: int = ROSTER_IDENTITY_FLOOR,
) -> IdentityReport:
    """Resolve every pre-``floor`` record to an identity, or refuse it with a tally.

    ``records`` should be the whole parse: rows at or above the floor are consumed as join
    evidence for their fold group and are never carried in an identity.
    """
    groups: dict[str, list[RosterRecord]] = defaultdict(list)
    for record in records:
        fold = identity_fold(record.name)
        groups[aliases.get(fold, fold)].append(record)

    join = _Join(seatings)
    identities: list[RosterIdentity] = []
    refused: list[RefusedIdentity] = []

    def mint(fold: str, rows: list[RosterRecord]) -> None:
        first = min(r.year for r in rows)
        identities.append(
            RosterIdentity(
                disposition=IDENTITY_MINTED,
                fold=fold,
                key=f"{fold}:{first}",
                wsl_member_id=None,
                records=tuple(rows),
            )
        )

    for fold, rows in sorted(groups.items()):
        pre = [r for r in rows if r.year < floor]
        post = [r for r in rows if r.year >= floor]
        if not pre:
            continue  # entirely WSL-era; nothing for the roster to identify

        if fold in splits:
            # A split group never reaches the 1991 join: both parts mint. Correct for the one
            # shipped entry (neither Johnston crosses the floor); a future split on a
            # *crossing* fold would need join handling per part first, or its post-floor rows
            # silently anchor to nothing and the minted half duplicates a WSL identity — the
            # §2 fork (CR #80).
            boundary = splits[fold]
            for part in (
                [r for r in pre if r.year < boundary],
                [r for r in pre if r.year >= boundary],
            ):
                if part:
                    mint(fold, part)
            continue

        gap = _wide_gap(sorted({r.year for r in pre}))
        if gap is not None:
            refused.append(
                RefusedIdentity(
                    reason=REFUSED_WIDE_GAP,
                    fold=fold,
                    records=tuple(pre),
                    detail=(
                        f"consecutive listings {gap[0]} -> {gap[1]} "
                        f"({gap[1] - gap[0]} years apart), no adjudicated split"
                    ),
                )
            )
            continue

        if not post:
            # No post-floor listing — but a term that began below the floor may still have
            # run past it (#259). Probe WSL with those rows projected onto the floor year;
            # anything else is a genuine pre-floor-only identity.
            probes = _boundary_probes(pre, floor)
            member_id = join.resolve(probes)[0] if probes else None
            if member_id is None:
                mint(fold, pre)
            else:
                identities.append(
                    RosterIdentity(
                        disposition=IDENTITY_WSL,
                        fold=fold,
                        key=None,
                        wsl_member_id=member_id,
                        records=tuple(pre),
                    )
                )
            continue

        # A crossing fold: the pre-floor rows belong to an identity WSL may already hold.
        if fold in adjudications:
            member_id = adjudications[fold]
            if member_id is None:
                mint(fold, pre)
            else:
                identities.append(
                    RosterIdentity(
                        disposition=IDENTITY_WSL,
                        fold=fold,
                        key=None,
                        wsl_member_id=member_id,
                        records=tuple(pre),
                    )
                )
            continue

        member_id, reason = join.resolve(post)
        if member_id is not None:
            identities.append(
                RosterIdentity(
                    disposition=IDENTITY_WSL,
                    fold=fold,
                    key=None,
                    wsl_member_id=member_id,
                    records=tuple(pre),
                )
            )
        else:
            refused.append(
                RefusedIdentity(
                    reason=reason or REFUSED_JOIN_UNRESOLVED,
                    fold=fold,
                    records=tuple(pre),
                    detail=join.describe(post),
                )
            )

    return IdentityReport(identities=tuple(identities), refused=tuple(refused))
