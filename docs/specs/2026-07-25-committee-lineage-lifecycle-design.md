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

Events are read-only end-to-end today (`list_org_events` / `list_person_events` / `list_entity_event_types`; `descriptors/events.py`: *"usa-wa does not yet produce entity events… no observation-embed path"*). Nothing in usa-wa proceeds until the following ship.

### B1 — Catalog addition (one new org event type)

```
slug: succeeded_by      display: "Succeeded By"
applies_to: organization   requires_linked_entity: true   requires_year: false
direction convention: event lives on the PREDECESSOR; linked_entity → successor
```

`founded` / `dissolved` (window) and `split_from` / `merged_with` (branches) already exist and are unchanged.

### B2 — Event write channel (the core missing piece)

A producer path mirroring the existing observation pattern. Recommended: a **dedicated sub-resource observation** (keeps events off the parent org's own LWW clock — rejected embedding events in the org observation payload for that reason):

```
POST /api/v1/organizations/{org_id}/events/observations
{
  source, source_id,                       # producer natural key (idempotency anchor)
  event_type_slug,                         # founded | dissolved | succeeded_by | split_from | merged_with
  date: { year, month?, day? },            # PartialDate; year required for founded/dissolved
  linked_entity_type, linked_entity_id,    # PM org ULID, when the slug requires it
  notes?, visibility="public"
}
→ ObservationResult { disposition: new | auto-attached, event_id }
```

### B3 — Idempotency + LWW-safety (hard requirement)

PM must dedup on `(source, source_id)` so a re-produced identical event is a **true no-op that does not advance PM's clock**. The usa-wa producer re-emits every refresh cycle; without this we re-arm the outbox ping-pong repeatedly diagnosed in #65 / #102 / #109. This is a blocking acceptance criterion for the PM feature, not a nicety.

### B4 — Read-back

`GET /{id}/events` read-mirror already exists (`sync_entity_events`). Ask that produced events also surface in the **changes feed** so the reconcile path sees them without a full re-fetch. Nice-to-have — we already poll `/events` on the reconcile cadence — but preferred.

### B5 — No change for `active`

The `reconcile_committee_active` producer path already emits `active`; deactivation rides existing machinery. No PM change required for Axis 1.

## usa-wa design (§ C) — deferred until § B ships

### C1 — Auto layer (per-Id, objective; no grouping)

- **Deactivation:** adjust `reconcile_committee_active` so an `Id` absent from the current-biennium live roster resolves to `active=false` (today #90 scopes such Ids out so they never flip). The ~150 flip is a *legitimate* one-time mass-deactivation, run deliberately (`--max-close-fraction 1.0`, the committee-re-key precedent); in steady state the mass-close floor still guards partial pulls.
- **Windows:** a new module derives each `Id`'s `founded` (first observed biennium start) / `dissolved` (last observed biennium end; non-heads only) from `CommitteeRosterCohortProvider`. `founded` gated per OQ1.

### C2 — Operator attestation surface (judgment layer; new, modeled on #107 `operator_events`)

- Append-only, `usa_wa_operator`-sourced store for `succeeded_by` / `split_from` / `merged_with` attestations, natural-keyed on `(predecessor Id, successor Id, slug, date)`; supersede-for-corrections (provenance never mutated, #54).
- A CLI interjection surface (direct-arg / `--file` batch / `--supersede <id>` / `--list` / `--dry-run`), validating that both ends resolve to committee Orgs before writing; app-role DML (shell access = trust boundary, like the #107 CLI).

### C3 — Event producer (blocked on § B)

A new descriptor path emitting the C1 windows + the C2 attested links through the B2 observation channel, with `(source, source_id)` idempotency + the LWW no-op gate (`docs/LWW-NOOP-GATE.md`). Read-mirror already exists (`descriptors/events.py` / `sync_entity_events`).

### C4 — Child-entity coherence validation (Roles / Assignments)

Assignments are already coherent (0 active on historical orgs). Add a read-only invariant check (à la `succession_invariants.py`):
- no `active=false` / dissolved committee Org carries an `is_active` assignment;
- the current head is the sole active Org in its lineage (where a lineage is defined by attested succession links).

Daily systemd timer + `OnFailure=` operator email. Roles are unchanged — a Role is a membership container; tenure dates live on its Assignments, so no per-Role lifecycle field is added.

### C5 — Curation-assist report (OQ2)

A read-only **lineage-candidate report** surfacing likely succession pairs by name-similarity + membership-carryover across the biennium boundary + adjacent (non-overlapping) windows + chamber. **Advisory only** — it *suggests* which `Id`s an operator might link; it never asserts a link. This is the "fully automatic" inference repurposed as a suggestion tool, keeping ground-truth assertion with the operator.

## Rollout order (all deferred)

1. power-map ships B1 + B2 + B3 (+ B4).
2. Regenerate the PM client; wire the event producer + descriptor (C3).
3. Backfill `founded`/`dissolved` windows (C1) + the one-time ~150 deactivation run (C1 / OQ4).
4. Stand up the operator attestation surface (C2) + the curation-assist report (C5); operator attests the succession links.
5. Add the C4 validation invariant + timer.

## Resolved decisions

- **Scope:** model + PM API contract only; defer both usa-wa phases until PM is ready (user choice "C").
- **Lineage establishment:** hybrid — auto-derive objective facts (windows, current-vs-historical), operator-attest the succession links.
- **Event vocabulary:** add `succeeded_by` linked slug for continuation; reuse `split_from`/`merged_with` for branches; `founded`/`dissolved` for windows.
- **OQ1 (`founded` vs archive floor):** emit `founded` only when first-observed is safely after the 1999-00 floor; otherwise omit and leave to operator attestation. `dissolved` is reliable.
- **OQ2:** provide the advisory curation-assist report (C5).
- **OQ3 (splits/merges):** PM allows exactly one linked entity per event; a split is 1→2 (parent potentially preserved), a merge is 2→1 (one predecessor potentially preserved), each expressed as pairwise single-link events. The per-Id `active` rule handles both naturally (both current children active; dissolved parent inactive).
- **OQ4:** the one-time ~150 deactivation is a deliberate operator run overriding the mass-close floor.

## Open questions (carry into the implementation plan when unblocked)

- Exact PM disposition semantics for the event observation (does `auto-attached` apply `date`/`notes` deltas, or only anchor? — the #108 lesson for assignments must be checked for events before we rely on updates).
- Whether `succeeded_by` events should also carry an optional `date` (effective biennium boundary) for a richer timeline, given `requires_year=false`.
- Lineage identity for C4's "sole active in its lineage" assertion when a split yields two legitimately-active heads — the invariant must key on attested links, not on name.
