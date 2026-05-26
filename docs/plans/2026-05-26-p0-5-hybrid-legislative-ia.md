---
title: P0.5 — hybrid legislative IA + OCD/LegiScan/uscongress transformations
date: 2026-05-26
status: draft
---

# P0.5 — hybrid legislative IA + adapter transformations

Tracks [GH #3](https://github.com/CannObserv/usa-wa/issues/3). Sits between P0 (complete, refs #2) and P1a (next, blocked by this work).

## Problem

P0's multi-state IA delta ([note](../research/2026-05-26-multi-state-legislative-ia-delta.md)) surfaced three structural gaps in our `clearinghouse-domain-legislative` skeleton that are too consequential to absorb mid-P1a: `LegislativeSession` must be a first-class entity (not a `biennium` text column), the Vote cluster is missing entirely, and `BillSponsorship` cannot represent committee-sponsored bills. The fixes interact — fixing sessions cascades into votes, hearings, and future legislator-memberships — so they need to be designed as a coherent revision before any normalization code lands. The work also produces transformation specs against three industry references (OpenStates/OCD, LegiScan, uscongress) that prove our IA is complete by exercising it against three real schemas and double as blueprints for future *indirect-provider* adapters (fall back to OpenStates when WSL is down, corroborate against LegiScan, etc.).

## Approach

Two-pass spec authoring. **Pass 1**: draft a hybrid IA v0 spec that applies the delta's high-impact findings directly (LegislativeSession, Vote cluster, polymorphic sponsorship, title/short_description swap), plus the obvious power-map column-shape decisions from the new research note. **Pass 2**: write three transformation specs *in parallel* (one each for OCD, LegiScan, uscongress) — each exercises hybrid IA v0 against a real foreign schema and surfaces revisions. Synthesize the transformation findings into a final hybrid IA v1 spec. Then implement: revise SQLAlchemy models in `clearinghouse-domain-legislative`, write the first alembic migration for the `canonical.*` schema. Power-map integration lands as a separate sub-spec that consumes the [power-map research note](../research/2026-05-26-power-map-integration-contract.md) and tracks the upstream feature request ([CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156)). Finish by pruning the MVP spec of P0-skeleton entity descriptions superseded by the hybrid IA.

## Tradeoffs / alternatives

- **Skip the transformation specs; just revise our IA from the delta note.** Rejected — the delta surfaced gaps but didn't exercise every concept against every source. Writing the transformations is what catches the field-level mismatches that matter (vocabulary inversions, lossy directions, scope mismatches). Without that pressure, the hybrid IA risks codifying our own blind spots.
- **Author each transformation in its own GH issue / plan / commit.** Rejected — they're tightly coupled to the hybrid IA synthesis. Separating them would cause spec churn (revising hybrid IA every time a new transformation lands) and lose the parallelism win.
- **Defer the `canonical.*` alembic migration to P1a.** Rejected — the migration is mechanical once the SQLAlchemy models are committed; landing it now lets P1a focus purely on adapter code, not entity tuning. Cost-low, blast-radius-zero (no production data depends on canonical tables yet).
- **Materialize the hybrid IA only in spec; write SQLAlchemy code in P1a.** Rejected — implementation is small (~16 models, mostly column declarations) and proves the spec is buildable. Splitting it would defer a real review past the point where revisions are cheap.
- **Author the three transformations in-session, sequentially.** Rejected for the parallel-agent dispatch — same prompt template, independent investigations, ~3× wall-clock improvement and keeps context tight.

## Steps

1. **Hybrid IA v0 draft.** Write `docs/specs/2026-05-26-hybrid-legislative-ia.md` covering the revised entity skeleton. Incorporates the four high-impact delta findings (LegislativeSession as first-class with classification/start/end/active; Vote cluster — VoteEvent + VoteCount + PersonVote, OCD shape; polymorphic BillSponsorship with nullable legislator_id / nullable committee_id / 4-value role / sponsor_order / withdrawn_at; Bill.title ↔ Bill.short_description swap). Notes the power-map column-shape decision (existing nullable `powermap_*_id` columns stay as-is; no graduation to JSONB `external_ids`). Verify: file exists; all four findings reflected; cross-reference to delta note included.

2. **Three transformation specs in parallel** — dispatch as three background agents (one prompt each, mirrors the P0 discovery dispatch pattern). Outputs:
   - `docs/specs/2026-05-26-transformation-ocd.md` — bidirectional mapping between hybrid IA v0 and Open Civic Data / OpenStates schema. Entity correspondence table, field-level mapping with transforms, vocabulary alignment (action types, status, sponsor roles, vote outcomes), explicitly-flagged lossy directions.
   - `docs/specs/2026-05-26-transformation-legiscan.md` — same shape against LegiScan's API schema.
   - `docs/specs/2026-05-26-transformation-uscongress.md` — same shape against `github.com/unitedstates/congress` schemas.

   Each agent returns a short summary listing any IA-v0 revisions its transformation surfaced. Verify: three files exist, each ends with a recommendation block calling out revisions, all three return summaries consumed in step 3.

3. **Hybrid IA v1 — finalize.** Edit `docs/specs/2026-05-26-hybrid-legislative-ia.md` to incorporate revisions surfaced by the transformations. Bump status to `final`. Cross-link to the three transformation specs. Document the deferred-but-acknowledged concerns from the delta (per-legislator vote rows in P3, NCSL inaccessibility). Verify: file's status is `final`; every transformation-revision is either reflected or explicitly deferred with rationale.

4. **Implement revised schema in `clearinghouse-domain-legislative`.** Apply v1 to the SQLAlchemy models: split `Legislator` into `Legislator` + `LegislatorMembership` (decision pending on open question 4 — may stay flat for MVP); replace `Bill.biennium` with `legislative_session_id` FK + a new `LegislativeSession` model; add `VoteEvent` + `VoteCount` + `PersonVote` (scope per open question 3); restructure `BillSponsorship` per v1; swap `Bill.title` / `Bill.short_description`. Update the smoke tests to match. Verify: `uv run pytest` passes; `uv run ruff check .` passes; the entity smoke tests still cover one instantiation per cluster.

5. **First alembic migration for `canonical.*`.** Write `alembic/versions/2026_05_NN_canonical_init.py` creating the `canonical` schema and every revised legislative-domain table. Short revision id (≤32 chars: `20260526_canonical_init` or similar). No data seed in this migration. Apply against the dev DB; verify the test DB still works via `Base.metadata.create_all`. Verify: `uv run alembic upgrade head` succeeds; `psql -c '\dn'` shows both `clearinghouse_core` and `canonical` schemas; `\dt canonical.*` lists all revised tables; tests still green.

6. **Power-map integration sub-spec.** Write `docs/specs/2026-05-26-power-map-integration.md` consuming the power-map research note and CannObserv/power-map#156. Documents: (a) the read-flow contract for P2 (Filer ↔ Org via search + identifier-filter + detail), (b) the deferred write-flow design (observation/upsert), (c) the upstream-dependency matrix mapping power-map#156's 11 asks to usa-wa phase gates, (d) the column-shape decision (nullable `powermap_*_id` FKs, no JSONB). Verify: file exists; section-by-section coverage of read/write/dependencies/columns; cross-references to #156 and the research note.

7. **Update MVP spec.** Edit `docs/specs/2026-05-25-usa-wa-mvp-design.md` to (a) replace the P0-skeleton entity descriptions in the "Canonical data spine" section with pointers to the hybrid IA spec (don't duplicate; one source of truth), (b) update the open-questions list to reflect resolutions from this plan, (c) close the P0.5 status loop in the frontmatter. Verify: no entity-by-entity descriptions remain duplicated between the MVP spec and the hybrid IA spec; status line reads `P0.5 complete; P1a planning unblocked`.

Steps 1 and 6 may proceed in parallel. Step 2's three agents run in parallel with each other but must complete before step 3. Steps 4 and 5 are sequential (models before migration). Step 7 is the last step (depends on all prior).

## Open questions / risks

The five blocking unknowns from the multi-state IA delta need user input before step 1 finalizes:

1. **Committee-sponsor reality in WSL SOAP.** Do WA bills sometimes list a committee (rather than a legislator) as the sponsor? If yes, polymorphic sponsorship is required for P1a; if no, the polymorphism can stay scoped to other jurisdictions and our adapter stays simple. *Likely answer: yes — but worth confirming with a sample fetch before we commit.*
2. **Biennium identifier convention.** When we switch from `Bill.biennium = "2025-26"` text to `Bill.legislative_session_id` ULID, what's the session's natural-key vocabulary? OpenStates uses opaque slugs (`"wa-2025"`, `"wa-2025-special-1"`). Worth adopting that convention or rolling our own? *Recommend: adopt OpenStates' convention for cross-source mapping ease.*
3. **Vote scope in P1a vs P3.** Minimum-viable for P1a: chamber-level vote summary on Bill (`passed_house_67_31`). Full per-legislator detail (`PersonVote` rows × 150 legislators × ~3 final-passage votes per bill × ~5000 bills/biennium = ~2.25M rows/biennium). Materialize VoteEvent in P1a, defer PersonVote to P3? *Recommend: VoteEvent + VoteCount in P1a, PersonVote in P3.*
4. **Legislator vs LegislatorMembership split.** OCD models a Person separately from their Membership in a chamber/session — one Person spans multiple bienniums. Adopt now (cleaner long-term, more refactor cost in P1a), or stay flat with `Legislator(biennium)` rows and split later (less work now, refactor cost later)? *No strong recommendation — depends on whether power-map's `people` model is doing this already.*
5. **NCSL access.** P0's IA delta agent reported 403 from every NCSL URL including archive.org. Do you have a known route or a contact who does? If not, we proceed with OCD's `BILL_ACTION_CLASSIFICATIONS` as the standard proxy and skip a transformation spec for NCSL. *Assume: skip NCSL unless you have access.*

Other risks:

- **Power-map upstream timing.** [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156) is the upstream feature request. Step 6's recommendations are sensitive to which asks the power-map team picks up. If the People endpoints (#1) and identifier-filter (#2) land in weeks, P2 stays close to schedule; if months, P2 read-flow stalls. The sub-spec should document the dependency matrix explicitly so phase shifts are easy to see.
- **Alembic version_num length cap.** Default 32-char limit. Use a short revision id like `20260526_canonical_init` for step 5's migration (continuing the convention set in `20260526_chcore_init`).
- **Schema-change blast radius post-MVP.** Once usa-wa has real data in `canonical.*`, schema changes become migrations under load. Take an extra pass on the revised models before merging step 4 — we don't get another cheap chance.
- **Test database lifecycle.** `Base.metadata.create_all` + `drop_all` already handles schemas dynamically. Step 5's migration shouldn't affect tests, but verify after applying.
