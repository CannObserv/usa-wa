# #56 — Joint/`Other` committee rename detection (meeting-derived)

Sibling of #46 for the meeting-derived (`org_type='other'`) committee class.
`CommitteeService.GetCommittees` is structurally blind to Joint/`Other` bodies
(#39), so #46's roster diff never sees their renames. Live instance: **ESEC
(`Id 13945`)** renamed by 2023 c 230 s 308, visible only via
`CommitteeMeetingService.GetCommitteeMeetings`.

## Problem

Detect a rename (stable WSL `Id`, changed name) for the Joint/`Other` class and
emit the same windowed dated-name evidence #46 emits, so PM curates `is_canonical`
and the #45 read mirror brings the window back. Emit-to-PM-only; no local write.

## Approach

Reuse #46's spine. Three deltas — only the first is in the issue:

1. **Source.** Diff two bienniums' *meeting-derived* cohorts (deduped Joint/`Other`
   committee refs across a `GetCommitteeMeetings` window, keyed by stable `Id`),
   not `GetCommittees` rosters. Intersect on `Id`s present in **both** windows —
   window-absence is dormancy, never a rename.

2. **Emit the clean `Name`, not the double-prefixed `LongName`** (correctness).
   The class stores WSL's agency-double-prefixed `LongName` ("Joint Joint …") as
   `Organization.name`; #61 established PM receives the clean `short_name`
   (`observed_name`). So the diff map is `{Id: clean Name}` and that clean string
   is what `to_names_observation` emits — detection and evidence use the same
   string, mirroring #46's "match and observe the same name" principle. (The agency
   prefix is stable, so diffing `Name` vs `LongName` detect identical renames.)

3. **Guard re-tuning for a dormancy-prone cohort.**
   - **Low-overlap guard relaxed.** #46's guard assumes near-total overlap (stable
     Ids, full roster every biennium). Meeting cohorts legitimately overlap thinly
     (a body that didn't meet is absent), so the guard's premise fails. Default the
     fraction to `0.0` (off) for this class; keep it operator-settable.
   - **Storm-floor minimum overlap.** The rename-storm fraction over a tiny overlap
     is hair-trigger (overlap 2, 1 rename = 0.5). Only apply the fraction when
     `overlap >= STORM_FLOOR_MIN_OVERLAP` (5); below that, skip the fraction check
     (the small absolute count can't be a "storm").
   - **Empty-pull** kept as-is (either window empty = failed pull).

Shared spine extracted to `clearinghouse`-adjacent core in usa-wa-sync-powermap so
#46 and #56 share the diff, guards, cohort queries (parametrized by `org_type`),
and per-row emit/eligibility.

## Frugality / provenance

Archive-first (revised after CR finding 6). The reconcile is read-only — it
detects, it doesn't produce. `MeetingCohortProvider` re-parses a closed window's
**already-archived** SOAP wire offline (through the same zeep binding, so the
parse can't diverge — a #54 fidelity concern), so the immutable ~1.5 MB prior
window isn't re-pulled weekly. Window archival stays owned by the daily refresh
(current window) and the #39 harvest (history); a window with no archived copy
falls back to a live, un-archived pull (matching #46's `GetCommittees` reads).
Steady state = zero WSL data pulls (one cheap WSDL load for the binding).

## Structure

- `usa_wa_sync_powermap/committee_name_reconcile.py` — shared spine:
  `diff_renamed`, guard evaluation, `live_cohort_by_source_id(org_type)`,
  `produced_source_ids(org_type)`, `emit_names`, `reconcile_names_from_maps`.
- `reconcile_committee_names.py` (#46) — builds committee maps from
  `GetCommittees`; delegates. Behaviour unchanged.
- `reconcile_committee_meeting_names.py` (#56) — builds `{Id: clean Name}` maps
  from `GetCommitteeMeetings` windows (current + prior); delegates with
  `org_type='other'` and relaxed guard defaults. CLI mirrors #46 (`--dry-run`,
  `--biennium`, guard overrides; exit codes 0/1/2/3).
- `usa_wa_adapter_legislature/normalize/committee_meetings.py` — extract
  `joint_other_refs(meetings) -> {source_id: ref}` (dedup + Agency filter);
  normalizer reuses it.
- meeting-cohort provider: `biennium -> {source_id: clean Name}` over `WSLClient`
  + `biennium_window` + `joint_other_refs`.

## Deploy

New weekly oneshot+timer `usa-wa-reconcile-committee-meeting-names`, staggered
~Sun 07:45 UTC (after active 07:00 / names 07:30, before integrity 08:00).
`OnFailure=` notify handler; `--frozen --no-sync`; add to `test_unit_ordering`
EXPECTED.

## ESEC backfill caveat

The detector diffs current-vs-immediately-prior biennium. ESEC's rename is 2023,
so a 2025-26 run (vs 2023-24) sees no change. Capture it with `--biennium 2023-24`
(diffs vs 2021-22), if the meeting service serves 2021-22. Ongoing detection at
each boundary is automatic.

## TDD order

1. `joint_other_refs` helper + normalizer refactor (green existing normalizer tests).
2. Shared spine extraction; #46 delegates (green existing #46 tests).
3. Meeting-cohort provider.
4. `reconcile_committee_meeting_names` reconcile + CLI tests.
5. Deploy unit + ordering test.
