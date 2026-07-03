---
title: Committee backfill redesign (identity = WSL Id) — implementation
date: 2026-07-02
status: draft
supersedes: docs/plans/2026-07-02-committee-historical-backfill-subproject-3.md
---

# Committee backfill redesign (identity = WSL Id)

Spec: [docs/specs/2026-07-02-committee-historical-backfill-redesign.md](../specs/2026-07-02-committee-historical-backfill-redesign.md)

## Problem

WSL re-keys committees across eras (same chamber+name, new `Id`), which broke the
original stable-`Id` backfill: historical committees name-matched onto existing
same-name PM orgs, two local committees anchored to one PM org, and the org-name
mirror's global unique key crashed the sidecar cycle. We adopt identity = WSL Id
(distinct org per Id, same-names coexist), which needs two targeted code fixes
before the harvest can be safely re-run; the harvest/chain code is preserved.

## Approach

Two small, TDD'd changes. **(1)** In `OrganizationDescriptor.pm_match`, guard the
stage-2 name FTS match: PM's search payload omits identifiers, so for each name
candidate fetch its `OrgDetail` (`get_entity`) and **skip any candidate that
already carries a `org_wa_legislature_committee_id` identifier** — since stage 1
(identifier) already ran and missed, such a candidate is claimed by a *different*
committee, so we must create a new org; only an identifier-less same-name org is
adopted. **(2)** Harden `sync_org_names` and `sync_org_acronyms` to skip-and-log a
`pm_org_name_id` / `pm_org_acronym_id` already present under a *different* org
instead of letting the `flush` crash the cycle. Then re-run the (unchanged)
`harvest_committees` to re-materialize the 152 local rows; the sidecar
identifier-matches the ~100 orphan PM orgs (adopt) and creates the rest, monitored
for the absence of the crash signature; finally re-emit the rename chain.

## Tradeoffs / alternatives

- **Guard using the search payload alone** — rejected: PM org search returns only
  `id/name/acronym/slug/parent_id/archived_at` (verified), no identifiers. The
  guard needs a per-candidate `get_entity` detail fetch. Bounded: name-match is the
  fallback path and candidates are typically 0–1.
- **Skip only a candidate claimed by a *different* source_id** (vs. any committee
  id) — simplified to "any committee identifier present → skip": stage 1 already
  matched-and-returned if the candidate held *our* id, so in stage 2 any committee
  id present is necessarily another committee's. Same result, one fewer value
  comparison.
- **Per-org unique key migration for the mirror** — rejected (spec): under model A
  distinct orgs have distinct child rows; skip-and-log is enough and keeps the
  global-uniqueness invariant.
- **Delete/clean the 100 orphan PM orgs** — rejected: investigation showed they're
  clean distinct-per-Id orgs carrying WSL-Id identifiers; re-materialization adopts
  them by identifier, no PM-side deletion.

## Steps

1. **`pm_match` guard — tests first.** Extend `test_organization_descriptor.py`:
   (a) stage-1 identifier match still wins (unchanged); (b) a same-name candidate
   whose `get_entity` detail carries a `org_wa_legislature_committee_id` → `pm_match`
   returns `None` (create-new), not that org; (c) a same-name candidate with **no**
   committee identifier is still adopted; (d) the fake client records the extra
   detail fetch only on the name path. Use a fake client returning a name page +
   crafted `get_entity` details.
2. **`pm_match` guard — implementation.** In stage 2, after the `normalize_name`
   filter, fetch each surviving candidate's detail and drop those carrying a
   committee identifier; keep the single-adopt / hierarchy logic on what remains.
   Green step 1. (Reuse `record_has_identifier` / the identifier-reading helper.)
3. **Mirror hardening — tests first.** In `test_org_name_sync.py` /
   `test_org_acronym_sync.py`: seed an `OrganizationName` with `pm_org_name_id` X
   under org A; run `sync_org_names` for org B whose PM names include X → assert X
   is skipped-and-logged, org B's other names still mirror, no exception. Mirror the
   case for `sync_org_acronyms` / `pm_org_acronym_id`.
4. **Mirror hardening — implementation.** Before inserting a new child row, check
   whether its `(source, source_id)` (= `pm_org_name_id`) already exists under a
   different `organization_id`; if so, log a warning and skip. Green step 3.
5. **Suite + lint + docs.** `uv run pytest` + `ruff` green. Update the
   `descriptors/organization.py` `pm_match` docstring (the name-path detail fetch +
   claimed-candidate guard) and the `org_names`/`org_acronyms` module docstrings
   (skip-and-log). Note the redesign in AGENTS.md where the Id-keyed CLIs are
   described.
6. **Operational — deploy + re-materialize (guarded).** (a) Restart the sidecar on
   the fixes. (b) Re-run `harvest_committees --from-biennium 1991-92 --to-biennium
   2025-26 --force` to re-insert the 152 local rows. **Revision (mechanics):** a plain
   re-run does *not* re-materialize — the rosters are archived within the 1-day TTL, so
   the runner's cache-or-fetch short-circuits (`upserted=0`, no re-normalize/upsert).
   The rolled-back org rows only come back if the runner actually re-normalizes, so
   `harvest_committees` gained a `--force` flag (TDD'd) that re-fetches + re-normalizes
   past the cache; the byte-identical wire still dedups to the existing RawPayload
   (revalidation, not re-store), and fill-only leaves unaffected committees untouched.
   Same 18-paced-`GetCommittees` WSL profile the original harvest used.
   (c) **Monitor anchoring**: confirm the `UniqueViolationError` crash signature is
   absent, the ~100 orphans adopt by identifier, and the remaining Ids create
   distinct orgs (spot-check two same-name/different-Id committees → two PM orgs).
   Do not proceed until anchoring is clean and stable.
7. **Operational — heal LWW-lock, then validate + emit.** A force re-materialization
   *creates* committees whose local `updated_at` (creation) is ≥ PM's org clock, so
   `apply_record` skips the PM-wins branch and their name/acronym windows never
   mirror — `validate_committees` shows them as divergent with empty child tables
   (bit us: 43 committees, cleared only by the heal). Run
   `heal_committee_curation` (force-adopt PM's OrgDetail past LWW, #65 Part 2) so the
   created cohort mirrors its names/acronyms. Then `validate_committees` → clean →
   `reconcile_committee_name_chain --dry-run` → review → emit. Confirm no divergence
   introduced and the sidecar stays healthy.

## Open questions / risks

- **Detail-fetch cost on the name path.** Adds one `get_entity` per name candidate
  during `sweep_unanchored`. Bounded (name-match is the fallback, ~0–1 candidates),
  but worth a log so a pathological fan-out is visible. Confirm acceptable at the
  152-committee re-materialization scale (one-time).
- **`record_has_identifier` shape.** The guard reuses the identifier-reading helper
  the descriptor already has for `needs_enrich`; verify it reads the detail
  payload's `identifiers` list the same way (step 2).
- **Monitoring is mandatory (incident guardrail).** The chain emit stays gated
  behind proven-clean anchoring; a regression must not silently re-wedge the
  sidecar. If the crash signature reappears, stop and roll back the re-materialized
  local rows (same procedure as the incident recovery).
- **Idempotent re-run.** `harvest_committees` must stay fill-only + cache-bound so
  re-running is safe; anchoring remains the sidecar's job, not the harvest's.
