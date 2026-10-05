"""Ballot winners keyed to the seat they won (#412 PR B).

The row builder behind the ``seat_winners`` model. It exists for one consumer:
``dbt/tests/assignments_odd_year_winners_seated.sql``, the port of the House
and Senate odd-year corroboration units, which read canonical and retired with it. That
check is a join — a winner's seat against the spans that hold it — and SQL
cannot pick a winner out of ``stg_sos_results`` without re-parsing its race
labels, which this repo's rule forbids keying on (three audited House label
variants, #101).

So nothing is re-implemented here. Winner selection is the SOS normalizers',
imported unchanged, never-guess tiebreak and all (a tie or an unrankable race
is omitted). The seat key is :func:`~usa_wa_pipeline.conformed.roles.role_for_span`'s,
the one ``assignments`` carries, so the two sides of the join cannot drift.

**Per wire, not per year.** Each ``sos-legresults:<date>`` resource is one
election's cohort, and winners are chosen within it — which also lets every
row name the wire it came from, as a staging row does (#313).

Not published: an internal model, like ``person_name_collisions``.
"""

from __future__ import annotations

from typing import Any

from clearinghouse_domain_legislative.span_kinds import KIND_HOUSE, KIND_SENATE
from usa_wa_adapter_sos.results.normalize import build_house_winners, build_senate_winners
from usa_wa_common.seats import house_span_discriminator
from usa_wa_pipeline.conformed.house import election_year, result_wire_row
from usa_wa_pipeline.conformed.roles import role_for_span
from usa_wa_pipeline.staging.common import PROVENANCE_SCHEMA

SEAT_WINNER_SCHEMA = {
    "election_date": "VARCHAR",
    "election_year": "BIGINT",
    "role_key": "VARCHAR",
    "district": "BIGINT",
    "qualifier": "VARCHAR",
    **PROVENANCE_SCHEMA,
}


def winner_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Staging SOS result rows → one row per seat a ballot decided, per wire."""
    wires: dict[tuple[str, int, str, str], list[dict[str, Any]]] = {}
    for row in results:
        year = election_year(row)
        if year is None:
            continue
        key = (str(row["election_date"]), year, row.get("source"), row.get("resource_id"))
        wires.setdefault(key, []).append(result_wire_row(row))

    out: list[dict[str, Any]] = []
    for (election_date, year, source, resource_id), wire in sorted(wires.items()):
        seats = [
            (role_for_span(KIND_SENATE, str(ld)).role_key, ld, None)
            for ld in build_senate_winners(wire)
        ]
        seats += [
            (
                role_for_span(KIND_HOUSE, house_span_discriminator(ld, winner.qualifier)).role_key,
                ld,
                winner.qualifier,
            )
            for ld, positions in build_house_winners(wire).items()
            for winner in positions
        ]
        out.extend(
            {
                "election_date": election_date,
                "election_year": year,
                "role_key": role_key,
                "district": ld,
                "qualifier": qualifier,
                "source": source,
                "resource_id": resource_id,
            }
            for role_key, ld, qualifier in sorted(seats)
        )
    return out
