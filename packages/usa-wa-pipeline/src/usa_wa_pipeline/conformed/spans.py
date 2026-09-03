"""Tenure spans as a stateless transform (#309 part 2): staging → assignments.

The conformed analog of the Postgres-tier Phase-B builders
(``sponsors/build.py``, ``membership/build.py``), with one structural
difference: **nothing here writes, so nothing here needs the DB half**. The
old builders' `close_stale_spans` sweep (#83), synthetic-anchor bootstrap and
`load_context_spans` read exist because they mutate a durable table in place;
a conformed model recomputes every span from staging on each run, so a span
the archive no longer asserts is simply absent — retraction-as-absence, which
is the #302 publication contract.

What is imported UNCHANGED, because each guard encodes a production incident:

- the pure engine — ``build_tenure_spans`` (dormancy splits, open-end
  resolution) and ``apply_operator_events`` (#267 departed-split-at-return,
  #272 seated-dates-the-tenure-it-starts, #119 open-synth gate);
- the projections — ``build_sponsor_observations`` (party + Senate seat) and
  ``build_committee_membership_observations``, so no upstream string is
  re-interpreted here;
- the hygiene — ``stale_exclusions_by_biennium`` (#105 departed-but-still-named
  ghosts, with its coverage floor and tail rule), the biennium-scoped operator
  exemption (#145) and the curated artifact denylist (#144).

Only the plumbing is new: staging rows carry the same facts as the wire under
normalized names, so :func:`sponsor_wire_rows` / :func:`committee_rosters`
restore the shape the projectors consume, and :func:`build_all_spans` applies
the same steps in the same ORDER as ``sponsors/build.py``.

**Context spans (#267).** The `departed` split reads a member's return date
off spans of OTHER kinds. The old builder loads them from Postgres because
each builder writes separately; here every kind is built in ONE pass, so the
committee spans computed in this run serve as the sponsor build's context —
same information, no cross-builder blindness. The one kind still missing is
`chamber-house`, which needs the PDC Position inference (#229) the facts-seats
port brings; until then a member who returned only to a House seat can keep a
span the Postgres tier would have split. That gap is measured by the span
parity probe, not assumed away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clearinghouse_domain_legislative.operator_overlay import (
    apply_operator_events,
    from_rows,
    latest_event_biennium_by_member,
    stale_exempt_members,
)
from clearinghouse_domain_legislative.span_kinds import (
    KIND_COMMITTEE,
    KIND_PARTY,
    KIND_SENATE,
)
from clearinghouse_domain_legislative.tenure_spans import (
    Observation,
    TenureSpan,
    build_tenure_spans,
)
from usa_wa_adapter_legislature.membership.projector import (
    build_committee_membership_observations,
)
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_WSL,
    Seating,
    resolve_identities,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.projector import build_pre1991_observations
from usa_wa_adapter_legislature.sponsors.artifacts import with_artifact_exclusions
from usa_wa_adapter_legislature.sponsors.projector import build_sponsor_observations
from usa_wa_adapter_legislature.sponsors.roster_hygiene import (
    STALE_MIN_COVERAGE_DEFAULT,
    committee_member_ids_by_biennium,
    stale_exclusions_by_biennium,
)
from usa_wa_common.seats import district_number

#: The source these spans are asserted under — the WSL archive.
SOURCE = "usa_wa_legislature"

#: The kinds this module owns. `chamber-house` belongs to the facts-seats port.
SPONSOR_KINDS = frozenset({KIND_PARTY, KIND_SENATE})
COMMITTEE_KINDS = frozenset({KIND_COMMITTEE})

ASSIGNMENT_COLUMNS = [
    "entity_id",
    "member_id",
    "source",
    "span_kind",
    "span_discriminator",
    "span_start_biennium",
    "span_end_biennium",
    "valid_from",
    "valid_to",
    "is_active",
]


@dataclass(frozen=True)
class SpanInputs:
    """Everything the span transform reads: two staging row sets plus the
    curated operator events (the one input with no raw-store origin — human
    succession decisions, read from Postgres)."""

    sponsors: list[dict[str, Any]]
    committee_members: list[dict[str, Any]]
    events: list[Any] = field(default_factory=list)
    #: Roster-PDF member-years — the #228 deepening input (see
    #: :func:`deepening_observations`). Empty means "no deepening", which
    #: re-asserts the shallow 1991-start keys, so a real build must pass them.
    roster: list[dict[str, Any]] = field(default_factory=list)


def _wire(row: dict[str, Any]) -> dict[str, Any]:
    """One staging row in the WSL wire's own shape, for the projectors."""
    return {
        "Id": row.get("member_id"),
        "FirstName": row.get("first_name"),
        "LastName": row.get("last_name"),
        "Name": row.get("name"),
        "LongName": row.get("long_name"),
        "Agency": row.get("agency"),
        "Party": row.get("party"),
        "District": row.get("district"),
    }


def sponsor_wire_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Staging sponsor rows → ``{biennium: [wire dicts]}`` (the roster-map shape)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        biennium = row.get("biennium")
        if not biennium:
            continue
        out.setdefault(str(biennium), []).append(_wire(row))
    return out


def committee_rosters(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Staging committee-member rows → ``{(biennium, committee_id): [wire dicts]}``."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        biennium, committee_id = row.get("biennium"), row.get("committee_id")
        if not biennium or not committee_id:
            continue
        out.setdefault((str(biennium), str(committee_id)), []).append(_wire(row))
    return out


def seatings_from_sponsors(rows: list[dict[str, Any]]) -> list[Seating]:
    """Staging sponsor rows → per-YEAR seatings, the identity resolve's index.

    Mirrors ``roster_pdf.backfill.load_seatings`` exactly, including its rule
    that a biennium's roster attests to BOTH of its years (a succession
    boundary is dated in one of them).
    """
    seatings: list[Seating] = []
    for row in rows:
        district = district_number(row.get("district"))
        biennium = row.get("biennium")
        if district is None or not biennium:
            continue
        start = int(str(biennium).split("-")[0])
        chamber = "senate" if row.get("agency") == "Senate" else "house"
        for year in (start, start + 1):
            seatings.append(
                Seating(
                    member_id=str(row.get("member_id")),
                    chamber=chamber,
                    district=district,
                    year=year,
                    surname=row.get("last_name") or "",
                    given_name=row.get("first_name") or "",
                )
            )
    return seatings


def roster_records(rows: list[dict[str, Any]]) -> list[RosterRecord]:
    """Staging roster rows → :class:`RosterRecord`s for the resolve step.

    ``page_number`` is reconstructed as ``0``: staging drops it as a layout
    artifact, and neither the identity resolve nor the pre-1991 projection
    reads it (verified) — only the parser that produced it did.
    """
    records: list[RosterRecord] = []
    for row in rows:
        try:
            records.append(
                RosterRecord(
                    district=int(row["district"]),
                    chamber=str(row["chamber"]),
                    year=int(row["year"]),
                    order=int(row["order"]),
                    name=str(row["name"]),
                    party_token=str(row.get("party_token") or ""),
                    annotation=row.get("annotation"),
                    page_number=0,
                )
            )
        except (KeyError, TypeError, ValueError):
            # report-don't-drop belongs to the parser; a malformed staging row
            # here is a staging bug the roster key tests catch, not a fact.
            continue
    return records


def deepening_observations(
    roster: list[dict[str, Any]], sponsors: list[dict[str, Any]]
) -> list[Observation]:
    """The #228 deepening: roster-era tenure for WSL-JOINED identities.

    A member whose service crosses the 1991 sponsor-archive floor must emit ONE
    span keyed at its true start, not a shallow 1991-start span abutting a
    roster-sourced twin (the #97 collapse shape). Ported from
    ``roster_pdf.deepening.joined_pre1991_observations``: same resolve, same
    projection, same WSL-joined filter — but over staging rows, so no
    provenance-table read and no WSL re-pull.
    """
    records = roster_records(roster)
    if not records:
        return []
    report = resolve_identities(records, seatings=seatings_from_sponsors(sponsors))
    projection = build_pre1991_observations(report.identities, records)
    joined = {i.wsl_member_id for i in report.identities if i.disposition == IDENTITY_WSL}
    return [o for o in projection.observations if o.member_id in joined]


def build_all_spans(
    inputs: SpanInputs,
    *,
    current_biennium: str,
    stale_min_coverage: float = STALE_MIN_COVERAGE_DEFAULT,
    extra_observations: list[Observation] | None = None,
) -> list[TenureSpan]:
    """Every span this module owns, in the Postgres tier's own order.

    Committee spans build FIRST so they can serve as the sponsor build's
    ``context_spans`` (#267) — see the module docstring.
    """
    events = from_rows(inputs.events)
    rosters = committee_rosters(inputs.committee_members)
    roster_map = sponsor_wire_rows(inputs.sponsors)
    extras = (
        list(extra_observations)
        if extra_observations is not None
        else deepening_observations(inputs.roster, inputs.sponsors)
    )

    committee_spans = apply_operator_events(
        build_tenure_spans(
            build_committee_membership_observations(rosters), current_biennium=current_biennium
        ),
        events,
        current_biennium=current_biennium,
        owned_kinds=set(COMMITTEE_KINDS),
    )

    # #105 stale-row exclusion, then the #145 biennium-scoped operator
    # exemption, then the #144 artifact denylist as a hard union — the same
    # order as sponsors/build.py, where each step's rationale lives.
    exclusions = stale_exclusions_by_biennium(
        roster_map,
        committee_member_ids_by_biennium(rosters),
        min_coverage=stale_min_coverage,
    )
    latest_event_biennium = latest_event_biennium_by_member(events)
    exclusions = {
        biennium: (ids - stale_exempt_members(latest_event_biennium, biennium))
        for biennium, ids in exclusions.items()
    }
    exclusions = with_artifact_exclusions(exclusions)

    observations = build_sponsor_observations(roster_map, exclusions) + extras
    sponsor_spans = apply_operator_events(
        build_tenure_spans(observations, current_biennium=current_biennium),
        events,
        current_biennium=current_biennium,
        owned_kinds=set(SPONSOR_KINDS),
        context_spans=committee_spans,
    )
    return sorted(
        [*committee_spans, *sponsor_spans],
        key=lambda s: (s.member_id, s.kind, s.discriminator, s.start_biennium),
    )


def entity_index(crosswalk: list[dict[str, Any]]) -> dict[str, str]:
    """natural key → LIVE entity id, tombstones resolved to the survivor.

    An assignment must follow a merge rather than vanish with it: the member's
    keys still name a real person, now under the survivor's ULID (the merge
    tombstone is the published crosswalk's only re-point signal). Chains
    resolve transitively; a cycle cannot occur (the merge verb refuses a
    tombstoned survivor) but the walk is bounded anyway.
    """
    merged: dict[str, str] = {}
    for row in crosswalk:
        target = row.get("merged_into")
        if target is not None:
            merged[str(row["entity_id"])] = str(target)

    def resolve(entity_id: str) -> str:
        seen: set[str] = set()
        while entity_id in merged and entity_id not in seen:
            seen.add(entity_id)
            entity_id = merged[entity_id]
        return entity_id

    return {row["natural_key"]: resolve(str(row["entity_id"])) for row in crosswalk}


def assignment_rows(
    spans: list[TenureSpan], entity_by_key: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Spans ⨝ the person crosswalk → published assignment rows + counters.

    The 4-part span ``source_id`` becomes real columns (#309): a consumer reads
    ``span_kind``/``span_discriminator``/``span_start_biennium`` instead of
    splitting a string. A member with no registry key drops (inner join) and is
    counted — an unregistered identity must never publish a headless
    assignment.
    """
    rows: list[dict[str, Any]] = []
    counters = {"spans": len(spans), "unregistered_spans": 0}
    for span in spans:
        entity_id = entity_by_key.get(f"{SOURCE}:{span.member_id}")
        if entity_id is None:
            counters["unregistered_spans"] += 1
            continue
        rows.append(
            {
                "entity_id": entity_id,
                "member_id": span.member_id,
                "source": SOURCE,
                "span_kind": span.kind,
                "span_discriminator": span.discriminator,
                "span_start_biennium": span.start_biennium,
                "span_end_biennium": span.end_biennium,
                "valid_from": span.valid_from,
                "valid_to": span.valid_to,
                "is_active": span.is_active,
            }
        )
    counters["published"] = len(rows)
    return rows, counters
