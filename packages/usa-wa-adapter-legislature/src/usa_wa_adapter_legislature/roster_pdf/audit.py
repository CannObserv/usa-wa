"""Read-only audit oracle over the roster (#225) — pure, no writes.

Phase 1's whole point: use the roster to *check* what we already assert, before it is ever
allowed to write a span. A claim is a ``(name, district, chamber, session_year)`` tuple drawn
from our own record; the roster either attests it or it does not.

**Attested** corroborates. **Unattested** is a candidate artifact — a span asserting an
occupancy the Legislature's own roster does not show. That is exactly the #144 class: John
Wynne's LD39 *Senate* row for 1991-92, where the roster shows him only in the LD39 House, was a
chamber-conflation artifact; Marlo Braun's LD20 Senate substitution, which the roster does show,
was genuine. Reproducing both from the source alone, with no hand-curated denylist, is this
issue's acceptance oracle.

Unattested does **not** mean false. Below the roster's own floor, or in a district the edition
renumbered, absence is silence rather than denial — which is why this module reports and never
deletes. The :attr:`RosterAudit.match_rate` it returns is the number #228 is gated on.

**The match rate is deliberately conservative — read it as a floor on disagreement, not a
precision score** (CR finding 6). A roster row covers the whole term it opens, so a member who
left mid-term still reads as attested for the remainder even where the roster names their
successor in the same year group. Narrowing the window to "until the next row for this seat" is
not sound: the House runs two seats per district, so consecutive rows in one district-chamber-year
are *different seats*, not a succession. Tightening this needs the Position discriminator, which
arrives with #229 — until then the oracle under-reports disagreement rather than inventing it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_common.names import fold_token, surname_match_set

#: A claim under audit: our own assertion, in the roster's own coordinates.
Claim = tuple[str, int, str, int]

#: Term length per chamber, in years. **The roster lists a member only in the session year their
#: term begins**, so a senator elected in 1995 has roster rows at 1995, 1999, 2003 -- and none at
#: 1997, 2001, 2005 despite sitting throughout. Matching a claim on exact year equality therefore
#: marks roughly half of all Senate claims unattested *by construction*, which would both bury
#: the real artifacts and hand #228 a meaningless gate. A roster row instead covers the term it
#: opens.
TERM_YEARS = {"house": 2, "senate": 4}


def _surname(name: str) -> str:
    """The folded surname of a free-form name -- its last meaningful token.

    The roster writes honorifics, nicknames, initials and marital forms the wires never do
    (``Dr. C. G. Brown``, ``Robert "Bob" McCaslin``, ``Margaret (Mrs. Joseph E.) Hurley``).
    Matching on the surname is what survives that; matching on the full string would read every
    pre-1991 span as unattested and make this oracle worthless.
    """
    tokens = [folded for raw in name.replace('"', " ").split() if (folded := fold_token(raw))]
    return tokens[-1] if tokens else ""


def match_rate(*, matched: int, total: int) -> float:
    """Matched over total, ``0.0`` on an empty cohort. Reported, never asserted."""
    return matched / total if total else 0.0


@dataclass(frozen=True)
class RosterAudit:
    """What the roster does and does not corroborate. Report-only."""

    attested: tuple[Claim, ...]
    unattested: tuple[Claim, ...]

    @property
    def match_rate(self) -> float:
        """The share of claims the roster corroborates -- the #228 gate."""
        total = len(self.attested) + len(self.unattested)
        return match_rate(matched=len(self.attested), total=total)


def audit_roster(*, records: Sequence[RosterRecord], claims: Iterable[Claim]) -> RosterAudit:
    """Partition ``claims`` by whether the roster attests them. Pure; no database, no writes."""
    index: dict[tuple[int, str], list[RosterRecord]] = {}
    for record in records:
        index.setdefault((record.district, record.chamber), []).append(record)

    attested: list[Claim] = []
    unattested: list[Claim] = []
    for claim in claims:
        name, district, chamber, year = claim
        if chamber not in TERM_YEARS:
            # Silently defaulting an unrecognised chamber to a two-year window would produce a
            # quietly wrong match rate, and that number gates #228 — a wrong number is worse
            # than a loud failure (CR finding 5).
            raise ValueError(
                f"unknown chamber {chamber!r} in claim {claim!r}; expected one of "
                f"{sorted(TERM_YEARS)}"
            )
        term = TERM_YEARS[chamber]
        surname = _surname(name)
        candidates = [
            c for c in index.get((district, chamber), ()) if c.year <= year < c.year + term
        ]
        hit = any(
            surname and (surname in surname_match_set(c.name) or _surname(c.name) == surname)
            for c in candidates
        )
        (attested if hit else unattested).append(claim)
    return RosterAudit(attested=tuple(attested), unattested=tuple(unattested))
