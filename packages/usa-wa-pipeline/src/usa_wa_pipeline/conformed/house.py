"""The House Position seat as a stateless transform (#309 part 2, increment 3).

The conformed analog of ``usa_wa_facts_seats.house.build``: WSL owns *who sits*
(the sponsor roster — LD + party), SOS owns *which position* (the ballot's
Position 1/2), and the join produces one ``chamber-house`` span per tenure.
This is the Layer-3b composition the other two families do not need, which is
why it lives beside :mod:`~usa_wa_pipeline.conformed.spans` rather than inside
it — the same seam the package split draws.

Nothing about the inference is re-implemented. Imported UNCHANGED, because each
carries a decided question:

- ``build_house_roster`` / ``house_mover_ids`` — the #105 (a) mover exclusion, a
  House row whose stable ``Id`` also appears in a named Senate row of the same
  wire;
- ``merge_positions`` — the #123 §1 map: the even November's full candidacy set
  (the #103 elimination depends on the losers) ∪ the odd November's special
  **winners** only (hazard b — a losing special candidacy must never
  false-match);
- ``backchain_house_observations`` — the #118 carry-back of a ballot-anchored
  Position through continuous same-LD tenure, with the #103 within-LD
  elimination it cascades into;
- ``apply_operator_events`` with ``movers_by_biennium`` — the #145 gate that
  synthesizes a chamber-mover's closed House tenure from a ``vacated`` WITHOUT
  re-including them in the roster, which would re-run the elimination and split
  the backfill.

**What dissolves**, as in the sibling families: the emitter, every citation
path (``inferred_keys``/``special_keys``/``fetch_events``/``roster_events``),
the synthetic-anchor bootstrap and ``close_stale_spans`` (#83) all exist to
mutate a durable table. A recomputed transform expresses retraction as absence.

**``restrict_to_biennium`` dissolves too, and that is the deep build.** The
Postgres tier's daily re-drive scopes emission to the current biennium; the
backfill does not. Both were engineered to produce ONE span identity (the #100
CR depth mismatch), and a stateless rebuild is unconditionally the unrestricted
one — so the depth question cannot arise here at all.
"""

from __future__ import annotations

from typing import Any

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.operator_overlay import (
    apply_operator_events,
    from_rows,
    latest_event_biennium_by_member,
    stale_exempt_members,
)
from clearinghouse_domain_legislative.span_kinds import KIND_HOUSE
from clearinghouse_domain_legislative.tenure_spans import TenureSpan, build_tenure_spans
from usa_wa_adapter_legislature.sponsors.roster_hygiene import (
    STALE_MIN_COVERAGE_DEFAULT,
    committee_member_ids_by_biennium,
    stale_exclusions_by_biennium,
)
from usa_wa_adapter_sos.results.normalize import build_house_positions, build_house_winners
from usa_wa_facts_seats.house.backchain import (
    MAX_BACKCHAIN_HOPS_DEFAULT,
    backchain_house_observations,
)
from usa_wa_facts_seats.house.build import merge_positions
from usa_wa_facts_seats.pdc.matching import build_house_roster, house_mover_ids
from usa_wa_pipeline.conformed.wire import committee_rosters, sponsor_wire_rows

logger = get_logger(__name__)

#: The kind this module owns. Its spans join the WSL family's source space —
#: the seat is asserted about a WSL member id, the ballot only names it.
HOUSE_KINDS = frozenset({KIND_HOUSE})


def sos_result_wires(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Staging SOS result rows → ``{election_year: [CSV-shaped rows]}``.

    The SOS normalizers read the archived CSV's own header keys, so this
    restores that shape the way :func:`~usa_wa_pipeline.conformed.spans._wire`
    restores the SOAP one — never re-interpreting an upstream string, only
    re-labelling it. A row whose ``election_date`` carries no year is skipped:
    the year is what keys a cohort to its ballot, and a row that cannot supply
    one cannot be attributed to an election.
    """
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        raw = str(row.get("election_date") or "")[:4]
        if not raw.isdigit():
            continue
        out.setdefault(int(raw), []).append(
            {
                "Race": row.get("race"),
                "Candidate": row.get("candidate"),
                "Party": row.get("party"),
                "Votes": row.get("votes"),
            }
        )
    return out


def house_positions_by_year(
    wires: dict[int, list[dict[str, Any]]],
) -> tuple[dict[int, Any], dict[int, Any]]:
    """``(positions, winners)`` per election year — the two halves #123 merges.

    ``positions`` keeps every candidacy (the #103 within-LD elimination reasons
    over the losers); ``winners`` is the top-vote non-write-in per race, the
    only form in which an odd-year special may join the map.
    """
    return (
        {year: build_house_positions(rows) for year, rows in wires.items()},
        {year: build_house_winners(rows) for year, rows in wires.items()},
    )


def build_house_spans(
    *,
    sponsors: list[dict[str, Any]],
    committee_members: list[dict[str, Any]],
    sos_results: list[dict[str, Any]],
    events: list[Any],
    current_biennium: str,
    stale_min_coverage: float = STALE_MIN_COVERAGE_DEFAULT,
    max_backchain_hops: int = MAX_BACKCHAIN_HOPS_DEFAULT,
    context_spans: list[TenureSpan] | None = None,
) -> list[TenureSpan]:
    """WSL roster × SOS ballot → ``chamber-house`` tenure spans.

    Raises ``ValueError`` when a live sponsor corpus meets an **empty** ballot
    archive. That combination silently deletes the whole family, and nothing
    downstream would notice: chamber-house is ~4% of the assignments table,
    well inside the publish gate's 10% shrink floor. Same rule as the #228
    deepening (CR 57) — an input whose absence deletes facts must refuse, not
    return empty.
    """
    if not sponsors:
        return []
    if not sos_results:
        raise ValueError(
            f"the House Position seat needs the SOS ballot archive: 0 result rows under a "
            f"corpus of {len(sponsors)} sponsor rows. Publishing now would drop every "
            "chamber-house span — ~4% of the table, inside the publish shrink floor, so "
            "nothing downstream would catch it."
        )
    rows_by_biennium = sponsor_wire_rows(sponsors)
    positions, winners = house_positions_by_year(sos_result_wires(sos_results))

    # The #105 hygiene, in the Postgres tier's own order: stale exclusion, then
    # the #145 biennium-scoped operator exemption MINUS this biennium's movers
    # (re-including a mover re-runs the #103 elimination and splits the
    # backfill — the overlay synthesizes their closed tenure instead).
    exclusions = stale_exclusions_by_biennium(
        rows_by_biennium,
        committee_member_ids_by_biennium(committee_rosters(committee_members)),
        min_coverage=stale_min_coverage,
    )
    overlay = from_rows(events)
    latest_event_biennium = latest_event_biennium_by_member(overlay)
    movers_by_biennium = {
        biennium: house_mover_ids(rows) for biennium, rows in rows_by_biennium.items()
    }
    roster_by_biennium = {
        biennium: build_house_roster(
            rows,
            exclude_ids=exclusions.get(biennium, set()),
            keep_ids=stale_exempt_members(latest_event_biennium, biennium)
            - movers_by_biennium[biennium],
        )
        for biennium, rows in rows_by_biennium.items()
    }
    positions_by_biennium = {
        biennium: merge_positions(biennium, positions, winners) for biennium in rows_by_biennium
    }

    backchain = backchain_house_observations(
        roster_by_biennium, positions_by_biennium, max_hops=max_backchain_hops
    )
    logger.info(
        "conformed_house_cohort",
        extra={
            "bienniums": len(rows_by_biennium),
            "observations": len(backchain.observations),
            "inferred": len(backchain.inferred_keys),
            "backchained": len(backchain.backchain_keys),
        },
    )
    spans = apply_operator_events(
        build_tenure_spans(list(backchain.observations), current_biennium=current_biennium),
        overlay,
        current_biennium=current_biennium,
        owned_kinds=set(HOUSE_KINDS),
        movers_by_biennium=movers_by_biennium,
        context_spans=context_spans or [],
    )
    return sorted(spans, key=lambda s: (s.member_id, s.kind, s.discriminator, s.start_biennium))
