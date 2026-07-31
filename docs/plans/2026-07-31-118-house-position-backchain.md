# #118 Phase 1 — back-chain House Position through continuous tenure

## Problem

House `state_representative` Position spans floor at 2009-01-01 (the 2008 general, the
first full-House ballot in the archive). Pre-2009 House members carry party + committee
coverage but no Position seat. No pre-2008 House ballot evidence is reachable
(`results.vote.wa.gov` House floor = Nov 2008; edge confirmed in #140).

## Approach

A WA rep runs for a *specific* Position and holds it continuously, so a ballot-anchored
Position propagates **backward** through an uninterrupted same-LD tenure. Seeding one seat
in an LD then lets the existing #103 within-LD elimination resolve the mate.

**Phase 1 (this plan):** back-chain *ballot-anchored* members' positions through their own
same-era tenure (depth-capped) + one round of within-biennium #103 elimination per reached
biennium. Do **not** recursively back-chain elimination-resolved mates (that is Phase 2).

Reachable window = the 2001 redistricting map-era pre-2009 bienniums: **2003-04, 2005-06,
2007-08**. Earlier eras have no reachable anchor (#140).

## Design

Runs inside the ONE builder (`house.build.build_house_position_spans`), before
`build_tenure_spans`, so daily (`restrict_to_biennium`=current) and backfill share span
identity — no #100-CR depth mismatch. Idempotent (same rosters + ballots each run).

1. **`house.projector.build_house_seat_observations`** — add `seed_positions: {ld:
   {member_id: qualifier}}`. Two-pass within each LD: ballot matches first, then apply a
   seed to an otherwise-unmatched member rostered in that LD (only if the seeded qualifier
   isn't already ballot-claimed), then #103 elimination over the combined resolved set.
   Return `matched_keys` + `seeded_keys` (both → `inferred_keys` for roster citation, but
   `matched_keys`/`seeded_keys` distinguish carry-back eligibility).

2. **`house.backchain.backchain_house_observations`** (new, pure) — walk biennia
   newest→oldest, maintaining a seed map. Per biennium: project (with the seeds carried from
   its successor), then for each **ballot-class** member (ballot-matched hops=0, or applied
   seed hops=k) seed the previous biennium (start−2) under the member's current LD with
   `(qualifier, hops+1)` — **unless** the current biennium starts a new map-era
   (`REDISTRICTING_ERA_START_BIENNIA = {1993-94, 2003-04, 2013-14, 2023-24}`) or
   `hops+1 > max_hops`. The projector applies a seed only if the member is rostered in that
   LD that biennium, so an LD move or a tenure gap breaks the chain naturally.
   Elimination-only members are emitted but never carried back (Phase-1 boundary).

3. **`house.build`** — build `roster_by_biennium` up front, call the orchestrator, add
   `--max-backchain-hops` (default `MAX_BACKCHAIN_HOPS_DEFAULT = 4`, covers the era; the
   era break is the hard stop). Log per-seat depth (`house_seat_backchained`).

Citations: back-chained seats are `inferred` → cite `sponsors:<biennium>` (the wire that
names the member), the #103 precedent. (Field-citing the seeding ballot is a Phase-1.1
nicety, deferred.)

## Tradeoffs

- Carry-back only ballot-class positions (not eliminated mates) — conservative; leaves some
  238-cohort members for Phase 2. Accepts under-coverage to avoid compounding a wrong hop
  into a false structural seat (the #101 "absence is honest" stance).
- `max_hops` default 4 covers the reachable era; the era-start break dominates correctness.
- **Generality beyond pre-2009 (CR finding 6).** The mechanism fills *any* interior gap where a
  later same-era ballot-class position exists — e.g. a post-2009 member whose one biennium missed a
  name-match, sandwiched between two anchors, is now healed. Correct and beneficial (mid-tenure
  match-miss holes close), but broader than the "pre-2009" framing; intended, not a surprise.

## Steps

1. TDD projector `seed_positions` (red→green).
2. TDD `backchain_house_observations` (era break, gap break, LD-move break, multi-hop,
   1-hop elimination cascade, max_hops).
3. Wire `build.py`; extend `test_house_build` for a back-chained pre-2009 span.
4. Full suite + ruff.

## Deploy (CR finding 1 — span-deepening → PM-orphan risk)

Back-chain runs in the **daily** path too, so the first post-deploy run deepens a long-tenured
current member's span — its `source_id` start moves earlier (`…:2009-10` → `…:2003-04`), the old
anchored Assignment is superseded, a new one created. Without care the sidecar mints a new PM
assignment for the deep row and **orphans** the old row's anchor (the #108/#80 class). Sequence the
deploy exactly like #101:

1. **Pause the sidecar.**
2. Run `house.build` (backfill), then `house.migrate` — its #103 `_superseded_pairs` pass collapses
   the deeper-start `usa_wa_legislature` row onto the shallower keeper and transfers the PM anchor.
   No migrate code change needed (the mechanism already exists).
3. **Resume the sidecar.**

Do not merge-and-let-the-timer-run — the daily timer would deepen before the migrate collapses.

## Verification (from #118)

- `house_seat_cohort` `matched`/`inferred`/`seeded` > 0 for 2003-04→2007-08 (today 0).
- No new duplicate occupancy at any biennium start. **(CR finding 5)** `succession_invariants`
  only checks the *live* open-seat cohort — before trusting the unrestricted rebuild, run the
  `--dry-run` backfill and eyeball `seeded` / `house_seat_backchained` counts + the ~10-member
  ground-truth spot-check (#118); a historical duplicate-occupancy assertion is a possible follow-up.
- A continuous tenure across the 2002 boundary produces **two** spans, not one.

## Out of scope

- Phase 2 recursive cascade of eliminated mates.
- Era A (pre-2002) — #140.
- Field-citation to the seeding ballot resource.
