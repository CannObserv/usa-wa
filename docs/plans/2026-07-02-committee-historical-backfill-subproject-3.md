---
title: Committee historical backfill + full rename-chain — sub-project 3 implementation
date: 2026-07-02
status: draft
---

# Committee historical backfill + full rename-chain (sub-project 3)

Spec: [docs/specs/2026-07-02-committee-historical-backfill-design.md](../specs/2026-07-02-committee-historical-backfill-design.md)

## Problem

Only 2025-26 committees are ingested, but `CommitteeService.GetCommittees` returns
~35 committees/biennium back toward statehood. We want the full standing-committee
historical record — materialized by stable `Id`, wire archived, and each
committee's complete rename chain emitted to PM — without re-creating the #65 LWW
ping-pong or hammering WSL.

## Approach

Two decoupled phases (like #39 harvest vs #46/#56 reconcile). **Phase A** finds the
earliest biennium via a committee-only extent probe, then sweeps
`GetCommittees(biennium)` to it through `AdapterRunner(fill_only=True)` — archiving
each roster's wire under a **new resource id `committees-roster:<biennium>`**
(distinct from the daily `committees:<biennium>` GetActiveCommittees archive, a
different operation) and inserting committees by stable `Id` without clobbering
existing rows, with an inter-request pause. **Phase B** re-parses those archived
rosters offline (new transport `parse_committees`), builds each `Id`'s
`normalize_name(LongName)` timeline across all bienniums, detects the full rename
chain, and emits windowed `former`/`legal` dated-name evidence to PM via the
`committee_name_reconcile` spine — emit-only, deep-history-guardrailed. Both TDD.

## Tradeoffs / alternatives

- **Reuse the daily `committees:<biennium>` archive (GetActiveCommittees) for the
  timeline** — rejected: GetActiveCommittees is current-only "active" semantics; the
  historical timeline needs GetCommittees(biennium)'s full roster (the op #46
  diffs). Distinct operation → distinct resource id, no provenance collision.
- **One combined CLI (harvest + emit)** — rejected: couples WSL-facing archival to
  PM-facing emission; the spec's decoupling lets Phase B re-run/re-tune off the
  local archive without re-pulling WSL. Two CLIs.
- **Phase B diffs adjacent bienniums (reuse #56 as-is)** — rejected: only yields the
  latest hop. A full timeline over all archived rosters yields the whole chain
  (the spec's requirement); generalize the spine to N-biennium.
- **Freeze a committee seed (like #39)** — deferred (spec Open questions): the
  RawPayload archive is the durable record; re-harvest is cache-bounded.

## Steps

### Phase A — harvest + materialize

1. **Transport `fetch_committees(biennium) → WireFetch` — test + impl.** Analog of
   `fetch_active_committees` but calling `GetCommittees(biennium)`, returning parsed
   Committee records + pristine wire for archival. Cassette-backed test.
2. **Adapter routes the roster resource — test + impl.** Teach
   `WALegislatureAdapter` to dispatch `committees-roster:<biennium>` →
   `fetch_committees(biennium)` and normalize via the existing committees normalizer
   (GetCommittees returns the same Committee shape as GetActiveCommittees). Assert
   the daily `committees:<biennium>` path is untouched.
3. **Committee-only extent probe — test + impl.** A cheap `GetCommittees`-only
   backward walk (no meeting calls) from current to the earliest non-empty biennium
   (stop after N consecutive empties). Reuse/extend `probe_committee_extent`'s walk;
   fake-WSL tests for the boundary.
4. **`harvest_committees` CLI — test + impl.** Sweep `bienniums_in_range(floor,
   current)` through `AdapterRunner(fill_only=True)`, archiving
   `committees-roster:<biennium>` + inserting by `Id`; `--pause-seconds` between
   windows; `--from`/`--to`/`--dry-run`. Model on `harvest_committee_meetings` minus
   the seed-freeze. Tests: fill-only archival across a range, materialize-by-`Id`, no
   clobber of a PM-curated existing row, idempotent re-run (cache hits), pause invoked.

### Phase B — full rename-chain emission

5. **Transport `parse_committees(wire) → records` — test + impl.** Offline
   re-deserialize an archived `GetCommittees` envelope through the same
   CommitteeService binding (`binding.get("GetCommittees")` + `process_reply`), the
   #56 `parse_committee_meetings` analog. Cassette round-trip guard test.
6. **Committee roster cohort provider — test + impl.** Archive-first: load the
   `committees-roster:<biennium>` RawPayload for a biennium and `parse_committees` it
   offline (live fallback only for an un-archived window). Analog of
   `MeetingCohortProvider`.
7. **Full-timeline chain builder (pure) — test + impl.** Given `{biennium: {Id:
   LongName}}` across all bienniums, build per-`Id` ordered timelines on
   `normalize_name`, emit rename transitions as `(former, effective_end)` /
   `(legal, effective_start)` windows at biennium-start boundaries. Pure function;
   tests: single rename, multi-hop chain, dormancy gap (name persists across
   absence via consecutive-appearance diff), formatting-only change (no rename),
   per-boundary rename-storm floor (skip that boundary).
8. **`reconcile_committee_name_chain` CLI — test + impl.** Load all archived
   rosters via step 6, build the chain via step 7, emit windowed evidence via
   `committee_name_reconcile` / `to_names_observation` (emit-only, PM curates
   `is_canonical`); `--dry-run`, reconcile-family exit codes, empty-archive abort.
   Fake-PM-client tests incl. idempotent re-emit.

### Close-out

9. **Suite + lint + docs.** `uv run pytest` + `ruff` green; document both CLIs +
   the new resource id in AGENTS.md / COMMANDS.md.
10. **Operational (prod).** Run the committee-only probe (capture the true floor);
    `harvest_committees --dry-run` then live (with a pause); spot-check the emitted
    chain on `reconcile_committee_name_chain --dry-run` before the first live emit;
    then emit; re-run `validate_committees` to confirm no divergence introduced.

## Open questions / risks

- **Resource-id key.** Proposing `committees-roster:<biennium>` for the GetCommittees
  archive, distinct from the daily `committees:<biennium>` (GetActiveCommittees).
  Confirm the name before step 1 (it becomes durable provenance).
- **Split into two plans?** This is one sub-project but ~10 steps across two phases.
  If Phase A review runs long, Phase B (steps 5–8) can spin off into its own plan
  once Phase A's archive exists. Default: keep as one, execute Phase A → Phase B.
- **Risk — GetCommittees vs GetActiveCommittees shape drift.** Verify the two return
  the same Committee fields (`Id/Name/LongName/Agency/Acronym/Phone`) before reusing
  the normalizer (step 2); a missing field would need a normalizer guard.
- **Risk — deep-history WSL load / unknown limits.** ~68 sequential POSTs with a
  pause; one-time; closed windows cache. Do not parallelize.
- **Risk — old-biennium naming noise.** `normalize_name` may leave formatting-only
  diffs in very old rosters; the storm floor catches systematic boundaries,
  `--dry-run` catches the rest before emit.
