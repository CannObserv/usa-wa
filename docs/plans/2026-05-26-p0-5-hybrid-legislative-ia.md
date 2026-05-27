---
title: P0.5 — hybrid legislative IA + OCD/LegiScan/uscongress transformations
date: 2026-05-26
status: in-progress (open questions resolved 2026-05-27)
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

1. **Hybrid IA v0 draft.** Write `docs/specs/2026-05-27-hybrid-legislative-ia.md` covering the revised entity skeleton. Incorporates the four high-impact delta findings *and* the open-question resolutions:
   - **LegislativeSession** as first-class entity with classification (regular/special), start/end, active flag, and a natural-key slug following the OpenStates convention extended for our jurisdiction encoding: `<jurisdiction_id>-<year>[-<session_suffix>]` (e.g., `usa-wa-2025`, `usa-wa-2025-special-1`). (OQ2)
   - **Identity layer adopts power-map terminology** (OQ4 + [[project-identity-producer-archival]]): `Person` (was `Legislator`), `Organization` (covers chambers, parties, committees, lobbying orgs, candidate committees), `Role` (Senator/Representative/Chair/Vice-chair/Member/Speaker/etc., as named slots within an Organization), `Assignment` (Person × Role × Period). No standalone `Legislator`, `Committee`, or `Filer` entities — those become Organizations or Person+Assignment compositions. Each entity carries source/source_id for upsert and is *staged* for eventual push to power-map as the long-term archival store; current MVP keeps a local copy for query latency.
   - **Vote cluster — flexible enough for committee + floor + amendments + motions** (OQ3). Schema: `VoteEvent` (polymorphic subject via `subject_type` ∈ {bill, amendment, motion} + `subject_id` ULID + nullable concrete-FK columns for query efficiency; `context_type` ∈ {floor, committee}; `context_organization_id` FK to Organization; chamber; event_at). `VoteCount` (vote_event_id, count_type ∈ {yea, nay, excused, absent, other}, value). `PersonVote` (vote_event_id, person_id, vote ∈ {yea, nay, abstain, excused, absent}). PersonVote materialized in P1a — no deferral.
   - **Amendment** as a new first-class entity (consequence of OQ3) — Amendment(bill_id, source/source_id, label, sponsor_person_id, status, offered_at, adopted_at?, withdrawn_at?). VoteEvent can reference an Amendment as its subject.
   - **Polymorphic BillSponsorship retained for Layer 2 reusability** (OQ1 + multi-jurisdiction principle): supports both `person_id`-sponsored (WA's only mode) and `organization_id`-sponsored (federal Congress, some states). WA adapter never emits committee-sponsorship rows. 4-value role vocab + `sponsor_order` + nullable `withdrawn_at`.
   - **`Bill.title` ↔ `Bill.short_description` swap** to match industry convention.
   - **Power-map column-shape**: `Person.powermap_person_id`, `Organization.powermap_org_id` stay as standalone nullable ULID columns. No graduation to JSONB `external_ids` (power-map already gives us the canonical id directly).
   - **Producer/archival framing** documented in a dedicated spec section: usa-wa is both a query layer and an identity-data producer for power-map. Local cache for query latency, archival truth upstream, primary-source resilience as a side effect. See [[project-identity-producer-archival]].

   Verify: file exists; every bullet above is reflected in the spec; cross-references to delta note + power-map research note included; OpenStates session-slug convention explicit.

2. **Three transformation specs in parallel** — dispatch as three background agents (one prompt each, mirrors the P0 discovery dispatch pattern). Outputs:
   - `docs/specs/2026-05-26-transformation-ocd.md` — bidirectional mapping between hybrid IA v0 and Open Civic Data / OpenStates schema. Entity correspondence table, field-level mapping with transforms, vocabulary alignment (action types, status, sponsor roles, vote outcomes), explicitly-flagged lossy directions.
   - `docs/specs/2026-05-26-transformation-legiscan.md` — same shape against LegiScan's API schema.
   - `docs/specs/2026-05-26-transformation-uscongress.md` — same shape against `github.com/unitedstates/congress` schemas.

   Each agent returns a short summary listing any IA-v0 revisions its transformation surfaced. Verify: three files exist, each ends with a recommendation block calling out revisions, all three return summaries consumed in step 3.

3. **Hybrid IA v1 — finalize.** Edit `docs/specs/2026-05-26-hybrid-legislative-ia.md` to incorporate revisions surfaced by the transformations. Bump status to `final`. Cross-link to the three transformation specs. Document the deferred-but-acknowledged concerns from the delta (per-legislator vote rows in P3, NCSL inaccessibility). Verify: file's status is `final`; every transformation-revision is either reflected or explicitly deferred with rationale.

4. **Implement revised schema in `clearinghouse-domain-legislative`.** Apply v1 to the SQLAlchemy models. Substantial restructure:
   - **Delete** `Legislator`, `Committee`, `Filer` as standalone tables. Their concepts become `Person`, `Organization`, and `Assignment` instances.
   - **Add identity cluster**: `Person`, `Organization` (with `org_type` discriminator: chamber / party / committee / candidate_committee / lobbying_firm / pac / other), `Role` (with `name` slug and `organization_id` FK), `Assignment` (Person × Role × Period with `valid_from` / `valid_to`).
   - **Add** `LegislativeSession`; replace `Bill.biennium` text column with `legislative_session_id` FK.
   - **Add** `Amendment` (linked to Bill).
   - **Add Vote cluster**: `VoteEvent` (polymorphic subject), `VoteCount`, `PersonVote`.
   - **Restructure** `BillSponsorship`: rename `legislator_id` → `person_id` (nullable), add `organization_id` (nullable), expand `role` vocab, add `sponsor_order`, add `withdrawn_at`.
   - **Rename / swap** in `Bill`: `title` ↔ `short_description`.
   - **Reshape** PDC models: `LobbyingActivity.filer_id` becomes either `person_id` (individual lobbyists) or `organization_id` (lobby firms) — likely both nullable with at-least-one constraint. `Contribution.recipient_filer_id` becomes `recipient_organization_id` (candidate committees are Organizations). `Contribution.contributor_filer_id` becomes nullable `contributor_person_id` + nullable `contributor_organization_id`.

   Update the smoke tests for each cluster. Verify: `uv run pytest` passes; `uv run ruff check .` passes; the entity smoke tests cover one instantiation per cluster (Bill, Person, Organization, Assignment, VoteEvent, Statute, Amendment).

5. **First alembic migration for `canonical.*`.** Write `alembic/versions/2026_05_NN_canonical_init.py` creating the `canonical` schema and every revised legislative-domain table. Short revision id (≤32 chars: `20260526_canonical_init` or similar). No data seed in this migration. Apply against the dev DB; verify the test DB still works via `Base.metadata.create_all`. Verify: `uv run alembic upgrade head` succeeds; `psql -c '\dn'` shows both `clearinghouse_core` and `canonical` schemas; `\dt canonical.*` lists all revised tables; tests still green.

6. **Power-map integration sub-spec.** Write `docs/specs/2026-05-27-power-map-integration.md` consuming the power-map research note and CannObserv/power-map#156. Documents:
   - **Producer/archival framing**: usa-wa as identity-data producer for power-map; the long-term direction is push-to-archive (Organizations, People, Roles, Assignments). Local Postgres cache survives State-resource outages because the canonical-identity truth lives upstream.
   - **Read flow (P2)**: pull Org identity from power-map's existing endpoints (`orgs/search` + `orgs/{id}`); blocked on identifier-filter ask (power-map#156 §2) to be efficient. Person identity blocked on People endpoints (power-map#156 §1).
   - **Write flow (P3+ deferred)**: observation/upsert design (power-map#156 §9) — what attribute set usa-wa would push for a Person (name + WSL member_id + chamber/district from current Assignment), for an Organization (name + WSL committee_id or PDC filer_id + org_type), for a Role/Assignment.
   - **Upstream-dependency matrix**: each of power-map#156's 11 asks → which usa-wa phase gates on it.
   - **Column-shape commitment**: nullable `powermap_person_id` / `powermap_organization_id` FKs on Person and Organization; no JSONB `external_ids` bag.
   - **Staging strategy**: between MVP and the day power-map's write API ships, usa-wa keeps its own identity records in `canonical.*`. When the write API lands, a one-shot backfill job pushes the staged records up, then steady-state push-on-update.

   Verify: file exists; each section above present; cross-references to #156 and the power-map research note resolve.

7. **Update MVP spec.** Edit `docs/specs/2026-05-25-usa-wa-mvp-design.md` to (a) replace the P0-skeleton entity descriptions in the "Canonical data spine" section with pointers to the hybrid IA spec (don't duplicate; one source of truth), (b) update the open-questions list to reflect resolutions from this plan, (c) close the P0.5 status loop in the frontmatter. Verify: no entity-by-entity descriptions remain duplicated between the MVP spec and the hybrid IA spec; status line reads `P0.5 complete; P1a planning unblocked`.

Steps 1 and 6 may proceed in parallel. Step 2's three agents run in parallel with each other but must complete before step 3. Steps 4 and 5 are sequential (models before migration). Step 7 is the last step (depends on all prior).

## Open questions / risks

**Resolved 2026-05-27 (user input):**

1. ~~**Committee-sponsor reality in WSL SOAP.**~~ **Resolved: committees do NOT sponsor bills in WA.** WA adapter never emits committee-sponsorship rows. Layer 2 retains polymorphic sponsorship for cross-jurisdiction reusability (federal Congress uses it).
2. ~~**Biennium identifier convention.**~~ **Resolved: adopt OpenStates convention** — `LegislativeSession.slug` follows `<jurisdiction_id>-<year>[-<session_suffix>]` (e.g., `usa-wa-2025`, `usa-wa-2025-special-1`). Transformation specs map OpenStates' `wa-2025` ↔ our `usa-wa-2025`.
3. ~~**Vote scope in P1a vs P3.**~~ **Resolved: PersonVote materialized in P1a.** Votes are fundamental measures. VoteEvent must be flexible enough to model committee votes on bills *and* amendments, floor votes on motions *and* amendments. Schema reflects this with polymorphic subject (bill / amendment / motion) and polymorphic context (floor / committee).
4. ~~**Legislator vs LegislatorMembership split.**~~ **Resolved: adopt power-map terminology** — Person / Organization / Role / Assignment. No standalone Legislator / Committee / Filer entities. Long-term: usa-wa is a producer of identity data for power-map ([[project-identity-producer-archival]]); local cache for query latency, archival truth upstream.
5. ~~**NCSL access.**~~ **Resolved: skip.** Two transformation specs (OCD, LegiScan, uscongress remain; NCSL was a research input not a transformation target).

Remaining risks:

- **Power-map upstream timing.** [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156) is the upstream feature request. Step 6's recommendations are sensitive to which asks the power-map team picks up. If the People endpoints (#1) and identifier-filter (#2) land in weeks, P2 stays close to schedule; if months, P2 read-flow stalls. The sub-spec should document the dependency matrix explicitly so phase shifts are easy to see.
- **Alembic version_num length cap.** Default 32-char limit. Use a short revision id like `20260526_canonical_init` for step 5's migration (continuing the convention set in `20260526_chcore_init`).
- **Schema-change blast radius post-MVP.** Once usa-wa has real data in `canonical.*`, schema changes become migrations under load. Take an extra pass on the revised models before merging step 4 — we don't get another cheap chance.
- **Test database lifecycle.** `Base.metadata.create_all` + `drop_all` already handles schemas dynamically. Step 5's migration shouldn't affect tests, but verify after applying.
