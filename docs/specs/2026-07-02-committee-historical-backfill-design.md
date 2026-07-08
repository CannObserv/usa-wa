# Committee Historical Backfill + Full Rename-Chain Emission

**Date:** 2026-07-02
**Status:** SUPERSEDED by
[2026-07-02-committee-historical-backfill-redesign.md](2026-07-02-committee-historical-backfill-redesign.md)
— the stable-WSL-`Id` premise below was disproven operationally (WSL re-keys
committees across eras). The redesign switches to identity = WSL Id (distinct org
per Id). Kept for the record.
**Scope:** Sub-project 3 of the committee validate/backfill effort. Follows
sub-project 1 (validation + provenance, #64) and the #65 LWW-ping-pong fix.

## Context

Sub-project 1 built the validation tooling and a write-free extent probe. The
probe answered "how much data exists":

- **Meeting docket** (`CommitteeMeetingService`) spans **1999-00 → 2025-26** (~26k
  meetings, ~19 MB wire), empty at 1997-98 and earlier.
- **Standing committees** (`CommitteeService.GetCommittees`) return ~35/biennium
  for **every** biennium back toward statehood — far deeper than the meeting
  service.

**Chamber-op redundancy (verified — do not re-confirm).** `GetHouseCommittees(b)`,
`GetSenateCommittees(b)`, and the `GetActive{House,Senate}Committees()` pair are
**wholly redundant** with `GetCommittees(b)` / `GetActiveCommittees()` — they are
strictly the same rows filtered by `Agency`. Verified live 2026-07-03 across
1991-92 (the floor), 2001-02, 2013-14, 2025-26: `GetCommittees(b) ≡
GetHouseCommittees(b) ∪ GetSenateCommittees(b)` exactly (empty symmetric difference
both directions), the per-committee dicts are byte-identical (same
`Id, Name, LongName, Agency, Acronym, Phone` keyset), and the `Active` variants
partition identically. **No `CommitteeService` op ever returns a Joint/`Other`
agency** — every op yields only `{House, Senate}`, reconfirming that
`CommitteeMeetingService.GetCommitteeMeetings` is the *sole* Joint/`Other` channel
(#39). So the chamber ops carry zero unique data and are correctly ignored;
`GetCommittees(biennium)` is the complete standing-committee roster source.

Today only **2025-26** committees and **2023-24/2025-26** meetings are ingested.
The decisions taken in the opening brainstorm (and confirmed against the probe):

- **Backfill standing committees to full depth** (earliest available biennium).
- **Materialize** every body keyed by its stable WSL `Id`; a changed name across
  bienniums is a **rename** (→ `former`/`legal` dated-name evidence), while mere
  absence is **not** a retirement signal (no `archived_at` from absence).
- **Emit the full rename chain** to PM (not just adjacent-biennium).
- Periodic change-detecting re-fetch of closed bienniums is **sub-project 4**.

The #65 fix is load-bearing here: the daily refresh clobbered PM-curated fields
via `ON CONFLICT DO UPDATE`, causing an LWW ping-pong. Backfilling decades of
committees through the same path would amplify it — so Phase A uses the
`AdapterRunner(fill_only=True)` mechanism #65 introduced and proved in production.

## Goals

- Archive the pristine SOAP wire for every historical `committees:<biennium>`
  window (FetchEvent + RawPayload + `content_hash`, #54), once per window.
- Materialize standing committees keyed by stable `Id` — insert-only, never
  clobbering a PM-curated existing row (fill-only).
- Build each committee's full dated-name history and emit the complete
  `former`/`legal` rename chain to PM, derived entirely from the local archive.

## Non-goals

- Joint/Other (`org_type='other'`) full-chain — stays with #56's adjacent-biennium
  detector; their meeting docket floors at 1999 so there is no deep history.
- Periodic change-detecting re-fetch / re-validation of closed bienniums —
  **sub-project 4**.
- Historical `active` reconciliation — #44 governs the current biennium; historical
  committees get **no** `archived_at` and **no** `active=false` from absence.
- A frozen committee seed — **deferred** (see Open questions). The RawPayload
  archive is the durable record; re-harvest is idempotent + cache-bounded.

## Two-phase architecture

Decoupled exactly like #39 harvest vs #46/#56 reconcile: Phase A is
WSL-facing/archival/idempotent; Phase B derives entirely from the local archive and
can re-run/re-tune/re-emit without touching WSL.

### Phase A — Harvest + materialize (fill-only)

`python -m usa_wa_adapter_legislature.harvest_committees`

- **Find the floor first.** The sub-project-1 both-empty probe never terminates for
  committees (unbounded backward history). Phase A runs a **committee-only extent
  probe** (`GetCommittees` only — no slow meeting calls) to find the earliest
  biennium that returns data, then sweeps to it.
- **Sweep** `GetCommittees(biennium)` across the full range **through
  `AdapterRunner(fill_only=True)`**. Each `committees:<biennium>` window archives
  its wire once (dedup-bounded) and **inserts** committees keyed by stable `Id`.
  A body seen across bienniums is one org (untouched on re-observe — fill-only);
  a body seen only historically is a new org with **no** `archived_at`.
- **Idempotent.** Closed windows are cache hits on re-run; fill-only guarantees a
  re-harvest never clobbers PM curation or bumps `updated_at`.
- `--from` / `--to` / `--dry-run`. Hits live WSL (one POST per window) and mutates
  the DB (archival + inserts); not read-only.

### Phase B — Full rename-chain emission (archive-derived → PM)

`python -m usa_wa_sync_powermap.reconcile_committee_name_chain`

- **Read the archive offline.** Re-parse every archived `committees:<biennium>`
  RawPayload through the CommitteeService binding — a new transport
  `parse_committees` (analog of #56's `parse_committee_meetings`), guarded by a
  cassette round-trip test. No WSL re-pull.
- **Build per-`Id` timelines.** For each stable `Id`, collect
  `(biennium → normalize_name(LongName))` across all bienniums, ordered.
- **Detect transitions.** Walk consecutive *appearances*; each normalized-name
  change is a rename. A committee with K distinct names emits K−1 windows: the
  prior name typed `former`, `effective_end` = the changed biennium's start
  boundary; the new name typed `legal`, `effective_start` = same boundary, open end
  (#58 windowing generalized to the whole chain).
- **Which name.** Diff/emit on WSL's raw `LongName` via `normalize_name` — **not**
  the PM-resolved scalar (#46: avoids false-firing on PM canonicalization, catches
  round-tripped renames). Standing committees only.
- **Emit-only.** Windowed `former`/`legal` evidence via
  `OrganizationDescriptor.to_names_observation`; the #45 read-mirror brings PM's
  curation back, and #65's fill-only + heal ensure it sticks. No local write.
- Idempotent: windows keyed by their natural key; re-running re-asserts identical
  evidence (PM observation dedups).

### Guardrails (re-tuned for deep history)

- **Normalize before compare** — old bienniums drift in punctuation/casing; compare
  on `normalize_name` so formatting churn isn't a false rename.
- **Empty/unparseable biennium → skip** — a gap in the archive is not a signal.
- **Dormancy-aware** — compare consecutive *appearances* of an `Id`; an absence gap
  is spanned (the name persists across it, per "absence ≠ retirement").
- **Rename-storm floor per boundary** — if an outsized fraction of `Id`s change
  normalized name at a single biennium boundary, that is a systematic WSL
  reformat/re-key, not real renames → flag + skip that boundary rather than emit a
  storm of bogus `former`s.

## Testing (TDD)

- **Phase A** — fake WSL + runner: fill-only archival across a range,
  materialize-by-`Id`, no clobber of an existing PM-curated row, idempotent re-run
  (cache hits). Committee-only extent probe: backward walk + earliest-boundary stop.
- **Phase B** — a **pure timeline-construction function** tested with crafted `Id`
  name sequences: single rename, multi-hop chain, dormancy gap (name persists),
  formatting-only change (no rename), storm boundary (skip). Emission with a fake PM
  client (windowed evidence, idempotent re-emit, exit codes).
- **Transport** — cassette round-trip guard for the offline `parse_committees`
  (re-run after any zeep bump, like #56).

## Sequencing

1. Phase A: committee-only extent probe → sweep `GetCommittees` to the floor,
   archiving wire + materializing fill-only.
2. Phase B: re-parse the archive → build per-`Id` timelines → emit the full
   `former`/`legal` chain to PM.

On completion, PM holds the full standing-committee historical record with its
rename chains, and the local archive is the durable basis for sub-project 4's
periodic re-validation.

## Open questions / risks

- **Committee seed — deferred.** A frozen seed (like #39's Joint/Other) would let a
  fresh deploy materialize historical committees without re-hitting WSL. Deferred:
  the RawPayload archive is the durable record and re-harvest is cache-bounded.
  Revisit if fresh-deploy provisioning without WSL access becomes a requirement.
- **Risk — deep-history data quality.** Very old `LongName`s may be inconsistent
  enough that `normalize_name` still leaves formatting-only diffs. The storm floor
  catches a systematic boundary; spot-check the emitted chain on a `--dry-run`
  before the first live emit.
- **Risk — WSL load.** Phase A is one POST per biennium (~68). Sequential, one-time;
  closed windows cache on re-run. Acceptable for a backfill; do not parallelize
  against WSL. Add a small configurable **inter-request pause** (`--pause-seconds`,
  default a second or two) between window fetches so a full-depth sweep drips rather
  than bursts against WSL — a courtesy to a vital upstream, and headroom against any
  unadvertised rate limit.
- **Offline re-parse fidelity (#54).** `parse_committees` must re-deserialize the
  archived wire through the *same* CommitteeService binding so the re-parse can't
  diverge from the live parse — the cassette round-trip test is the guard.
