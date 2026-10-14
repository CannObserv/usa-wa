# Runbook — biennium rollover (Nov 2026 → Feb 2027)

The first rollover since the #302 replatform (#135). On 2027-01-01 every clock in the
pipeline flips to `2027-28` at once: the harvests' window, `spans.current_biennium()`, and
the `active` flag on organizations. WSL publishes the new biennium on its own schedule,
and until it does, the nightly carries two known failures, which it accepts in code (#452)
rather than mailing about. This page says what that looks like, which email is expected
when WSL publishes, what to check, and the few steps that stay manual.

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
- **Re-run the rehearsal after #412 closes** (PR F drops the canonical and provenance
  tables from the database the nightly runs against; the chain itself is unchanged) and after
  certification, so it runs on the real 2026 ballot:
  ```bash
  scripts/rollover-rehearsal.sh empty      # what upstream serves that day
  scripts/rollover-rehearsal.sh partial    # a synthesized half-published 2027-28 roster
  ```
  Expected since #452: `empty` exits 0 with both acceptance lines in `nightly.log`;
  `partial` (synthesized sponsors stage) has `build_warnings` exit 1, still warning on the
  vacancy with `stale_acceptances` naming it — the "WSL published" path.
  Not while the 08:00 nightly runs (the wrapper refuses: it would copy a half-written run).
  Each prints its scratch dir under `~/rehearsal/`; record the outcome lines in the
  research doc beside the October run. Delete the scratch dirs afterwards (~135 MB each).
- **Appointments dated in December** still need their operator `seated` event (#107).
  #282 tracks why an even-year November/December seating can be inert.

## 2027-01-01 — no email expected

Until WSL publishes 2027-28, the nightly carries two known failures and **accepts both**
(#452, the #333 `ACCEPTED_OUTAGES` pattern), so it exits 0 and sends **no email**. Look for
the acceptance lines in the journal instead:

```bash
journalctl -u usa-wa-pipeline --since today | grep -E 'wsl_raw_harvest_accepted_fault|dbt_build_accepted_warning'
```

Each names `#452`. The vacancy line appears every night until 2027-28 sponsors stage; the
harvest line only while `GetCommittees` still faults (if WSL publishes committees first, the
harvest is simply ok). The stage summaries carry
the same facts: `job=wsl-raw-harvest outcome=ok … errors=1 fanout_skipped=1 … roster_faulted=1
sponsor_rows=0 accepted_faults=["2027-28"]` and `job=dbt-build-warnings outcome=ok … warned=1
not_clean=[] accepted_warnings=["test.usa_wa_pipeline.assignments_chamber_vacancy"]`.

- **The WSL harvest fault.** `GetCommittees(2027-28)` raises a server fault
  (`DataPortal.Fetch failed (Object reference not set…)`) until WSL publishes the new
  committees. It does not return an empty list. `GetSponsors(2027-28)` answers empty
  without a fault. `ACCEPTED_ROSTER_FAULTS` accepts exactly that shape: a SOAP fault on the
  roster (`roster_faulted=1`), as the run's only error, while sponsors answered with no rows
  (`sponsor_rows=0`). **An outage looks different and still mails:** a transport error on
  the roster, more than one error, `fetched=0`, or sponsors that did not land.
- **The vacancy warning.** `assignments_chamber_vacancy` warns for both chambers: every
  2025-26 span closed at 2026-12-31 and nothing is open (`0 < 49`, `0 < 98`).
  `ACCEPTED_WARNINGS` accepts that one node, as a `warn` only, while the built duckdb stages
  no 2027-28 sponsors. Any other warning still fails the stage.
- **Any email in the window is real.** That includes the SOS harvest, because by Jan 1 the
  certified 2026 export lands and only `2027` errs, so the source is not degraded.
- **If the `dbt_build_accepted_warning` line is missing** and no email came, check that the
  nightly ran at all (`GET /health/datasets`, `GET /api/v1/health/jobs`).

The data state the same night, which is correct and needs no repair:

| Dataset | Expected |
|---|---|
| `assignments` | same row count; **0 active**; every 2025-26 span `valid_to = 2026-12-31` |
| `organizations` | same row count; every committee and Joint/Other body `active = false` (API.md) |
| publish | new versions of `assignments`, `organizations`, `stg_raw_fetches`; **no shrink or floor refusal** (a floor refusal is a real regression — the floors sit ~5% under the 2026-10-06 counts, #472) |
| `registry_coverage`, `parity_citations` | ok |

## When WSL publishes 2027-28

The availability probe mails (exit 4) the morning a roster changes state: first rows, or `GetCommittees` stops faulting. `jq . data/research/wsl-availability.jsonl` shows what moved.

**The next nightly emails too, on purpose.** Once `GetSponsors(2027-28)` has rows, both #452
acceptances are false claims: the WSL harvest and `build_warnings` each fail (exit 4, or 1
while the vacancy still warns) with `stale_acceptances` and log `wsl_raw_harvest_acceptance_stale` / `dbt_build_acceptance_stale`,
every night until the entries are removed. The log line's `follow_up` is the cleanup: remove
the 2027-28 entry from `ACCEPTED_ROSTER_FAULTS`
(`usa_wa_adapter_legislature/raw_harvest.py`) and from `ACCEPTED_WARNINGS`
(`usa_wa_pipeline/build_warnings.py`), and record the date in the research doc. If WSL
publishes before 2027-01-01, neither acceptance ever applies, and the stale email arrives on
the first nightly of 2027.

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
- The journal's two #452 acceptance lines each night of the window; any email then is real.

## Manual steps

- [ ] 2026-11-04: the checklist above (the routine's #135 comment will prompt it)
- [ ] December: re-run both rehearsals; record them
- [ ] As each source starts answering: record the date in the research doc
- [ ] On the first stale-acceptance email: remove both #452 entries (§ When WSL publishes)
- [ ] After 2027-02-28: `sudo systemctl disable --now usa-wa-wsl-availability-probe.timer`
      (the probe already asks nothing past `--until`). For the 2029 rollover, bump
      `--biennium` / `--until` in the unit and re-enable.
