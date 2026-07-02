# Committee Data Validation + Provenance Cleanup

**Date:** 2026-07-02
**Status:** Approved (design)
**Scope:** Sub-project 1 of a four-part effort to validate, backfill, and durably
re-validate WA Legislature committee data.

## Context

Recent work (#39/#46/#56/#58/#61) landed retrieval of standing committees
(`CommitteeService`) and Joint/Other committees (`CommitteeMeetingService`) from
the WA Legislature SOAP endpoints, normalized to `canonical.organizations`, and
roundtripped to Power Map (PM) via the sync sidecar.

Current production state (2026-07-02):

- **58 orgs, all PM-linked** (`pm_organization_id` set): 34 `committee`
  (standing, 2025-26 only), 21 `other` (Joint/Other), 2 `chamber`, 1
  `legislature`. Plus 73 `organization_names`, 50 `organization_acronyms`.
- **Provenance:** 10 `raw_payloads` (~4.3 MB), 12 `fetch_events`. **6 have NULL
  `content_hash`** — the Jun 19–28 daily `committees:2025-26` pulls that predate
  the #54 archival/baseline (payload-less; nothing to hash retroactively).
- **Coverage:** committees fetched for **2025-26 only**; meeting windows for
  **2025-26 and 2023-24 only**.
- **Integrity sweep:** never run — cursor still NULL.
- **Outbox:** 4,145 DELIVERED, zero pending/failed.

The pipeline works end-to-end. Before pushing deeper history through it, we want
to (a) prove the current cohort roundtripped cleanly (inclusive of PM-side
curation), and (b) clean up the provenance corpus.

## Decomposition (Approach A — sequential, validation-gated)

The full effort is four sub-projects, each its own spec → plan → implementation
cycle, executed in order:

1. **Committee data validation + provenance cleanup** — *this spec*. Read-only
   local↔PM reconciliation CLI; delete the 6 unbaselined fetch_events; first
   integrity-sweep pass. Plus the lightweight probe as an immediate follow-on.
2. *(within this spec)* **Probe sweep** — cheap discovery of the earliest
   available biennium + data volume. Answers "how much exists," sets the phase-3
   cutoff. Read-only, no archival.
3. **Historical backfill + full-chain rename** — sweep all bienniums, materialize
   orgs keyed by stable `Id`, archive wire, detect the full rename chain, emit
   `former`/`legal` dated-name evidence to PM. Designed *after* the probe reports
   volume. **Deferred to its own spec.**
4. **Periodic historical re-validation** — slow-cadence change-detecting re-fetch
   of closed bienniums + systemd timer + divergence → re-emit. **Deferred to its
   own spec.**

Validation (1) gates backfill (3): prove the current 58-org cohort is clean
before growing the corpus. The probe (2) gates the *scope* of 3 and 4.

## Goals

- Rerunnable, read-only tool that diffs local canonical orgs ↔ PM live state and
  classifies every discrepancy — inclusive of PM-side curation (name/acronym
  canonicalization, merges).
- A clean provenance corpus: the 6 payload-less pre-baseline fetch_events removed,
  a first full integrity-sweep pass confirming zero mismatches, and the weekly
  sweep timer confirmed firing.
- A write-free probe reporting the true extent of available committee data.

## Non-goals

- No backfill of historical bienniums (sub-project 3).
- No periodic re-validation timer (sub-project 4).
- No changes to the runner's cache/TTL policy.
- No PM writes of any kind — this sub-project is read-only against PM.

## Component 1 — Validation reconciliation CLI

`python -m usa_wa_sync_powermap.validate_committees`

Read-only member of the `reconcile_*` family. For each `canonical.organizations`
row with a `pm_organization_id`, fetch PM's live `OrgDetail` via
`PowerMapClient.get_entity` and diff local ↔ PM. **Emits nothing** — pure report.

### Plumbing (reuses existing surfaces)

- `GeneratedPowerMapClient` / `PowerMapClient` Protocol for PM reads
  (`get_entity`); `SidecarSettings` for `POWERMAP_BASE_URL` / `POWERMAP_API_KEY`.
- Local reads via the app-role session factory. `SELECT` only both sides — no
  observations, no local writes.
- Guarded by an empty-cohort abort (a zero-org local read is a bug, not "all
  clean"), matching the reconciler convention.

### Discrepancy classes

Per PM-linked org, diff local ↔ PM's live `OrgDetail`:

| Class | Local | PM | Meaning |
|---|---|---|---|
| `unlinked` | `pm_organization_id` NULL | — | never roundtripped (expected 0 today) |
| `missing-in-pm` | linked | `get_entity` → 404 | PM deleted/merged the org |
| `merged` | linked | 404 resolvable via `merged_into` | PM merged it; flag if local didn't re-resolve (#37) |
| `name-drift` | `name` / `short_name` | PM canonical scalar | PM curated a different canonical name |
| `acronym-drift` | `acronym` | PM canonical acronym | PM curated a different canonical acronym (#47) |
| `names-window-drift` | `organization_names` set | PM `names[]` | local didn't mirror a PM window (former/legal/is_canonical) or vice-versa (#45) |
| `acronyms-drift` | `organization_acronyms` set | PM `acronyms[]` | set mismatch |
| `parent-drift` | `parent_organization_id`→pm_id | PM parent | hierarchy diverged |

### Reconciled vs. divergent

PM is authority for the canonical name/acronym scalar; local *mirrors* it (read
mirror #45/#47). So a scalar difference is not automatically an error:

- **`reconciled`** (informational): local and PM differ, but local has **adopted**
  PM's curated value — i.e. PM curation flowed back correctly. Reported, not
  counted as a failure.
- **`divergent`** (actionable): local and PM disagree **and** local has not
  adopted PM's value — a mirror lag or break.

The report separates the two. Exit code is driven by `divergent` (and hard
errors), not by `reconciled`.

### Output & exit codes

- Structured summary: counts per discrepancy class, split reconciled/divergent.
- Detail table; `--json` for machine consumption.
- Reports the count of unbaselined (NULL content_hash) fetch_events so the
  provenance gap is visible, not silent.
- Exit codes (reconcile family): `0` clean · `1` divergences found · `2` auth
  block. This makes the tool a candidate for the #49 `OnFailure` alerting family
  later (not wired in this sub-project).

## Component 2 — Provenance cleanup

### 2a — Baseline the 6 unbaselined fetch_events (premise corrected 2026-07-02)

The Jun 19–28 `committees:2025-26` daily pulls predate the #54 baseline: they
carry NULL `content_hash`. **Correction (discovered during the operational run):**
they are **not** payload-less — each archived its ~5.7 KB body (a `RawPayload`
exists). The original plan to *delete* them was premised on their being
payload-less; that premise was false, and deletion would have destroyed 6
archived committee payloads for no benefit.

**Disposition: retroactively baseline them** (owner-role, since the app role is
REVOKEd `UPDATE` on `fetch_events`, #54). For each NULL-hash event that has a
body, set `content_hash = sha256(RawPayload.body)` — exactly the digest the runner
now derives (`AdapterRunner._record_fetch_event`). This converts them from
"unbaselined" to integrity-verified while **keeping** both the fetch history and
the archived bytes; no citation re-pointing or deletion needed. A NULL-hash event
with no body (none exist for this resource) is counted `skipped_no_payload` and
left alone — never treated as verified. Idempotent; verified by tests.

### 2b — First integrity-sweep pass + timer verification

The sweep cursor is NULL — the sweep has never run. As an operational step:

- Run `python -m clearinghouse_core.integrity --full` once to establish a clean
  whole-corpus pass over all payloads (~4.3 MB — trivial), confirming zero
  mismatches before the corpus grows.
- **Verify `usa-wa-integrity-sweep.timer` is enabled and firing** on the VM — a
  persistently NULL cursor may mean the weekly timer never ran (vs. simply being
  newly deployed). If it isn't firing, fixing it is part of this sub-project.

Run 2b **after** 2a so the baseline pass reflects the cleaned corpus.

## Component 3 — Probe sweep (follow-on)

`python -m usa_wa_adapter_legislature.probe_committee_extent`

Cheap, **read-only, write-free** discovery to answer "how much data exists" and
set the phase-3 cutoff.

- Walks candidate bienniums **backward from current** (2025-26, 2023-24, …),
  calling `CommitteeService.GetCommittees(biennium)` and
  `CommitteeMeetingService.GetCommitteeMeetings(window)` per biennium.
- Records per biennium: whether the service returns data, row count, approximate
  wire bytes.
- **Earliest-boundary heuristic:** stop after N consecutive empty bienniums
  (service returns nothing → earliest available reached). Preferred over a
  hardcoded floor year — the services define their own extent.
- **No DB writes, no RawPayload** — explicitly bypasses the runner's archival
  path. We learn the extent *before* committing to archive it; the real backfill
  (sub-project 3) does archival properly through the runner.
- Emits a table: biennium → committee count, meeting count, wire size + totals.
  This output feeds the sub-project 3 spec (how far back, total volume).

## Testing (TDD)

- **Validation CLI:** unit tests with a `FakePowerMapClient` returning crafted
  `OrgDetail` responses — one fixture per discrepancy class (unlinked, missing,
  merged, name-drift-reconciled, name-drift-divergent, acronym-drift,
  names-window-drift, parent-drift, clean). Assert each sorts into the right
  bucket and the exit code (0/1/2). Empty-cohort abort test.
- **Probe CLI:** unit tests with a fake WSL transport returning data for a few
  bienniums then empties — assert the backward walk, the N-consecutive-empty
  stop, and the tallies. No live WSL.
- **Cleanup migration:** test that the 6 payload-less fetch_events and their
  citations are removed/re-pointed without orphaning provenance FKs; assert
  baselined rows and their payloads survive untouched.
- Integrity `--full` run + timer check are operational steps (verified by output,
  not new tests).

## Sequencing

1. Validation CLI (build + run against production; confirm current cohort clean).
2. Provenance cleanup 2a (delete 6), then 2b (`--full` sweep + timer verify).
3. Probe sweep (run; capture volume for the sub-project 3 spec).

On completion, the current cohort is proven clean, the corpus is baselined, and
we know the true extent of available history — the inputs to brainstorming
sub-project 3.
