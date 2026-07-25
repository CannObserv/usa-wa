# Committee Lineage & Lifecycle — data-model + PM API contract

**Date:** 2026-07-25
**Status:** Design approved; implementation deferred (blocked on a power-map API feature — see § PM contract).
**Scope:** Model the coherent-timeline representation for re-keyed legislative committees, and specify the power-map API contract it depends on. **No usa-wa code is implemented under this spec** until power-map ships the event-write channel + `succeeded_by` catalog slug. The usa-wa design (§ C) is specified here so the PM contract is validated against a concrete consumer; it becomes its own plan/PR cycle once unblocked.

## Problem

WA Legislature re-keys a standing committee across eras: the same body is renamed / re-scoped and WSL assigns a **new committee `Id`** roughly each decade. Under model A (committee historical backfill redesign — each WSL `Id` is its own Org, same-name bodies coexist), each era becomes a separate PM/usa-wa Organization. There is no upstream link between the Ids.

The result is incoherent Org state. Worked example — the "Senate Labor & Commerce" lineage:

| WSL Id | Name | Created | active | roles | assigns | active assigns |
|---|---|---|---|---|---|---|
| 28244 | Washington State Senate Labor and Commerce Committee | 2026-06-19 (daily) | ✅ true | 1 | 21 | **9** |
| 14294 | Senate Committee on Labor, Commerce & Consumer Protection | 2026-07-03 (harvest) | ⚠️ true | 1 | 9 | 0 |
| 10171 | Senate Committee on Labor, Commerce, Research & Development | 2026-07-03 (harvest) | ⚠️ true | 1 | 13 | 0 |
| … (further era Ids) | | | ⚠️ true | | | 0 |

Systemic footprint (as of 2026-07-24): **186 committee orgs — 184 `active=true`, 2 false, 0 archived, 0 deleted.** 34 came from daily discovery (all genuinely current); **152 from the `harvest_committees` backfill, of which 150 remain `active=true`.**

### Root cause

1. `harvest_committees` (model A) materializes every era's roster; every backfilled Org defaults `active=true` — the harvest has no signal that an old-era `Id` is defunct.
2. `reconcile_committee_active` **live-era scoping (#90)** deliberately excludes any `Id` not in the current-or-prior biennium roster (`scoped_out`), to stop the 152 reading as a mass retirement that trips the abort floor. **Side effect: the historical Ids can never be retired** — permanent `active=true` zombies with no lifecycle dating.

### What is already coherent

Child entities are largely fine: historical orgs carry **0 active assignments** (the span builders closed the membership windows); only the current head (28244) holds the 9 live members. The incoherence is concentrated at the **Org level** — `active` and the absent lifecycle/succession record — not in Assignments.

### What PM has no model for

Cross-`Id` **succession**. The dated-name / rename-chain machinery (#46/#56) operates *within* one stable `Id`; it does not stitch era-`Id` → era-`Id`. "These four are one committee's timeline" is unrepresented anywhere today.

## Goal

Org history maps a coherent timeline, leaving **only the current Org active**; dependent Roles and Assignments are validated coherent. Fix is **general** (all ~150 historical committees), not per-lineage.

## Target model (§ A)

Preserve model A — each WSL `Id` stays its own Org (no collapsing). Coherence comes from three orthogonal axes made explicit:

### Axis 1 — `active` (boolean; PM-authoritative, usa-wa produces)

Operational live/dissolved flag. **Per-Id rule, no lineage grouping required:**

> `active = true` ⟺ the `Id` is present in the **current-biennium live roster**; else `active = false`.

This alone delivers "only the current Org active." The 34 daily-discovered stay active; the ~150 harvest-only flip false. Distinct from `archived_at` (record-archived axis) — a dissolved committee is `active=false`, **not** archived.

### Axis 2 — lifecycle window (PM events, per-Id)

`founded` / `dissolved` events bound each era-`Id`'s operational span, giving a non-overlapping timeline. Derived from the biennium range of that `Id`'s archived rosters (objective). `dissolved` is emitted only for non-current heads.

### Axis 3 — succession chain (PM events, cross-Id, linked-entity)

- `succeeded_by` **(new PM slug)** — event on the **predecessor**, `linked_entity` → **successor**. Expresses the dominant rename-re-key **continuation**.
- `split_from` / `merged_with` (existing PM slugs) — genuine branches.

Each PM event carries **exactly one** linked entity. Multi-way re-orgs are expressed pairwise:
- **1→2 split** (parent may or may not continue): two `split_from` events, each **child** → the single parent.
- **2→1 merge** (one predecessor may be preserved): pairwise `merged_with` events.

### Hybrid boundary — derived vs. attested

| Fact | Source | Rationale |
|---|---|---|
| `active` per Id | **auto** — current-roster membership | objective, per-Id, no grouping |
| `dissolved` year | **auto** — last observed biennium | objective |
| `founded` year | **auto, gated** — first observed biennium, only when safely after the 1999-00 archive floor (else omitted, left to operator) | first-observed is a lower bound; often wrong for pre-floor bodies |
| `succeeded_by` / `split_from` / `merged_with` links | **operator-attested** | judgment — irregular re-orgs, no ground truth |

**Key consequence:** deactivation (Axis 1) needs **no lineage grouping** — pure per-Id roster presence. Only the succession *links* (Axis 3) require operator judgment, so the human surface is small (N lineages, not 150 Ids), and the auto layer stays safe.

## PM API contract (§ B) — the CannObserv/power-map issue

> **Filed: [power-map#321](https://github.com/CannObserv/power-map/issues/321)** — `succeeded_by` slug + `pm_event_id` refine-in-place + per-event response disposition. usa-wa implementation (§ C) is gated on it (except append-only window emission, partially unblocked today — see B2). Positioned as the producer-scale complement to power-map#307 (shipped org-lifespan model) / #313 (manual 9-org backfill).

**Scope corrected after the #321 maintainer review (2026-07-25).** Three assumptions in the original filing were wrong and the ask shrank accordingly:
1. **Events are already producer-writable** — embedded in the org/person observation payload via `write_entity_events` (`POST /organizations/{id}/observations`). There is no missing "write API"; the channel exists.
2. **A dedicated sub-resource would *not* decouple events from the org LWW clock.** `entity_events` has no outbox trigger; changes propagate only via `trg_touch_entity_on_event_change` bumping `organizations.updated_at` (which the org outbox trigger then emits). Both transports hit the same trigger — clock placement is a *trigger* decision, not a transport one.
3. **Strict-identical re-emit is already a true no-op** — content-dedup on `(event_type, full partial-date, linked_entity)` (NULLs-equal) means an identical event never INSERTs, never touches, never emits an outbox row. The append-only no-op guarantee already holds.

**Relationship to existing power-map work.** PM already models org lifespan via `founded`/`dissolved`/`merged_with` **entity events**, with a `v_org_lifespan` view, an assignment-must-fall-within-lifespan invariant, and an audit/close script (power-map#307, shipped). power-map#313 is the *manual* backfill of that model for 9 defunct orgs — blocked on human-supplied end dates — and flags renamed-continuity committees (Senate LCTA; the COG→RSG re-key of #305) as *"renamed continuity → re-home per #266, not dissolve."* This spec is the **producer-scale** complement: usa-wa supplies these events for all ~150 re-keyed WA committees from the roster archive instead of by hand.

### B1 — Catalog addition (one new org event type)

```
slug: succeeded_by      display: "Succeeded By"
applies_to: organization   requires_linked_entity: true   requires_year: false
direction convention: event lives on the PREDECESSOR; linked_entity → successor
```

`founded` / `dissolved` (window) and `split_from` / `merged_with` (branches) already exist and are unchanged. `succeeded_by` is the genuine gap — the dominant rename-re-key *continuation* has no linked-entity slug (#313 works around it via "re-home per #266").

### B2 — Refine-in-place idempotency — anchor decision: **B (`pm_event_id` native update)**

The write channel exists; append-only identical re-emit already no-ops (see correction 3). The real gap content-dedup **can't** cover is letting a producer **refine** an event in place (year-only → full date, corrected date) without minting a duplicate — the same gap power-map#311 solved for assignments.

**Chosen anchor = B: `pm_event_id` native update-in-place** (the #311 pattern), gated on `source_key_id` provenance. Rejected **A (`(source, source_id)` producer natural key)** — A's only real advantage is a *stateless blind re-emit* contract, which usa-wa structurally does not use: our span/assignment producers already carry local anchored rows and address PM by `pm_*_id` (`descriptors/assignment.py` dual-mode). The event producer carries local rows the same way, so A's cost (a new 4th PM idempotency primitive) buys a statelessness we won't exploit. B reuses the exact assignment machinery + no-op gate; the `pm_event_id` anchor is free since we already mirror `/events` and store `pm_entity_event_id`, and the enriched write response returns the id at create time.

**Hard rider:** the anchored update path must **diff-before-write and skip the UPDATE when unchanged** — an unchanged UPDATE bumps `updated_at` and re-arms the ping-pong repeatedly diagnosed in #65 / #102 / #109. Append-only gets this free; the id-addressed path must earn it (the `docs/LWW-NOOP-GATE.md` discipline).

**Partial unblock:** because append-only embedded writes already no-op cleanly, the `founded`/`dissolved` **window emission is achievable today**; only refine-in-place (date sharpening) and `succeeded_by` truly gate on #321.

### B3 — Per-event response disposition (must-have)

The embedded writer returns nothing and silently skips dups. #321 must return **per-event disposition** (`new｜auto-attached｜updated｜rejected`) — our LWW no-op gate and non-convergence telemetry (#112) cannot function without it. Required regardless of transport.

### B4 — Transport (smaller choice): thin `POST /{id}/events/observations`, justified by failure isolation

A dedicated sub-resource is **not** justified by LWW decoupling (correction 2) but is still worth it for **failure isolation** — the lineage backfill emits many linked-entity events across orgs, and one bad `succeeded_by` (e.g. a not-yet-anchored successor) should not roll back an org's whole single-transaction observation. Also conceptual clarity (a `succeeded_by` is a relationship, not an org attribute). If PM prefers to enrich the embedded surface instead, acceptable — we lose only the isolation.

### B5 — Trigger/outbox: **no change** (do not decouple)

Keep events on the `trg_touch_entity_on_event_change` → org-outbox path. That org-touch is **load-bearing for our `sync_entity_events` reconcile** (we re-fetch `/events` on org change); decoupling breaks a working path for a speculative benefit, and a genuine timeline change *should* propagate an org change. Event-granular feed propagation, if ever wanted, is additive (event outbox rows *alongside* the org touch), not now.

### B6 — No change for `active`

The `reconcile_committee_active` producer path already emits `active`; deactivation rides existing machinery. No PM change required for Axis 1.

## usa-wa design (§ C) — deferred until § B ships

### C1 — Auto layer (per-Id, objective; no grouping)

- **Deactivation:** adjust `reconcile_committee_active` so an `Id` absent from the current-biennium live roster resolves to `active=false` (today #90 scopes such Ids out so they never flip). The ~150 flip is a *legitimate* one-time mass-deactivation, run deliberately (`--max-close-fraction 1.0`, the committee-re-key precedent); in steady state the mass-close floor still guards partial pulls.
- **Windows:** a new module derives each `Id`'s `founded` (first observed biennium start) / `dissolved` (last observed biennium end; non-heads only) from `CommitteeRosterCohortProvider`. `founded` gated per OQ1.

### C2 — Operator attestation surface (judgment layer; new, modeled on #107 `operator_events`)

- Append-only, `usa_wa_operator`-sourced store for `succeeded_by` / `split_from` / `merged_with` attestations, natural-keyed on `(predecessor Id, successor Id, slug, date)`; supersede-for-corrections (provenance never mutated, #54).
- A CLI interjection surface (direct-arg / `--file` batch / `--supersede <id>` / `--list` / `--dry-run`), validating that both ends resolve to committee Orgs before writing; app-role DML (shell access = trust boundary, like the #107 CLI).

### C3 — Event producer (blocked on § B)

A new descriptor path emitting the C1 windows + the C2 attested links through the event observation surface, carrying local anchored event rows and addressing PM by `pm_event_id` for refine-in-place (the `descriptors/assignment.py` dual-mode, per the B2 decision) + the diff-before-write LWW no-op gate (`docs/LWW-NOOP-GATE.md`). Read-mirror already exists (`descriptors/events.py` / `sync_entity_events`). Append-only window emission is achievable ahead of #321; refine-in-place + `succeeded_by` gate on it.

### C4 — Child-entity coherence validation (Roles / Assignments)

Assignments are already coherent (0 active on historical orgs). Add a read-only invariant check (à la `succession_invariants.py`):
- no `active=false` / dissolved committee Org carries an `is_active` assignment;
- the current head is the sole active Org in its lineage (where a lineage is defined by attested succession links).

Daily systemd timer + `OnFailure=` operator email. Roles are unchanged — a Role is a membership container; tenure dates live on its Assignments, so no per-Role lifecycle field is added.

### C5 — Curation-assist report (OQ2)

A read-only **lineage-candidate report** surfacing likely succession pairs by name-similarity + membership-carryover across the biennium boundary + adjacent (non-overlapping) windows + chamber. **Advisory only** — it *suggests* which `Id`s an operator might link; it never asserts a link. This is the "fully automatic" inference repurposed as a suggestion tool, keeping ground-truth assertion with the operator.

## Rollout order (all deferred)

0. *(unblocked now)* Optionally emit append-only `founded`/`dissolved` windows via the existing embedded write (identical re-emit already no-ops) — but deferred with the rest per scope C.
1. power-map ships B1 (`succeeded_by`) + B2 (`pm_event_id` refine-in-place) + B3 (per-event disposition) (+ B4 sub-resource).
2. Regenerate the PM client; wire the event producer + descriptor (C3).
3. Backfill `founded`/`dissolved` windows (C1) + the one-time ~150 deactivation run (C1 / OQ4).
4. Stand up the operator attestation surface (C2) + the curation-assist report (C5); operator attests the succession links.
5. Add the C4 validation invariant + timer.

## Resolved decisions

- **Scope:** model + PM API contract only; defer both usa-wa phases until PM is ready (user choice "C").
- **Lineage establishment:** hybrid — auto-derive objective facts (windows, current-vs-historical), operator-attest the succession links.
- **Event vocabulary:** add `succeeded_by` linked slug for continuation; reuse `split_from`/`merged_with` for branches; `founded`/`dissolved` for windows.
- **Idempotency anchor (#321 review):** **B — `pm_event_id` native update-in-place** (the #311 assignment pattern), not A (`(source, source_id)`) — usa-wa producers carry local anchors already, so A's stateless-blind-re-emit benefit is unused. Diff-before-write on the anchored path is mandatory.
- **Trigger/outbox:** no decouple — events stay on the org-touch → outbox path (load-bearing for our `sync_entity_events` reconcile).
- **OQ1 (`founded` vs archive floor):** emit `founded` only when first-observed is safely after the 1999-00 floor; otherwise omit and leave to operator attestation. `dissolved` is reliable.
- **OQ2:** provide the advisory curation-assist report (C5).
- **OQ3 (splits/merges):** PM allows exactly one linked entity per event; a split is 1→2 (parent potentially preserved), a merge is 2→1 (one predecessor potentially preserved), each expressed as pairwise single-link events. The per-Id `active` rule handles both naturally (both current children active; dissolved parent inactive).
- **OQ4:** the one-time ~150 deactivation is a deliberate operator run overriding the mass-close floor.

## Open questions (carry into the implementation plan when unblocked)

- Whether `succeeded_by` events should also carry an optional `date` (effective biennium boundary) for a richer timeline, given `requires_year=false`.
- Confirm the enriched writer's `updated` disposition applies `date` deltas to the anchored event (the #108 assignment lesson — `auto-attached` dropped `end_date`/`is_current` deltas — must not recur for events; the B2 `pm_event_id` path is designed to avoid it, but verify against the shipped #321 behaviour).
- Lineage identity for C4's "sole active in its lineage" assertion when a split yields two legitimately-active heads — the invariant must key on attested links, not on name.
