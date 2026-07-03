# Committee Historical Backfill — Redesign (identity = WSL Id)

**Date:** 2026-07-02
**Status:** Approved (design)
**Supersedes:** [2026-07-02-committee-historical-backfill-design.md](2026-07-02-committee-historical-backfill-design.md)
**Scope:** Sub-project 3, redesigned after the first attempt exposed WSL Id
instability and crash-looped the sidecar.

## Context — what the first attempt taught us

The original design assumed a committee keeps a **stable WSL `Id`** across
bienniums. The operational backfill disproved it: WSL **re-keys** committees over
time — the same chamber+name gets a *new* `Id` every ~decade (sometimes with
overlap, sometimes across a dormancy gap). 13 of the harvested committees carry
2–3 Ids each (e.g. House Appropriations `875` (1991–2011) → `17366` (2013–2019);
Senate Higher Education `185` → `12231` → `17558`).

The consequence chain that crash-looped the sidecar:
1. The harvest materialized each Id as a distinct local org (correct locally —
   `(source, source_id)` keys them).
2. `OrganizationDescriptor.pm_match` stage 2 (name FTS) matched a historical
   committee onto an **existing same-name PM org** already claimed by another Id.
3. Two local committees thus anchored to one PM org and both mirrored its
   `OrgName`; the mirror's **global** `uq_organization_names_natural_key
   (source, source_id)` collided, raising a `UniqueViolationError` that crashed
   the whole sidecar cycle (all sync wedged).

**Recovery already done:** the 152 historical committee rows, their 160 pending
outbox CREATEs, and 496 stale roster citations were rolled back; the 18 archived
rosters were kept; the sidecar was restarted and the cohort is healthy again
(`validate_committees`: 0 divergent). ~100 PM orgs the failed run created remain
(see Orphan adoption).

## Decision — identity is the WSL Id (model A)

Each distinct `org_wa_legislature_committee_id` is **its own committee org**.
Identical names **coexist** in PM (verified: PM already allows this — the failed
run produced two distinct same-name "House Committee on Local Government" orgs).
A **re-key** (same name, new Id) is a *different* committee; a **rename** (same
Id, new name) is a rename. This makes the existing rename-chain-by-`Id` logic
*correct* rather than something to redesign.

Rejected alternative (model B — one org carrying dated WSL-Id identifiers, like
dated names/addresses) is "most technically correct" but requires a new PM
feature **and** a name-based merge decision to attach a new Id to an existing org
(the fuzzy case). Deferred as a possible future PM enhancement; **out of scope**
here.

## Components

### 1. Core fix — guarded name-match in `pm_match`

Keep stage 1 (identifier match on `org_wa_legislature_committee_id` = the WSL Id)
— this is the happy path and is exactly what cleanly **adopts** the orphan orgs.
Guard stage 2 (name FTS): **skip any candidate PM org that already carries a
`org_wa_legislature_committee_id` identifier for a *different* `source_id`**. Such
an org is claimed by another committee, so we create a new org instead of gluing
onto it. This preserves the legitimate "adopt PM's pre-curated, *unclaimed* org"
path (a same-name org with no committee identifier yet) while eliminating the
cross-Id over-match. Scoped to the committee identifier type; other org classes
(chamber, legislature, Joint/Other) are untouched.

### 2. Mirror robustness (defense-in-depth)

The guarded match makes the collision not happen; this makes it non-fatal if it
ever does (e.g. a future PM merge surfacing one `OrgName` id under two orgs).
Harden `sync_org_names` **and** `sync_org_acronyms`: before inserting a child row,
if that `pm_org_name_id` / `pm_org_acronym_id` already exists under a *different*
org, **log a warning and skip it** rather than letting the `flush` raise and crash
the sidecar cycle. No schema change (the `(organization_id, source, source_id)`
migration alternative is rejected — under model A distinct orgs have distinct
child rows, so that permissiveness isn't needed and it would weaken the
invariant).

### 3. Re-materialization + orphan adoption

The Phase A/B code is **preserved** (the bug was matching, not harvest/chain):

- **Re-materialize** by re-running `harvest_committees`. The 18 rosters are
  already archived, so this is cache-hit-fast (no WSL re-pull) and just re-inserts
  the 152 historical committee local rows (fill-only).
- **Adopt by identifier.** With the guarded fix, `sweep_unanchored`
  identifier-matches each historical committee to its existing orphan PM org (the
  ~100 that carry WSL-Id identifiers) and **creates** new orgs for the Ids whose
  CREATE was lost in the crash. No duplicates, no cross-Id collision — the orphans
  stop being orphans. **No PM-side deletion needed** (investigation confirmed the
  orphans are clean distinct-per-Id orgs, not wrongly merged).
- **Rename chain** — `reconcile_committee_name_chain` runs unchanged, now correct:
  per-`Id` timelines emit true renames; re-keys are distinct committees and
  produce no spurious `former`.

**Model-A consequence (accepted):** a *renamed* committee (stable Id) emits its
`former`/`legal` windows, but a *re-keyed* committee (185→17558) shows as two
separate committees with no rename link. Unifying re-keys into one committee is
the deferred model-B enhancement.

## Testing (TDD)

- `pm_match`: a same-name PM org already claimed by another committee's WSL-Id
  identifier → assert we return `None` (create-new), not the claimed org; the
  unclaimed same-name org still adopts; identifier match still wins stage 1.
- Mirror: a `pm_org_name_id` (and `pm_org_acronym_id`) already present under a
  different org → assert skip-and-log, no exception, the mirror completes.
- Integration: two same-name / different-Id committees anchor to **two distinct**
  PM orgs (no collision).
- Existing harvest / roster-provider / chain-builder / emit tests stay green
  unchanged (they validate the preserved code under model A).

## Operational sequencing (guarded, incremental)

1. Land + deploy the `pm_match` + mirror fixes; restart the sidecar.
2. Re-run `harvest_committees --force` to re-insert the 152 local rows (a plain
   cache-hit re-run re-materializes nothing — the runner returns 0 without
   re-normalizing; `--force` re-fetches + re-normalizes past the freshness cache).
3. **Monitor anchoring:** confirm the `UniqueViolation` crash signature is *gone*,
   the ~100 orphans adopt by identifier, and the rest create distinct orgs. Do not
   proceed until anchoring is proven clean.
4. **Heal the LWW-lock:** the *created* committees carry a local `updated_at` at
   creation ≥ PM's org clock, so the read mirror won't adopt their name/acronym
   windows (they show as `validate_committees` divergent with empty child tables).
   Run `heal_committee_curation` (force-adopt past LWW, #65 Part 2) to mirror them.
5. `validate_committees` clean → `reconcile_committee_name_chain --dry-run`
   → review → emit.

## Non-goals

- **Model B** (one org, dated WSL-Id identifiers) — deferred PM feature.
- **PM-side org deletion / cleanup** — orphans are adopted, not deleted.
- **Joint/Other deep history** — the meeting docket floors at 1999; #56 covers the
  recent era.

## Risks

- **Guarded match subtlety:** the "claimed by a different committee identifier"
  check must read the candidate PM org's identifiers from the search record (or a
  follow-up fetch). If PM's search payload omits identifiers, the check needs the
  detail fetch — verify the payload shape in the plan.
- **Re-materialization idempotency:** re-running the harvest must remain fill-only
  and cache-bound; the local rows re-insert but anchoring is the sidecar's job.
- **Incident guardrail:** step 3 monitoring is mandatory — the chain emit stays
  gated until anchoring is proven clean, so a regression can't re-wedge the
  sidecar unnoticed.
