# Runbook — biennium rollover (Nov 2026 → Feb 2027)

The first rollover since the #302 replatform (#135). On 2027-01-01 every clock in the
pipeline flips to `2027-28` at once: the harvests' window, `spans.current_biennium()`, and
the `active` flag on organizations. WSL publishes the new biennium on its own schedule,
and until it does, the nightly is expected to send email. This page says which email is
expected, what to check, and the few steps that stay manual.

Evidence for every "expected" below comes from the rehearsals in
[research/2026-10-01-biennium-rollover-rehearsal.md](research/2026-10-01-biennium-rollover-rehearsal.md),
run with `scripts/rollover-rehearsal.sh`.

## What runs on its own

| Unit | When | What it does in this window |
|---|---|---|
| `usa-wa-pipeline.timer` | daily 08:00 UTC | From **2026-11-04** the PDC and SOS harvests also fetch the 2026 general (`lookahead_election_year`). From **2027-01-01** every harvest targets `2027-28`. |
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
- **Re-run the rehearsal after #412 closes** (PR F changes the nightly chain) and after
  certification, so it runs on the real 2026 ballot:
  ```bash
  scripts/rollover-rehearsal.sh empty      # what upstream serves that day
  scripts/rollover-rehearsal.sh partial    # a synthesized half-published 2027-28 roster
  ```
  Each prints its scratch dir under `~/rehearsal/`; record the outcome lines in the
  research doc beside the October run. Delete the scratch dirs afterwards (~135 MB each).
- **Appointments dated in December** still need their operator `seated` event (#107).
  #282 tracks why an even-year November/December seating can be inert.

## 2027-01-01 — the expected email

Until WSL publishes 2027-28, the nightly exits 1 and mails. The expected email's
`failed stage:` lines are **exactly these two**:

```
usa_wa_adapter_legislature.raw_harvest (exit 4): job=wsl-raw-harvest outcome=degraded … errors=1 fanout_skipped=1 …
usa_wa_pipeline.build_warnings (exit 1): … not_clean=["test.usa_wa_pipeline.assignments_chamber_vacancy"] …
```

- The WSL harvest degrades because `GetCommittees(2027-28)` raises a server fault
  (`DataPortal.Fetch failed (Object reference not set…)`) until WSL publishes the new
  committees. It does not return an empty list. `GetSponsors(2027-28)` answers empty
  without a fault. **An outage looks different:** more than one error, or `fetched=0`.
- `assignments_chamber_vacancy` warns for both chambers: every 2025-26 span closed at
  2026-12-31 and nothing is open (`0 < 49`, `0 < 98`). This is a warning, not an error, so
  the registrar and publish still run.
- **Anything else is real.** That includes the SOS harvest, because by Jan 1 the
  certified 2026 export lands and only `2027` errs, so the source is not degraded.

The data state the same night, which is correct and needs no repair:

| Dataset | Expected |
|---|---|
| `assignments` | same row count; **0 active**; every 2025-26 span `valid_to = 2026-12-31` |
| `organizations` | same row count; every committee and Joint/Other body `active = false` (API.md) |
| publish | new versions of `assignments`, `organizations`, `stg_raw_fetches`; **no shrink refusal** |
| `registry_coverage`, `parity_citations` | ok |

## When WSL publishes 2027-28

The availability probe mails (exit 4) the morning a roster changes state: first rows, or `GetCommittees` stops faulting. `jq . data/research/wsl-availability.jsonl` shows what moved.

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
- The nightly email's closing `failed stage:` lines, compared against the two above.

## Manual steps

- [ ] 2026-11-04: the checklist above (the routine's #135 comment will prompt it)
- [ ] December: re-run both rehearsals; record them
- [ ] As each source starts answering: record the date in the research doc
- [ ] After 2027-02-28: `sudo systemctl disable --now usa-wa-wsl-availability-probe.timer`
      (the probe already asks nothing past `--until`). For the 2029 rollover, bump
      `--biennium` / `--until` in the unit and re-enable.
