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

from clearinghouse_core.logging import get_logger
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
from usa_wa_adapter_legislature.roster_pdf.build import (
    OracleViolation,
    unattested_spans,
    verify_pre1991,
)
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_WSL,
    ROSTER_IDENTITY_FLOOR,
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

logger = get_logger(__name__)

#: The WSL archive: numeric member ids, 1991-.
SOURCE = "usa_wa_legislature"

#: The roster-PDF source: minted `<fold>:<first-session-year>` identities,
#: pre-1991. A DISJOINT identity space sharing the same assignments table.
ROSTER_SOURCE = "usa_wa_legislature_roster"

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
    #: :func:`deepening_observations`). Leaving it empty under a live sponsor
    #: corpus is refused by :func:`build_all_spans` rather than silently
    #: re-asserting the shallow 1991-start keys (CR 57).
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
    malformed = 0
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
            # here is a staging bug the roster key tests catch, not a fact —
            # but it must still leave a trace, never a bare `continue` (CR 63).
            malformed += 1
    if malformed:
        logger.warning(
            "roster_records_malformed",
            extra={"malformed": malformed, "kept": len(records)},
        )
    return records


@dataclass(frozen=True)
class RosterResolution:
    """One resolve of the roster corpus, partitioned by disposition.

    The pre-1991 corpus feeds **two** span families and the resolve is the
    expensive part (~8,600 records), so it runs once and both halves come from
    the same report — resolving twice would also give the halves a chance to
    disagree about who is WSL-joined.

    - :attr:`joined` — the #228 deepening: observations for identities the
      resolve bound to a WSL member id, merged into the WSL family's build so a
      crossing member emits ONE span at its true start (the #97 collapse shape).
    - :attr:`minted` — the roster family's own: identities with no WSL
      counterpart, keyed ``<fold>:<first-session-year>`` in the roster source
      space.
    """

    joined: list[Observation]
    minted: list[Observation]
    #: Every parsed record, refused groups included — the seat-listing index the
    #: §5 truncation bound needs, and the oracle's partition denominator.
    records: list[RosterRecord]


def roster_resolution(
    roster: list[dict[str, Any]], sponsors: list[dict[str, Any]]
) -> RosterResolution:
    """Staging rows → the resolved, oracle-checked pre-1991 projection.

    Ported from ``roster_pdf.build.build_pre1991`` steps 1–3 and
    ``roster_pdf.deepening``: same resolve, same projection, same acceptance
    oracle — but over staging rows, so no provenance-table read and no WSL
    re-pull. The oracle is imported unchanged and runs **before** anything is
    built, exactly as the Postgres tier runs it before anything is written:

    - partition exactness and person-side Senate simultaneity
      (``verify_pre1991``);
    - the party vocabulary — an edition introducing an unclassified
      abbreviation aborts rather than publishing a member with no party.

    Raises :class:`ValueError` when roster rows were supplied but **none
    parsed** (CR 67): an empty result is indistinguishable downstream from "no
    deepening applies", so a roster tier broken by an upstream rename would
    otherwise reach the same silent shallow publish :func:`build_all_spans`
    refuses at the other end.
    """
    if not roster:
        return RosterResolution(joined=[], minted=[], records=[])
    records = roster_records(roster)
    if not records:
        raise ValueError(
            f"the #228 deepening parsed 0 records from {len(roster)} roster rows — every "
            "one malformed, so the roster staging shape changed. Publishing now would "
            "re-assert shallow 1991-start spans. (The count is carried here rather than "
            "left to the roster_records_malformed warning: under a dbt build, where this "
            "raise fires, that warning is invisible — see docs/PIPELINE.md.)"
        )
    report = resolve_identities(records, seatings=seatings_from_sponsors(sponsors))
    verify_pre1991(
        report.identities,
        [r for r in records if r.year < ROSTER_IDENTITY_FLOOR],
        refused_records=sum(len(ref.records) for ref in report.refused),
    )
    projection = build_pre1991_observations(report.identities, records)
    if projection.unrecognized_parties:
        raise OracleViolation(
            f"unrecognized party tokens: {dict(projection.unrecognized_parties)} — a new "
            "edition introduced an abbreviation nobody has classified"
        )
    joined_members = {i.wsl_member_id for i in report.identities if i.disposition == IDENTITY_WSL}
    return RosterResolution(
        joined=[o for o in projection.observations if o.member_id in joined_members],
        minted=[o for o in projection.observations if o.member_id not in joined_members],
        records=records,
    )


def deepening_observations(
    roster: list[dict[str, Any]], sponsors: list[dict[str, Any]]
) -> list[Observation]:
    """The #228 deepening alone — :attr:`RosterResolution.joined`.

    Kept for callers that build only the WSL family; a build wanting both
    families should call :func:`roster_resolution` once and pass its halves.
    """
    return roster_resolution(roster, sponsors).joined


def build_roster_spans(
    resolution: RosterResolution,
    *,
    events: list[Any],
    current_biennium: str,
    context_spans: list[TenureSpan] | None = None,
) -> list[TenureSpan]:
    """The roster family: pre-1991 tenure for the MINTED identities.

    The conformed analog of ``roster_pdf.build.build_pre1991``'s emission half,
    minus everything that existed to mutate Postgres — minting Persons,
    retiring unasserted rows and Persons, the anchor bootstrap, the citation
    writes. A recomputed transform expresses all of that as absence.

    What is kept, because each is a guard rather than a write:

    - the **operator overlay**, scoped to this family's own members. Every
      pre-1991 span is this builder's, so the roster's 922 dated mid-term
      boundaries take effect here or nowhere (#226) — without it a resignation
      dated June 1930 leaves the span ending at its biennium floor.
    - the **unattested-span check**: the overlay can *synthesize* a span, and a
      synthesized one names a seat the edition never listed. The Postgres tier
      aborts rather than emit it citing an edition that never listed the
      member; absent citations here, it still means the build inferred a seat
      from an event, which the roster cannot attest.

    ``context_spans`` (#267) are read-only spans of OTHER kinds used to find a
    departed member's return. In the Postgres tier they come from a DB read; in
    one pass they are the WSL family built alongside. They can only match when
    a minted identity holds an other-kind span — none do today (the WSL family
    keys on numeric member ids), but `chamber-house` from the facts-seats port
    will be the first that could, so the seam is wired rather than assumed shut.
    """
    if not resolution.minted:
        return []
    members = {o.member_id for o in resolution.minted}
    # Scoped to this family's members, mirroring the adapter: matching is by
    # member_id anyway, so an unscoped overlay reads thousands of WSL-era rows
    # to apply none of them.
    scoped = [e for e in from_rows(events) if e.member_id in members]
    built = build_tenure_spans(resolution.minted, current_biennium=current_biennium)
    spans = apply_operator_events(
        built,
        scoped,
        current_biennium=current_biennium,
        owned_kinds=set(SPONSOR_KINDS),
        context_spans=context_spans or [],
    )
    if unattested := unattested_spans(spans, built):
        raise OracleViolation(
            "the operator overlay synthesized a span in the roster family, which the "
            "edition never listed — it would publish a seat inferred from an event "
            f"alone: {sorted(s.source_id for s in unattested)[:5]}"
        )
    return sorted(spans, key=lambda s: (s.member_id, s.kind, s.discriminator, s.start_biennium))


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

    Raises ``ValueError`` when the #228 deepening would be derived from an
    **empty** roster under a non-empty sponsor corpus (CR 57). That combination
    silently re-asserts shallow 1991-start spans (the #97 collapse shape), and
    nothing downstream can catch it: the publish shrink gate compares row
    counts, which barely move when the key set shifts, and the parity probe
    runs after publish. Pass ``extra_observations`` — ``[]`` included — to
    state the deepening instead of deriving it.
    """
    events = from_rows(inputs.events)
    rosters = committee_rosters(inputs.committee_members)
    roster_map = sponsor_wire_rows(inputs.sponsors)
    if extra_observations is None and inputs.sponsors and not inputs.roster:
        raise ValueError(
            "the #228 deepening needs the roster tier: SpanInputs.roster is empty under a "
            f"corpus of {len(inputs.sponsors)} sponsor rows, which would publish shallow "
            "1991-start spans. Pass roster rows, or extra_observations to state the "
            "deepening explicitly."
        )
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
    spans_by_source: dict[str, list[TenureSpan]], entity_by_key: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Span families ⨝ the person crosswalk → published rows + counters.

    Keyed by source because the two families live in **disjoint identity
    spaces** and share one table: the WSL family's member ids are the archive's
    numeric ids, the roster family's are minted ``<fold>:<year>`` keys, and the
    crosswalk lookup is ``<source>:<member_id>`` for both. A row must therefore
    name the source its key belongs to, not inherit a module default.

    The span ``source_id``'s parts become real columns (#309): a consumer reads
    ``span_kind``/``span_discriminator``/``span_start_biennium`` instead of
    splitting a string — which for the roster family is not even possible from
    the left, since its member ids contain a colon (CR 58).

    A member with no registry key drops (inner join) and is counted — an
    unregistered identity must never publish a headless assignment.
    """
    rows: list[dict[str, Any]] = []
    counters = {
        "spans": sum(len(spans) for spans in spans_by_source.values()),
        "unregistered_spans": 0,
    }
    for source, spans in spans_by_source.items():
        for span in spans:
            entity_id = entity_by_key.get(f"{source}:{span.member_id}")
            if entity_id is None:
                counters["unregistered_spans"] += 1
                continue
            rows.append(
                {
                    "entity_id": entity_id,
                    "member_id": span.member_id,
                    "source": source,
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
