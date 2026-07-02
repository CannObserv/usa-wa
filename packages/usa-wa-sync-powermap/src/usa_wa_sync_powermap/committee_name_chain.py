"""Full-timeline committee rename-chain builder (sub-project 3, Phase B).

The adjacent-biennium spine (:mod:`committee_name_reconcile`) detects one hop
(current vs prior). This builds the **whole chain**: given
``{biennium: {source_id: LongName}}`` across every archived biennium, it walks each
stable id's *consecutive appearances* and emits every ``normalize_name`` transition as
a windowed ``former`` → ``legal`` hop (effective bounds = the boundary biennium's start
date, #58 windowing generalized to the whole timeline).

Pure — no DB, no PM. Deep-history guardrails:

- **normalize before compare** — ``normalize_name`` collapses case/punctuation/whitespace
  drift so formatting churn in old rosters isn't a false rename.
- **dormancy-aware** — diffs consecutive *appearances* of an id, so an absence gap is
  spanned (the name persists across it; absence ≠ rename).
- **per-boundary rename-storm floor** — if an outsized fraction of the ids eligible at a
  single boundary change name, that's a systematic WSL reformat/re-key, not real renames:
  the whole boundary's transitions are dropped (recorded in ``storm_skipped``). The
  fraction is only weighed once the eligible count reaches ``storm_floor`` (a tiny
  overlap makes the fraction hair-trigger).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from clearinghouse_sync_powermap.descriptors import normalize_name
from usa_wa_adapter_legislature.refresh import _biennium_start_year, biennium_start_date

#: Rename-storm default: drop a boundary whose renamed fraction exceeds this (a
#: systematic reformat, not real renames). Matches the spine's default (#46).
DEFAULT_MAX_RENAME_FRACTION = 0.34
#: Only weigh the storm fraction once this many ids are eligible at the boundary — a
#: small overlap makes the fraction hair-trigger (one of two = 0.5).
DEFAULT_STORM_FLOOR = 5


@dataclass(frozen=True)
class RenameTransition:
    """One former→legal hop for a stable committee id at a biennium boundary."""

    source_id: str
    former_name: str
    legal_name: str
    boundary_biennium: str
    #: The window boundary — the boundary biennium's start date. The former name's
    #: window closes here; the legal name's opens here (open end until the next hop).
    effective_start: date
    former_effective_end: date


def build_rename_chain(
    cohorts: dict[str, dict[str, str]],
    *,
    max_rename_fraction: float = DEFAULT_MAX_RENAME_FRACTION,
    storm_floor: int = DEFAULT_STORM_FLOOR,
) -> dict:
    """Build every rename transition across the biennium timeline.

    ``cohorts`` maps ``biennium → {source_id: raw name}`` (any order; sorted here
    ascending). Returns ``{"transitions": [RenameTransition...], "storm_skipped":
    [{biennium, renamed, eligible}...]}``.
    """
    order = sorted(cohorts, key=_biennium_start_year)
    # Per-id ordered appearances (dormancy gaps simply don't appear in the list).
    appearances: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for biennium in order:
        for source_id, name in cohorts[biennium].items():
            appearances[source_id].append((biennium, name))

    eligible_at: Counter[str] = Counter()  # ids with a prior appearance, per boundary
    by_boundary: dict[str, list[RenameTransition]] = defaultdict(list)
    for source_id, seq in appearances.items():
        for (_prev_b, prev_name), (curr_b, curr_name) in zip(seq, seq[1:], strict=False):
            eligible_at[curr_b] += 1
            if normalize_name(prev_name) == normalize_name(curr_name):
                continue
            boundary_date = biennium_start_date(curr_b)
            by_boundary[curr_b].append(
                RenameTransition(
                    source_id=source_id,
                    former_name=prev_name,
                    legal_name=curr_name,
                    boundary_biennium=curr_b,
                    effective_start=boundary_date,
                    former_effective_end=boundary_date,
                )
            )

    transitions: list[RenameTransition] = []
    storm_skipped: list[dict] = []
    for biennium in sorted(by_boundary, key=_biennium_start_year):
        hops = by_boundary[biennium]
        eligible = eligible_at[biennium]
        fraction = len(hops) / eligible if eligible else 0.0
        if eligible >= storm_floor and fraction > max_rename_fraction:
            storm_skipped.append({"biennium": biennium, "renamed": len(hops), "eligible": eligible})
            continue
        transitions.extend(hops)

    return {"transitions": transitions, "storm_skipped": storm_skipped}
