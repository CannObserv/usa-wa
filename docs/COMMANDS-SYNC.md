# Commands — Power Map sync

Split out of [COMMANDS.md](COMMANDS.md), which is where the index lives.

## Reconcilers & validation (PM sync)

Emit-only producer CLIs (PM stays the authority; they mirror curation back) plus
read-only validation. Weekly timers in prod; the forms below are the manual /
dry-run surface. No operator token — shell access is the trust boundary.

```bash
# Contact-label backfill (#31) — re-observation of produced orgs holding a phone,
# so PM adopts the synthesized contact display_label. Idempotent + re-runnable;
# --dry-run counts the cohort without submitting. Since #34 the sidecar self-heals
# carry-field drift on its own (anchored-cohort reconcile re-enqueues an ENRICH on a
# local-fingerprint mismatch), so this is now a force-push convenience, not the only
# recovery path.
python -m usa_wa_sync_powermap.backfill_contact_labels --dry-run
python -m usa_wa_sync_powermap.backfill_contact_labels

# Committee active-flag reconciliation (#44) — reconciles PM `active` for WSL committees
# against the current biennium's `GetCommittees(biennium)` roster: `active=false` for the
# absent, `active=true` for the returning (reactivation self-heals a transient partial-pull
# false retirement next cycle). Explicit-membership diff (not current-only
# GetActiveCommittees), guarded by an empty-pull abort + a cohort floor (--max-absent-fraction,
# default 0.34) so a partial WSL pull can't mass-retire. Skips archived/deleted/unanchored;
# emit-only (PM mirrors `active` back). Idempotent.
# Live-era scoping (#90): the diff is restricted to committees whose WSL Id appears in the
# current OR immediately-prior biennium roster (present_ids ∪ prior_ids; the prior roster's
# raw Ids read archive-first via CommitteeRosterCohortProvider). The historical committee
# backfill (harvest_committees, model A) added ~152 defunct-era committee orgs, all defaulting
# active=true; absent from the current roster they'd read as a mass retirement and trip the
# floor every run. Scoping drops them before the diff (counted `scoped_out`) while a genuine
# prior-biennium retirement (in prior, gone from current) still fires. Retirement window is
# one biennium — a multi-biennium reconcile outage strands a vanished committee active=true.
# Prod runs this weekly (Sun 07:00 UTC) via usa-wa-reconcile-committee-active.timer (#48).
# --dry-run previews the diff. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_active --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_active --biennium 2025-26

# Committee rename detection (#46) — write-side sibling of #45's read mirror. Diffs
# `GetCommittees(current)` vs `GetCommittees(prior)` on the stable `Id`; a changed
# `normalize_name(LongName)` is a rename. Emits windowed dated-name evidence (prior name
# typed `former`, effective_end = biennium-start boundary; new name typed `legal`,
# effective_start = same, open end — #58) so PM curates is_canonical and the #45 read mirror
# brings the windows back — emit-only, no local write. Diffs WSL's RAW LongName, not the
# PM-resolved Organization.name scalar (which would false-fire on PM canonicalisation + miss
# round-tripped renames). Guarded by empty-pull (either roster) + low-overlap
# (--min-overlap-fraction, default 0.5; stable WSL Ids → a real diff overlaps heavily, so a
# thin overlap = wrong-biennium pull) + rename-storm floor (--max-rename-fraction, default
# 0.34). Skips unanchored + live-cohort-absent (hidden vs unproduced). Idempotent.
# Prod runs this weekly (Sun 07:30 UTC) via usa-wa-reconcile-committee-names.timer (#53),
# staggered 30 min off the active reconcile.
# --dry-run previews. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_names --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_names --biennium 2025-26

# Joint/Other rename detection (#56) — meeting-derived sibling of #46 for the org_type='other'
# class CommitteeService can't see (#39; e.g. ESEC Id 13945). Diffs two bienniums'
# GetCommitteeMeetings-derived cohorts (current + prior) on the stable `Id`; the cohort name
# is the CLEAN `Name` (#61 observed_name), not the double-prefixed LongName stored as
# Organization.name — so the "Joint Joint …" form never reaches PM. Same windowed emit +
# shared spine as #46, but re-tuned guards for a dormancy-prone cohort: low-overlap OFF by
# default (--min-overlap-fraction 0.0 — window-absence is dormancy, not a wrong-biennium
# signal) and the storm fraction only weighed past --storm-floor-min-overlap (default 5).
# Window-absence ≠ rename (intersects ids present in BOTH windows). Emit-only; idempotent.
# Archive-first + read-only: a closed window is re-parsed offline from the RawPayload the daily
# refresh / #39 harvest already archived (no ~1.5MB re-pull); only an un-archived window falls
# back to a live, un-archived pull. Prod runs this weekly (Sun 07:45 UTC) via
# usa-wa-reconcile-committee-meeting-names.timer, staggered 15 min off #46.
# --dry-run previews. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# NOTE backfill: the detector diffs current-vs-PRIOR biennium, so an older rename (ESEC =
# 2023) needs a targeted --biennium 2023-24 (diffs vs 2021-22) to surface.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_meeting_names --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_meeting_names --biennium 2023-24

# Committee ↔ PM validation (#64) — read-only. For each PM-linked produced org, diff local
# canonical state against PM's live OrgDetail and bucket discrepancies (name/acronym/window/
# parent drift, unlinked/missing/merged), splitting reconciled (PM curation roundtripped)
# from divergent. Emit-nothing; sequential reads + bounded backoff.
# Exit 0 clean / 1 divergent / 2 auth / 3 empty-cohort abort.
python -m usa_wa_sync_powermap.validate_committees          # human table
python -m usa_wa_sync_powermap.validate_committees --json   # machine-readable

# Force-adopt PM curation for LWW-locked committees (#65 Part 2) — one-shot heal. For the
# anchored produced cohort, re-fetch each PM OrgDetail and force-apply it (upsert_from_pm +
# clock-parity stamp), bypassing LWW. Idempotent (no-op at parity). App-role local write.
# Remedies TWO skew sources: the pre-fill-only refresh (#65) and the anchor-stamp bump
# (#109) — pre-fix, every created row landed ahead of PM by the delivery round-trip, so
# reach for this after an anchor-stamp-era org skew as well (org is deliberately ungated,
# so nothing else converges it). Reported counter is force-adopts attempted, NOT rows
# changed.
python -m usa_wa_sync_powermap.heal_committee_curation --dry-run
python -m usa_wa_sync_powermap.heal_committee_curation

# Heal LWW-skewed assignment clocks (#102) — one-shot, the assignment analog of the committee
# heal above. A 2026-07-06 span backfill bumped ~4,300 anchored assignments' local updated_at
# ahead of PM, so the reconcile re-POSTs an IDENTICAL observation every cycle forever (PM no-ops
# it without advancing its clock → 429s). For each anchored assignment whose local clock is ahead
# of PM AND whose observation wouldn't change PM (local_newer_is_noop), adopt PM's clock ONLY (not
# PM's fields — for assignments WE are the authority) → LWW parity, churn stops. A genuine pending
# change (observation differs) is LEFT for the reconcile. App-role local write; read-only PM.
# The durable backstop is the apply_record local-newer no-op gate (deployed with the sidecar);
# this one-shot converges the EXISTING skew before the sidecar resumes. Idempotent (no-op at
# parity). Exit 0 clean / 2 auth / 3 empty-cohort abort.
python -m usa_wa_sync_powermap.heal_assignment_clocks --dry-run
python -m usa_wa_sync_powermap.heal_assignment_clocks

# Subscription prune (#73 Axis 1 step 6) — one-shot reclaim. build_reconciler narrowed the
# subscription set to the mirror set (jurisdiction lineage ∪ OUR anchored producer rows), but
# sync_subscriptions is additive, so the ~1,000 PM-only strangers the old whole-subtree walk
# registered stay subscribed-but-inert (feed delivers, reconciler fetch-then-skips them). This
# diffs PM's list_subscriptions against the freshly-discovered mirror set and unsubscribes the
# difference. Guarded: empty desired-set aborts (empty_desired), stale fraction over
# --max-prune-fraction aborts (prune_floor, default 0.9 — permissive since the first run removes
# ~half). Strangers have no local row (nothing evicted).
# Exit 0 clean / 2 auth / 3 aborted. RE-RUN TO CONVERGENCE: PM auto-subscribes the producer on
# observation write, so a concurrently-draining outbox regenerates a shrinking residual — the
# first pass over a busy system removes the bulk, then re-run until a --dry-run shows stale=0
# (best run when the outbox is quiescent). Observed 2026-07-07: 1226 → 303 → 31 → 0.
python -m usa_wa_sync_powermap.prune_subscriptions --dry-run
python -m usa_wa_sync_powermap.prune_subscriptions   # re-run until dry-run shows stale=0

# Retract spurious anchored assignments (#144 Phase 2) — one-shot producer retraction. Given
# local Assignment.source_id(s), retires each artifact tenure usa-wa produced (a WSL sponsor-
# archive chamber-conflation like Wynne LD39 Senate 2001-02 + its paired party span) through the
# sanctioned /observations channel via power-map#391's op:"retract" — no orphan, no /admin/ route.
# POSTs {identifier_type:pm_assignment_id, identifier_value:<ulid>, op:"retract"} (op rides the
# request model's additional_properties, the #111 pattern — NO client regen). On a retracted (or,
# on a re-run, auto-attached = PM's already-archived no-op) disposition it tombstones the local row
# (archived_at — the reversible axis mirroring PM's archive; _seat_scope excludes archived, so the
# succession --sweep-biennia audit clears). RETRACTION IS TERMINAL (power-map#391 shipped no
# reversible archived:false; un-retract is admin-only) — never build retry against un-retract.
# PM's anti-resurrection (both create doors attach to the archived twin) + the #144 Phase 1
# derivation exclusion (member_artifacts) keep the phantom span from ever returning.
# RUN SIDECAR-PAUSED. --dry-run resolves + previews WITHOUT POSTing (a retract POST is irreversible
# — a local rollback can't undo it, so dry-run must not touch PM). Idempotent: an already-archived
# target is a no-op already_retracted (resolve is full natural key (source,source_id) incl.
# archived, so a completed re-run isn't mis-reported not_found). Transient 429/5xx retries on a
# bounded backoff. App-role local write; read-only-shaped PM mutation via observation.
# Exit 0 clean / idempotent · 1 a target left unsettled (not-found / unanchored / PM-refused) · 2 auth
# · 3 a persistent transient PM outage past the backoff budget (idempotent — re-run once PM recovers).
python -m usa_wa_sync_powermap.retract_assignments --dry-run \
    --source-id 481:chamber-senate:39:2001-02 --source-id 481:party:republican:2001-02
python -m usa_wa_sync_powermap.retract_assignments \
    --source-id 481:chamber-senate:39:2001-02 --source-id 481:party:republican:2001-02
```

## Provenance & integrity

```bash
# Provenance integrity sweep (#54/#55) — re-hashes stored RawPayload bodies against
# their FetchEvent.content_hash baseline; a divergence is corruption/tamper at rest.
# NULL baselines (pre-#54 legacy) are counted as "unbaselined", never a mismatch.
# Exit 0 clean / 1 mismatch (the non-zero the #49 OnFailure handler emails on).
# The default run is a ROLLING byte-slice (#55): it verifies --byte-budget (default
# 256 MiB) worth of payloads past a persisted ULID watermark and wraps at the archive
# tail, so per-run cost stays flat as the #39 docket volume grows (whole corpus
# re-verified every ceil(bytes/budget) runs). Its one write is the cursor upsert on
# clearinghouse_core.integrity_sweep_state (app-role DML; not the provenance tables).
# --full forces a whole-corpus pass ignoring the cursor (post-incident audit);
# --limit N is a row-capped partial (surfaced as limited). Prod runs this weekly
# (Sun 08:00 UTC) via usa-wa-integrity-sweep.timer.
python -m clearinghouse_core.integrity                # rolling slice (resumes + wraps)
python -m clearinghouse_core.integrity --full         # whole corpus, ignore cursor
python -m clearinghouse_core.integrity --limit 500    # row-capped partial

# One-off provenance repair (#64) — OWNER ROLE. The pre-#54 committees:2025-26 fetch
# events have NULL content_hash but DID archive their bodies, so backfill
# content_hash = sha256(RawPayload.body) — converting them to integrity-verified while
# keeping the fetch history + bytes (no deletion). Payload-less NULL-hash events are
# skipped+counted. Idempotent. Needs DATABASE_URL_OWNER (the app role is REVOKEd UPDATE
# on the ledger, #54). --dry-run previews.
python -m usa_wa_adapter_legislature.baseline_unbaselined_committees --dry-run
python -m usa_wa_adapter_legislature.baseline_unbaselined_committees
```
