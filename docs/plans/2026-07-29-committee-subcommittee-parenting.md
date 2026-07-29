---
title: Committee subcommittee parenting + parent-propagation
date: 2026-07-29
status: draft
---

# Committee subcommittee parenting + parent propagation

## Problem

WA legislative **subcommittees** (House Appropriations subs `12174/12175/13992`; Senate
Behavioral Health sub `29190`) are stored parented to the **chamber** (House/Senate),
not to their real parent committee (Appropriations / Health & Long Term Care). So the
Org hierarchy in USA-WA and PM shows a subcommittee floating directly under the chamber
with no containment — the `#124` lineage integrity gap the Labor & Commerce review
surfaced. Two distinct defects:

1. **Propagation (sync):** even if we set the correct parent locally, it won't reach PM
   and won't stick. The org reconcile mirrors PM's `parent_id` back into local every
   cycle, and `parent` is deliberately **excluded** from the org enrich-carry set — so a
   local-only write is clobbered within ~12h and PM never learns the new parent.
2. **Origin (adapter):** the WSL committees normalizer parents *every* committee to its
   chamber via `Agency`. It has no notion of a subcommittee, so the wrong parent is
   minted at the source for every subcommittee, past and future.

## Approach

Two parts, shippable together.

**Part A — open a parent-propagation path (sync).** Add `"organization_parent_id"` to
`OrganizationDescriptor.enrich_carry_fields`. The enrich payload already derives from
`to_observation` (which emits `organization_parent_id` when the parent is anchored), and
the sidecar's `_enrich_payload_drifted` fingerprint is computed from the enrich payload —
so once parent rides along, a parent change re-enqueues an ENRICH automatically and the
sidecar self-heals parent drift for **all** orgs, going forward. This reverses the prior
"parent is PM-curated" stance for orgs; low risk because local and PM parents already
agree for committees (no mass churn — only genuinely-changed rows re-fire once).

**Part B — correct the parent at origin (adapter).** In `normalize_committees`, detect a
subcommittee by the `"Subcommittee"` token in its name, infer the parent committee name
from the sub name (both `"{Parent} Subcommittee on {X}"` and `"{X} Subcommittee to
{Parent}"` shapes), and resolve it to a **concurrent committee in the same roster batch**
(same `Agency`, matched on normalized name) — setting `parent_organization_id` to that
committee's row instead of the chamber. Fall back to the chamber (today's behavior) when
the parent can't be resolved, logging the miss. Keep `org_type='committee'` — the parent
being a committee (not a chamber) *is* the structural tag; a subcommittee's containment is
expressed by the hierarchy, not a new type that would ripple into the PM identifier map.

**Backfill the 4 existing subs.** They're `fill_only`-protected (the daily refresh won't
rewrite them), so a one-off sets their local parent (`875` / `28241`) and, with Part A
live, the next reconcile/enrich propagates it to PM → PM mirrors it back → durable.

## Tradeoffs / alternatives

- **New `org_type='subcommittee'` (rejected):** cleaner tag, but `org_type` drives the PM
  `identifier_type` map and the read-mirror; a new value risks unresolved identifiers and
  sync rejections. The parent-committee hierarchy already conveys "is a subcommittee."
- **Dedicated `is_subcommittee` / `sort_order` columns + migration (deferred):** only
  needed if we want an explicit flag or an authored sibling order. WSL provides **no**
  ordering signal, so any order is synthesized (name/Id) — see open questions. Deferred
  unless the review wants it.
- **Part A via a one-off force-push CLI instead of enrich-carry (rejected):** pushes only
  the 4 and leaves parent perpetually un-synced; the enrich-carry fix is smaller and
  self-healing.
- **Cross-biennium subcommittee detection (rejected for now):** the bare "Education
  Appropriations" form (2009-10/2011-12) lacks the token, but every sub carries it at its
  creation biennium, and `fill_only` preserves the tag once set — so per-batch detection
  suffices in practice.

## Steps

1. **Part A (TDD):** failing test — `OrganizationDescriptor.to_enrich_observation`
   carries `organization_parent_id` when the row's parent is anchored. Green by adding the
   field to `enrich_carry_fields`. Add/confirm a test that a parent change drifts the
   enrich fingerprint (re-enqueue).
2. **Part B (TDD):** failing tests in `test_committees.py` for both name shapes
   (`Appropriations Subcommittee on Education` → parent `Appropriations`; `Behavioral
   Health Subcommittee to Health & Long Term Care` → parent `Health & Long Term Care`),
   same-Agency + same-batch resolution, and the unresolved-parent chamber fallback +
   warning. Green by adding subcommittee detection/inference to `normalize_committees`
   (a pure helper `resolve_subcommittee_parent(committee, batch, anchors)`).
3. **Full suite + ruff/format** green in the worktree.
4. **Backfill script (dry-run first):** set `parent_organization_id` for `12174/12175/
   13992 → 875`, `29190 → 28241` (app-role DML from prod checkout after merge).
5. **Propagate + verify:** run the sidecar (or a targeted enrich) with
   `POWERMAP_MIN_REQUEST_INTERVAL=2.0`; confirm PM `parent_id` now points at the parent
   committee for all 4; confirm C4 (`committee_lineage_invariants`) still green.
6. **Docs:** AGENTS.md note on the enrich-carry parent behavior + the normalizer's
   subcommittee parenting.

## Open questions / risks

1. **"Ordered in relation to parent" — semantics?** Options: (a) *just* establish the
   parent hierarchy (no explicit order) — my default; (b) add a `sort_order`/ordinal among
   siblings (needs a model column + migration, and WSL gives no order, so it'd be
   name-alphabetical or Id — synthetic). Which did you intend?
2. **Explicit subcommittee tag?** Default is hierarchy-only (no new column/type). Want an
   explicit `is_subcommittee` flag or `org_type='subcommittee'` despite the sync ripple?
3. **Parent-authority stance (Part A):** enrich-carry makes USA-WA assert `parent` to PM
   for all orgs. Confirm we want producer authority over org parent (vs. leaving it PM-
   curated). Committees agree today, so churn risk is low, but it is a deliberate stance
   change.
4. **Name-inference fragility:** detection needs the `"Subcommittee"` token; the bare
   "Education Appropriations" form is undetectable per-record. Accept (creation-biennium
   catches it), or add a curated override list?
