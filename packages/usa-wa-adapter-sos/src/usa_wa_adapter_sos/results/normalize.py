"""Pure ``results.vote.wa.gov`` → House-position primitives (#101).

Parses the legislative election-**results** CSV rows into the shared ``{LD: [HousePosition]}`` map
the ``house/`` application consumes, **and** the ``{LD: SenateWinner}`` map (#106 A′) — the wire is
one CSV carrying both chambers, so parsing only the House left the Senate ballot evidence invisible
(the *yes-and* rule in ARCHITECTURE.md). The position interface lives in
:mod:`usa_wa_adapter_sos.positions`; the parsing here is results-CSV-specific. The results wire
differs from the filings export: office + LD + position live in one combined ``Race`` string
(``"LEGISLATIVE DISTRICT N - State Representative Pos. 1"``), the ballot name in ``Candidate``,
party in ``Party``, and the vote count (the Senate winner tiebreak) in ``Votes``.

**Audited (#101): the race label is inconsistent** — WA SOS labels the House seat three ways
(``State Representative Pos. N`` ~99%, ``Representative, Position N`` at 2020 LD15, a bare
``State Representative N`` at 2014 LD30 — sometimes differing between one district's two seats in a
single file). An exact-match parser silently drops real seats, so the rule is tolerant: a row
whose ``Race`` names a *Representative* (not a *Senator*) yields ``LD`` = the digits after
``DISTRICT`` and ``position`` = the trailing ``1``/``2``. ``State Senator`` rows carry no trailing
position digit and separate cleanly; ``WRITE-IN`` candidacies are dropped.
"""

from __future__ import annotations

import re
from typing import Any

from usa_wa_adapter_pdc.normalize.positions import canonical_position, surname_match_set

from usa_wa_adapter_sos.positions import HousePosition, SenateWinner, sos_party_slug

#: The LD number in a results ``Race`` label (``"LEGISLATIVE DISTRICT 15 - …"``).
_LD_RE = re.compile(r"LEGISLATIVE DISTRICT\s+(\d+)", re.IGNORECASE)

#: The ballot position as the **trailing** ``1``/``2`` of a House ``Race`` label — the one part
#: common to all three audited variants (``Pos. 1`` / ``Position 1`` / bare ``… Representative 1``).
_TRAILING_POSITION_RE = re.compile(r"([12])\s*$")

#: The ``Candidate`` value for the write-in aggregate row (dropped — not a real candidacy).
_WRITE_IN = "WRITE-IN"


def _parse_votes(raw: Any) -> int | None:
    """The integer ``Votes`` count of a results row, or ``None`` when absent/blank/non-numeric
    (thousands separators tolerated). ``None`` means "can't be used to rank" — never zero."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    return int(text) if text.isdigit() else None


def parse_house_race(race: str) -> tuple[int, str] | None:
    """``(LD, qualifier)`` for a House Representative contest, else ``None``.

    Robust to the three audited label variants, case-insensitive. A ``State Senator`` row (no
    trailing position digit) or a non-legislative office returns ``None``."""
    if "representative" not in race.lower():
        return None  # State Senator / statewide / judicial / other office
    ld_match = _LD_RE.search(race)
    position_match = _TRAILING_POSITION_RE.search(race.strip())
    if ld_match is None or position_match is None:
        return None
    qualifier = canonical_position(position_match.group(1))
    if qualifier is None:
        return None
    return int(ld_match.group(1)), qualifier


def parse_senate_race(race: str) -> int | None:
    """The ``LD`` of a ``State Senator`` contest, else ``None`` — the mirror of
    :func:`parse_house_race` (a House contest, a non-legislative office, or a blank returns
    ``None``). The Senate seat has no ballot position, so this yields the LD alone."""
    lowered = race.lower()
    if "senator" not in lowered or "representative" in lowered:
        return None  # House / statewide / judicial / other office
    ld_match = _LD_RE.search(race)
    return int(ld_match.group(1)) if ld_match else None


def build_senate_winners(rows: list[dict[str, Any]]) -> dict[int, SenateWinner]:
    """Group a legislative-results cohort's **Senate** rows by LD → the winning candidacy (#106).

    The wire lists every candidacy, not just the winner (as it does for the House), so the winner
    is the top-``Votes`` non-write-in row of each LD. An LD whose winner is ambiguous — a vote tie,
    or unparseable/absent counts on more than one candidacy — is **omitted** rather than guessed
    (the :func:`position_for` discipline); a single uncontested candidacy is unambiguous with no
    vote counts at all. ``WRITE-IN`` rows, House / other offices, and blank-name rows are skipped.

    Unlike the House map this carries no structural fact (the Senate seat is unqualified) — it is
    attestation the Phase B consumer uses to cite an elected senator and corroborate a succession
    event (see :class:`SenateWinner`)."""
    by_ld: dict[int, list[tuple[SenateWinner, bool]]] = {}
    for row in rows:
        candidate = (row.get("Candidate") or "").strip()
        if candidate.upper() == _WRITE_IN or not candidate:
            continue
        ld = parse_senate_race(row.get("Race") or "")
        if ld is None:
            continue
        name_keys = surname_match_set(candidate)
        if not name_keys:
            continue
        votes = _parse_votes(row.get("Votes"))
        winner = SenateWinner(
            ld=ld,
            ballot_name=candidate,
            name_keys=frozenset(name_keys),
            party_slug=sos_party_slug(row.get("Party")),
            votes=votes,
        )
        by_ld.setdefault(ld, []).append((winner, votes is not None))

    winners: dict[int, SenateWinner] = {}
    for ld, candidacies in by_ld.items():
        if len(candidacies) == 1:
            winners[ld] = candidacies[0][0]  # uncontested — unambiguous without vote counts
            continue
        # Contested: the winner needs a vote count, and a tie on the top count is unresolvable.
        counted = [w for w, has_votes in candidacies if has_votes]
        if not counted:
            continue
        top = max(w.votes for w in counted)
        leaders = [w for w in counted if w.votes == top]
        if len(leaders) == 1:
            winners[ld] = leaders[0]
    return winners


def build_house_positions(rows: list[dict[str, Any]]) -> dict[int, list[HousePosition]]:
    """Group a legislative-results cohort's **House** candidacy rows by LD → ``[HousePosition]``.

    A row participates when its ``Race`` is a State Representative contest (any audited variant)
    with a parseable LD + trailing position + a non-blank ballot name; ``WRITE-IN`` rows, Senate /
    other offices, and unparseable rows are skipped."""
    by_ld: dict[int, list[HousePosition]] = {}
    for row in rows:
        candidate = (row.get("Candidate") or "").strip()
        if candidate.upper() == _WRITE_IN:
            continue
        parsed = parse_house_race(row.get("Race") or "")
        if parsed is None:
            continue
        ld, qualifier = parsed
        name_keys = surname_match_set(candidate)
        if not name_keys:
            continue
        by_ld.setdefault(ld, []).append(
            HousePosition(
                qualifier=qualifier,
                name_keys=frozenset(name_keys),
                party_slug=sos_party_slug(row.get("Party")),
            )
        )
    return by_ld
