# Runbook — biennium rollover (Nov 2026 → Feb 2027)

The first rollover since the #302 replatform (#135). Two clocks flip, on different days
(#597, `usa_wa_common.legislative_calendar`):

- the **filing window** (CannObserv/cli's rule) on **2026-12-07**, 00:00 Pacific, which is the
  08:00 UTC nightly. From then on, the harvests target `2027-28`, meetings windows are
  filing windows, and WSL also refreshes 2025-26 (the outgoing pass) until its terms end;
- the **term** on **2027-01-01**, at which `spans.current_biennium()` and `organizations.active`
  move together. #598 moves this to 2027-01-11 (RCW 44.04.021).

WSL publishes the new biennium on its own schedule.
This page says what to expect, what to check, and the few steps that stay manual.

**Status, 2026-11-24: WSL published 2027-28 before the rollover.** The availability probe
measured the first rows that morning: 74 House and 44 Senate sponsors, 34 committees, and
362 committee members. These are 2025-26 incumbents carried forward, plus one newcomer
(#595). The two #452 acceptances below were written for a rollover with nothing published
yet. Neither would ever have applied, so both were removed the same day, unused. Sections
that describe that unpublished window are kept as the record of what was planned.

Evidence for every "expected" below comes from the rehearsals in
[research/2026-10-01-biennium-rollover-rehearsal.md](research/2026-10-01-biennium-rollover-rehearsal.md),
run with `scripts/rollover-rehearsal.sh`.

## What runs on its own

| Unit | When | What it does in this window |
|---|---|---|
| `usa-wa-pipeline.timer` | daily 08:00 UTC | From **2026-11-04** the PDC and SOS harvests also fetch the 2026 general (`lookahead_election_year`). From #601's deploy (the first nightly after it) the WSL harvest also lands `2027-28`'s sponsors, committee roster and members (#599: the next biennium, from the night WSL publishes it; its own `next_biennium` counters). From **2026-12-07** every harvest targets `2027-28` (the filing window, #597), and the WSL harvest refreshes 2025-26 as `outgoing_biennium` until the term flip. |
| `usa-wa-wsl-availability-probe.timer` | daily 09:20 UTC, through 2027-02-28 | Measures how much of `2027-28` WSL serves (`data/research/wsl-availability.jsonl`). **Exit 4 = a roster changed state** (faulting, empty, has rows): the morning WSL starts publishing. Growth alone is logged, not mailed. |
| claude.ai routine `trig_01RrbHtAt554TKzoHDsBxAbc` | 2026-11-04 16:07 UTC, once | Checks the upstream sources from outside, then @mentions the owner on #135 with the 11-04 checklist below. |

## 2026-11-04 — the first early-capture nightly

A failing lookahead fetch **never alerts**. Each failed cohort is recorded as an `err`
manifest entry, and the 2024/2025 cohorts always land, so the run exits 0. Check by hand:

- [ ] `raw/usa_wa_sos_results/latest.json` has `sos-legresults:20261103` with a recent `fetched_at`
- [ ] `raw/usa_wa_pdc/latest.json` has `house-winners:2026` and `senate-winners:2026` (an empty cohort before PDC marks winners is expected)
- [ ] `journalctl -u usa-wa-pipeline --since 2026-11-04 | grep -E 'sos_raw_harvest_cohort_failed|pdc_raw_harvest_cohort_failed'` returns nothing
- [ ] the `pdc_raw_harvest_complete` / `sos_raw_harvest_complete` lines carry `lookahead_year: 2026`

From the same night, the published `stg_sos_results` carries the 2026 election's
**provisional** counts, which change nightly until SOS certifies (~2026-12-03).

## November–December

- **Certification (~early Dec).** `sos-legresults:20261103` re-lands as a new sha256, and
  PDC starts marking `Won in general`, so `house-winners:2026` / `senate-winners:2026` grow
  from empty to 98 / ~25 rows. Nothing derives from them yet: they seat a biennium with no
  roster (pinned by `test_match_pdc_wsl`, `test_conformed_house`).
- **Re-run the rehearsal after #412 closes** (PR F drops the canonical and provenance
  tables from the database the nightly runs against; the chain itself is unchanged) and after
  certification, so it runs on the real 2026 ballot:
  ```bash
  scripts/rollover-rehearsal.sh empty      # what upstream serves that day
  scripts/rollover-rehearsal.sh partial    # a synthesized half-published 2027-28 roster
  ```
  **Since 2026-11-24 (#596) only `empty` models anything.** WSL has published 2027-28, and
  the nightly lands it (#599), so `empty` now rehearses the published case on the real
  roster. Expect `build_warnings` to exit 1 on the partial roster's vacancy until it turns
  informational (#599 D3). `partial` overwrites the real `sponsors:2027-28` wire in its
  scratch copy with a synthesized one, so it no longer rehearses anything real: skip it.
  (The #452 expectation, `empty` exiting 0 with both acceptance lines, applied only before
  WSL published.)
  Not while the 08:00 nightly runs (the wrapper refuses: it would copy a half-written run).
  Each prints its scratch dir under `~/rehearsal/`; record the outcome lines in the
  research doc beside the October run. Delete the scratch dirs afterwards (~135 MB each).
- **Appointments dated in December** still need their operator `seated` event (#107).
  #282 tracks why an even-year November/December seating can be inert.

## 2027-01-01

**Planned (superseded 2026-11-24):** a rollover with nothing published, where the nightly
accepted the `GetCommittees` fault and the all-closed vacancy warning (#452) and sent no email.
WSL published first, so neither shape can occur and both acceptances are gone. Any email on
the night is unaccepted:

- **The WSL harvest** should be ok. `GetCommittees(2027-28)` has answered since 11-24, so a
  fault now is an outage.
- **The vacancy warning** fails `build_warnings` (exit 1) for as long as WSL's 2027-28
  roster is short of 49 senators and 98 seated House Positions. As of 11-24 it was 44 and 74
  sponsors. House seats reopen only through the 2026 ballot (below). That email is real: it
  names the open seats.
- **If no email came,** check that the nightly ran (`GET /health/datasets`,
  `GET /api/v1/health/jobs`).

## When WSL publishes 2027-28

The availability probe mails (exit 4) the morning a roster changes state: first rows, or `GetCommittees` stops faulting. `jq . data/research/wsl-availability.jsonl` shows what moved.

**Done for 2027-28 (2026-11-24):** WSL published before 2027-01-01, so the two #452
acceptances were removed before they ever applied, and the date is recorded in the research
doc. At the next rollover, an entry still in place when sponsors land fails both stages with
`stale_acceptances` (`wsl_raw_harvest_acceptance_stale` / `dbt_build_acceptance_stale`) every
night until it is removed. The log line's `follow_up` names the cleanup.

- **Sponsors land.** The next nightly stages them. Returning members' Senate and party
  spans reopen with their original start (same span keys). The registrar mints
  newcomers (#403), and `match_pdc_wsl` pairs the 2026 winners. **House seats reopen
  only through the 2026 ballot:** a 2027-28 Position comes from `sos-legresults:20261103`
  alone, so with no ballot in `raw/` every House seat stays closed (rehearsal: 0 of 61).
  `assignments_chamber_vacancy` stops once both chambers fill; a partial roster keeps it
  warning, with smaller deficits.
- **Committees land.** The WSL harvest stops degrading, committee spans reopen, and
  committees read `active` again.
- If `registry_coverage` reports `unregistered_spans` **two** nights running, the
  registrar did not close the one-night lag. That is real.

## What to watch

- `GET /health/datasets`: the catalog heartbeat. It should stay fresh every night of
  the window, because publish runs despite the warnings.
- `GET /api/v1/health/jobs`: the latest run per job slug. Rehearsal runs never appear
  here (`USA_WA_JOB_LEDGER=0`).
- Any nightly email in the window: no acceptance is in force, so each one is real.

## Manual steps

- [ ] 2026-11-04: the checklist above (the routine's #135 comment will prompt it)
- [ ] December: re-run both rehearsals; record them
- [ ] As each source starts answering: record the date in the research doc (WSL: 2026-11-24)
- [x] Remove both #452 entries: done 2026-11-24, before they applied (§ When WSL publishes)
- [ ] After 2027-02-28: `sudo systemctl disable --now usa-wa-wsl-availability-probe.timer`
      (the probe already asks nothing past `--until`). For the 2029 rollover, bump
      `--biennium` / `--until` in the unit and re-enable.
