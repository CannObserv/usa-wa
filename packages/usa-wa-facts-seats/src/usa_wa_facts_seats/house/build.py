"""WSL+SOS House Position span builder (#101, Phase B) — the re-partition core.

Reads the WSL sponsor roster (who sits — LD + party, archive-first) and the SOS election-results
archive (the ballot Position 1/2) **offline**, joins them per biennium into positioned tenure
observations (:mod:`normalize.house_seats`), merges those across biennia into
:class:`~clearinghouse_domain_legislative.tenure_spans.TenureSpan`s, and emits one
**``usa_wa_legislature``-sourced** ``state_representative`` Position seat Assignment per tenure —
symmetric with the Senate seat (#75). No PDC winner cohort: PDC is demoted to the
``person_wa_pdc`` cross-link only (:mod:`usa_wa_facts_seats.pdc.build_pdc_spans`, identifier-only).

**One builder, one span identity.** The daily re-drive (``restrict_to_biennium`` = current) and
the historical backfill (``restrict_to_biennium=None``) are the same pipeline with the same SOS
positions, so a member serving across the 2018 boundary builds ONE deep span either way — the
#100 CR finding-1 two-builder depth mismatch cannot recur.

**Coverage.** Position 2008→present directly (the votewa floor), extended back to **2003-04** by
the #118 back-chain (:mod:`.backchain`): a ballot-anchored Position carried through continuous
same-LD tenure, letting the #103 elimination cascade resolve the mate. The 1991-2001 map-era stays
uncovered — no reachable ballot anchor across the 2002 redistricting break (#140). A sitting member
with no resolvable SOS position and no back-chain reach gets no House Position seat (OQ1 — a
positioned seat's absence is honest, not a position-less ``state_representative``, which PM rejects)
— unless the projector's within-LD elimination (#103) resolves it (a mid-biennium appointee or a
ballot↔roster name change). Inferred / back-chained bienniums cite the sponsor roster, not the SOS
cohort. Depends on #77 (Persons + sponsor archive) and the SOS harvest (#100 Phase A).

**Deploy (span-deepening → PM-orphan risk).** Back-chain runs in the daily path too, so the first
post-deploy run deepens a long-tenured current member's span (its ``source_id`` start moves
earlier) — the old anchored Assignment is superseded and a new one created. Sequence the deploy
like #101 to avoid orphaning the old row's PM anchor: **sidecar-paused**, run this builder then
``house.migrate`` (whose #103 ``_superseded_pairs`` pass collapses the deeper-start row onto the
shallower keeper and transfers the anchor), **then** resume the sidecar. Do not merge-and-let-the-
timer-run.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.operator_overlay import (
    apply_operator_events,
    from_rows,
    latest_event_biennium_by_member,
    stale_exempt_members,
)
from clearinghouse_domain_legislative.span_emit import (
    MAX_CLOSE_FRACTION_DEFAULT,
    CitationTarget,
    close_fraction,
    close_stale_spans,
)
from clearinghouse_domain_legislative.tenure_spans import build_tenure_spans
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.adapter import SPONSORS_RESOURCE_PREFIX
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.cohorts import (
    committee_member_provider,
    sponsor_roster_provider,
)
from usa_wa_adapter_legislature.committee_member_cohort import (
    CommitteeMemberCohortProvider,
    MemberClient,
)
from usa_wa_adapter_legislature.operator_events_store import (
    cite_operator_events,
    current_events,
    get_or_create_operator_source,
)
from usa_wa_adapter_legislature.provisioning import (
    get_or_create_source as get_or_create_wsl_source,
)
from usa_wa_adapter_legislature.roster_hygiene import (
    STALE_MIN_COVERAGE_DEFAULT,
    committee_member_ids_by_biennium,
    stale_exclusions_by_biennium,
)
from usa_wa_adapter_legislature.sponsor_cohort import (
    SponsorClient,
    SponsorRosterCohortProvider,
)
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_adapter_sos.results.cohort import SosResultsCohortProvider
from usa_wa_common.ballot import HousePosition, position_for
from usa_wa_common.elections import election_year_for_biennium, election_years_for_biennium
from usa_wa_common.jurisdiction import resolve_jurisdiction
from usa_wa_facts_seats.house.backchain import (
    MAX_BACKCHAIN_HOPS_DEFAULT,
    backchain_house_observations,
)
from usa_wa_facts_seats.house.emit import emit_house_position_spans
from usa_wa_facts_seats.pdc.matching import build_house_roster, house_mover_ids
from usa_wa_facts_seats.pdc.observations import KIND_HOUSE

logger = get_logger(__name__)

_HOUSE_ASSIGNMENT_SOURCE = "usa_wa_legislature"

HousePositionsByLd = dict[int, list[HousePosition]]


def _biennium_election_years(biennium: str) -> tuple[int, int]:
    """``(even_seating_year, odd_special_year)`` for a biennium (#123). ``election_years_for_
    biennium`` returns ``[start-1, start]`` — the even November that seats the chamber and the odd
    November that fills mid-biennium vacancies by special. ``even == odd`` never happens (start-1 is
    always even), so the two are distinct sources to merge."""
    years = election_years_for_biennium(biennium)
    return years[0], years[-1]


def _merge_positions(
    biennium: str,
    positions: dict[int, HousePositionsByLd],
    house_winners: dict[int, HousePositionsByLd],
) -> HousePositionsByLd:
    """The biennium's House position map = even seating candidacies ∪ odd-special **winners**
    (#123 §1). ``position_for`` is name-keyed, so appended entries only *add* resolution power —
    nothing existing is retracted. The even seating cohort keeps its full candidacy set (the #103
    elimination depends on the losers); only the odd side is winner-filtered (hazard b — a losing
    special candidacy must not false-match a member). An absent/empty odd cohort (no special that
    biennium, or the odd November not yet held) leaves the even map unchanged — backward
    compatible with the pre-#123 single-year lookup."""
    even_year, odd_year = _biennium_election_years(biennium)
    merged: HousePositionsByLd = {
        ld: list(entries) for ld, entries in positions.get(even_year, {}).items()
    }
    for ld, entries in house_winners.get(odd_year, {}).items():
        merged.setdefault(ld, []).extend(entries)
    return merged


@dataclass
class HouseSpanResult:
    """Counts from one WSL+SOS House Position span build."""

    house_spans: int = 0
    bienniums: int = 0
    closed_stale: int = 0
    sweep_aborted: bool = False
    coverage: dict[str, dict[str, int]] = field(default_factory=dict)


async def build_house_position_spans(
    session: AsyncSession,
    *,
    sponsor_client: SponsorClient | None = None,
    member_client: MemberClient | None = None,
    current_biennium: str | None = None,
    restrict_to_biennium: str | None = None,
    max_close_fraction: float = MAX_CLOSE_FRACTION_DEFAULT,
    stale_min_coverage: float = STALE_MIN_COVERAGE_DEFAULT,
    max_backchain_hops: int = MAX_BACKCHAIN_HOPS_DEFAULT,
) -> HouseSpanResult:
    """Build + emit ``usa_wa_legislature`` House Position seat spans; return counts.

    ``restrict_to_biennium`` scopes the emission to members observed in that biennium (the daily
    re-drive passes the current biennium — each scoped member keeps their full span history).
    ``None`` (the historical backfill) rebuilds all archived bienniums.

    **Roster hygiene (#105).** Before projection each biennium's roster sheds (a) mover rows —
    a House row whose Id also appears in a named Senate row of the same wire (the
    ``build_house_roster`` Id exclusion), and (b) committee-corroborated stale rows — a named
    member absent from that biennium's committee-roster archive (:mod:`roster_hygiene`, guarded
    by ``stale_min_coverage``). Both turn a ghost-blocked 3-member LD back into the 2-member
    shape the #103 elimination can seat an appointee in, and drop the ghost's seat assertion so
    the #83 sweep closes it. ``member_client`` re-parses the committee archive offline."""
    jurisdiction = await resolve_jurisdiction(session)
    wsl_source = await get_or_create_wsl_source(session, jurisdiction)
    sos_source = await get_or_create_results_source(session, jurisdiction)
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=current, jurisdiction_id=jurisdiction.id
    )

    # Default to the WSL adapter's own factory (#189): the fact names a cohort, never a
    # transport. An injected client is typed by the provider's structural Protocol, so a
    # test double needs no SOAP stack.
    sponsors = (
        SponsorRosterCohortProvider(sponsor_client, session=session, source_id=wsl_source.id)
        if sponsor_client is not None
        else sponsor_roster_provider(session, source_id=wsl_source.id)
    )
    member_cohort = (
        CommitteeMemberCohortProvider(member_client, session=session, source_id=wsl_source.id)
        if member_client is not None
        else committee_member_provider(session, source_id=wsl_source.id)
    )
    committee_ids = committee_member_ids_by_biennium(await member_cohort.archived_rosters())
    sos = SosResultsCohortProvider(session=session, source_id=sos_source.id)
    positions = await sos.house_positions()
    house_winners = await sos.house_winners()
    citation_events = await sos.citation_events()
    bienniums = await sponsors.archived_bienniums()

    roster_events: dict[str, CitationTarget] = {
        biennium: (event_id, fetched_at, f"{SPONSORS_RESOURCE_PREFIX}{biennium}")
        for biennium, (event_id, fetched_at) in (await sponsors.fetch_event_map(bienniums)).items()
    }
    fetch_events: dict[str, CitationTarget] = {}
    result = HouseSpanResult(bienniums=len(bienniums))
    rows_by_biennium = {biennium: await sponsors.cohort(biennium) for biennium in bienniums}
    exclusions = stale_exclusions_by_biennium(
        rows_by_biennium, committee_ids, min_coverage=stale_min_coverage
    )
    # Operator-succession overlay (#107 / #145). An event-touched member is exempt from the #105
    # stale exclusion (keep_ids) so their House span is built for a `vacated` to date. But an
    # event-touched **mover** is deliberately NOT kept: re-including a mover in the roster re-runs
    # the #103 elimination and splits the backfiller (the reverted 2013-14 tranche). Instead the
    # overlay synthesizes the mover's closed House tenure from the `vacated`, gated on the
    # per-biennium mover signal passed below — so keep_ids = event members minus this biennium's
    # movers. (A future stale non-mover perturbing the elimination would need the same synth path.)
    # The keep is **biennium-scoped** (#145 CR): `stale_exempt_members` exempts a member only
    # through their latest event biennium, so an event-touched member who genuinely departs later
    # (a committee-stale ghost) is no longer kept in that later roster — mirroring the sponsor
    # builder's fix so a House member's span isn't stretched past their real departure.
    event_rows = list(await current_events(session))
    events = from_rows(event_rows)
    latest_event_biennium = latest_event_biennium_by_member(events)
    movers_by_biennium = {
        biennium: house_mover_ids(rows_by_biennium[biennium]) for biennium in bienniums
    }
    roster_by_biennium = {
        biennium: build_house_roster(
            rows_by_biennium[biennium],
            exclude_ids=exclusions.get(biennium, set()),
            keep_ids=stale_exempt_members(latest_event_biennium, biennium)
            - movers_by_biennium[biennium],
        )
        for biennium in bienniums
    }
    positions_by_biennium = {
        biennium: _merge_positions(biennium, positions, house_winners) for biennium in bienniums
    }
    # #123 §1c citation plumbing: a member whose position resolves from the odd-year special
    # cohort (and NOT the even seating cohort) is cited to the odd wire (`sos-legresults:<odd>`) —
    # the document that actually seated the mid-biennium appointee — not the even seating cohort.
    # Mirrors the `inferred_keys`/`roster_events` precedent. `special_events` maps the biennium to
    # its odd cohort's attesting FetchEvent.
    special_keys: set[tuple[str, str]] = set()
    special_events: dict[str, CitationTarget] = {}
    for biennium in bienniums:
        even_year, odd_year = _biennium_election_years(biennium)
        odd_event = citation_events.get(odd_year)
        odd_map = house_winners.get(odd_year, {})
        if odd_event is None or not odd_map:
            continue
        special_events[biennium] = odd_event
        even_map = positions.get(even_year, {})
        merged_map = positions_by_biennium[biennium]
        for ld, entries in roster_by_biennium[biennium].items():
            for entry in entries:
                even_hit = position_for(even_map, ld, entry.folded_last, entry.party_slug)
                merged_hit = position_for(merged_map, ld, entry.folded_last, entry.party_slug)
                # The seat came from the odd cohort iff the member's *merged* resolution (what the
                # projector actually seats) differs from what the even seating cohort alone gives.
                # Testing `odd_hit is not None` instead would misroute when the even cohort carries
                # the same folded surname (a loser, or a different person) — `position_for(even, …)`
                # resolves that surname to the wrong position, hiding a genuinely odd-sourced seat.
                if merged_hit is not None and merged_hit != even_hit:
                    special_keys.add((entry.member_id, biennium))
    # #118 back-chain: project every biennium AND carry each ballot-anchored Position back through
    # continuous same-LD tenure (newest→oldest), so a pre-2009 biennium below the SOS floor is
    # seated from a later ballot + the #103 elimination cascade. Same pass daily + backfill, so
    # span identity holds (no #100-CR depth mismatch). Bounded by the redistricting era breaks and
    # ``max_backchain_hops``.
    backchain = backchain_house_observations(
        roster_by_biennium, positions_by_biennium, max_hops=max_backchain_hops
    )
    observations = list(backchain.observations)
    inferred_keys = set(backchain.inferred_keys)
    result.coverage = backchain.coverage
    backchained_by_biennium: dict[str, list[str]] = {}
    for member, biennium in backchain.backchain_keys:
        backchained_by_biennium.setdefault(biennium, []).append(member)
    inferred_by_biennium: dict[str, list[str]] = {}
    for member, biennium in backchain.inferred_keys:
        inferred_by_biennium.setdefault(biennium, []).append(member)
    for biennium in bienniums:
        logger.info(
            "house_seat_cohort",
            extra={"biennium": biennium, **backchain.coverage.get(biennium, {})},
        )
        inferred_members = inferred_by_biennium.get(biennium)
        if inferred_members:
            # #103/#118: name the elimination-seated + back-chained members so the inference is
            # operator-auditable (the PDC #74 precedent — the merged span carries no per-biennium
            # confidence).
            logger.info(
                "house_seat_inferred",
                extra={"biennium": biennium, "members": sorted(inferred_members)},
            )
        backchained_members = backchained_by_biennium.get(biennium)
        if backchained_members:
            # #118: the back-chained subset, with the max hop depth from a ballot anchor so an
            # operator can gauge the inference distance (confidence decays with depth).
            logger.info(
                "house_seat_backchained",
                extra={
                    "biennium": biennium,
                    "members": sorted(backchained_members),
                    "max_depth": max(
                        backchain.depth[(member, biennium)] for member in backchained_members
                    ),
                },
            )
        event = citation_events.get(election_year_for_biennium(biennium))
        if event is not None:
            fetch_events[biennium] = event

    # Daily re-drive: "observed" keys on a *resolved SOS position*, not mere roster presence
    # (unlike the sponsor/PDC re-drives), so a current member the SOS archive can't position drops
    # out here and their open seat is swept closed below. Safe only because the SOS results archive
    # is immutable within a biennium and the WSL folded surname is stable → position_for is
    # deterministic across daily runs (a genuine flip is a real data change); max_close_fraction
    # bounds any mass close. The elimination pass (#103) adds a roster-composition dependence: a
    # second same-LD mid-biennium departure blanks the ballot-matched member, retracting the
    # earlier inference — the appointee's seat then sweeps closed until data improves (rare,
    # self-limiting, no worse than the pre-#103 gap).
    if restrict_to_biennium is not None:
        observed = {o.member_id for o in observations if o.biennium == restrict_to_biennium}
        observations = [o for o in observations if o.member_id in observed]

    built_spans = build_tenure_spans(observations, current_biennium=current)
    spans = apply_operator_events(
        built_spans,
        events,
        current_biennium=current,
        owned_kinds={KIND_HOUSE},
        movers_by_biennium=movers_by_biennium,
    )
    # Operator-synthesized spans (a House appointee the ballot/roster hasn't positioned) skip
    # the roster/ballot citation — the operator field citation is their sole attestation (#107).
    synthesized_ids = {s.source_id for s in spans} - {s.source_id for s in built_spans}
    result.house_spans = await emit_house_position_spans(
        session,
        spans,
        anchors=anchors,
        reliability=sos_source.reliability,
        fetch_events=fetch_events,
        roster_events=roster_events,
        inferred_keys=inferred_keys,
        special_keys=special_keys,
        special_events=special_events,
        assignment_source=_HOUSE_ASSIGNMENT_SOURCE,
        skip_citation_ids=synthesized_ids,
    )
    if event_rows:
        operator_source = await get_or_create_operator_source(session, jurisdiction)
        await cite_operator_events(
            session,
            event_rows,
            spans,
            owned_kinds={KIND_HOUSE},
            assignment_source=_HOUSE_ASSIGNMENT_SOURCE,
            confidence=operator_source.reliability,
        )
    # #83: a departed member keeps no observation in the (possibly restricted) rebuilt set, so
    # their open chamber-house span would stay is_active forever — close it.
    sweep = await close_stale_spans(
        session,
        assignment_source=_HOUSE_ASSIGNMENT_SOURCE,
        kinds={KIND_HOUSE},
        asserted_source_ids={s.source_id for s in spans},
        current_biennium=current,
        max_close_fraction=max_close_fraction,
    )
    result.closed_stale = sweep.closed
    result.sweep_aborted = sweep.aborted
    logger.info(
        "house_span_build_complete",
        extra={
            "bienniums": result.bienniums,
            "house_spans": result.house_spans,
            "closed_stale": sweep.closed,
            "sweep_aborted": sweep.aborted,
            "restricted": restrict_to_biennium,
        },
    )
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Build WSL+SOS House Position seat spans from archive (#101)."
    )
    parser.add_argument("--dry-run", action="store_true", help="build but roll back (preview)")
    parser.add_argument(
        "--biennium",
        default=None,
        help="the current operating biennium (e.g. 2025-26): scope the rebuild to its members "
        "(each keeps full span history) AND treat it as the span open-end / stale-close "
        "boundary. Omit for a full historical rebuild",
    )
    parser.add_argument(
        "--max-close-fraction",
        type=close_fraction,
        default=MAX_CLOSE_FRACTION_DEFAULT,
        help="mass-close guard ceiling in (0, 1] (#83); 1.0 disables the guard",
    )
    parser.add_argument(
        "--stale-min-coverage",
        type=float,
        default=STALE_MIN_COVERAGE_DEFAULT,
        help="committee-roster coverage floor for the #105 stale-row exclusion; a biennium "
        "under it is skipped. >1 disables the exclusion entirely (audit via --dry-run logs)",
    )
    parser.add_argument(
        "--max-backchain-hops",
        type=int,
        default=MAX_BACKCHAIN_HOPS_DEFAULT,
        help="cap on #118 back-chain hops from a ballot anchor (pre-2009 Position depth); the "
        "redistricting era break is the hard stop. 0 disables back-chaining",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await build_house_position_spans(
                session,
                current_biennium=args.biennium,
                restrict_to_biennium=args.biennium,
                max_close_fraction=args.max_close_fraction,
                stale_min_coverage=args.stale_min_coverage,
                max_backchain_hops=args.max_backchain_hops,
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("house_span_build_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"House Position span build: house_spans={result.house_spans} "
        f"bienniums={result.bienniums} closed_stale={result.closed_stale} "
        f"sweep_aborted={result.sweep_aborted} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
