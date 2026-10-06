# Modules — WA PDC adapter

Layer 3, `packages/usa-wa-adapter-pdc/`: the Public Disclosure Commission SODA
source. It archives the seated-winner cohorts into the raw store; the pipeline
stages them (`stg_pdc_winners`) and links each winner to a WSL member
(`match_pdc_wsl`, the `wa_pdc:<id>` ↔ `usa_wa_legislature:<id>` registry key).
The House Position seat belongs to the SOS ballot — [MODULES-SOS.md](MODULES-SOS.md);
the PDC-derived seat logic left in Python is in
[MODULES-FACTS-SEATS.md](MODULES-FACTS-SEATS.md). The Postgres half — adapter,
cohort provider, harvest and archive refresh — was deleted in #412 PR F.

```
  usa-wa-adapter-pdc/                 — Layer 3: WA PDC (Public Disclosure Commission) SODA source
    src/usa_wa_adapter_pdc/
      transport.py    — PDCClient: async `httpx` reader for the PDC `Campaign Finance Summary` Socrata dataset (`3h9x-7bvm`) on data.wa.gov. `fetch_house_winners(election_year)` GETs the seated House winner cohort (`office=STATE REPRESENTATIVE` ∧ `general_election_status='Won in general'` — one row per `(LD, position)`); `fetch_senate_winners(election_year)` (#75) is the Senate sibling (`office=STATE SENATOR` — one row per LD, ~half the chamber each even year). Both return `WireFetch` (pristine JSON bytes + the decoded rows) via a shared `_fetch_winners`. `parse_house_winners` / `parse_senate_winners` are the offline re-parsers. Optional `USA_WA_PDC_APP_TOKEN` → `X-App-Token` (rate-limit only, not auth — sent only when set)
      resources.py    — the `house-winners:` / `senate-winners:` prefixes (#412). Shared by `raw_harvest` and the pipeline's PDC staging
      parsing.py      — offline parse seam (#307): re-exports `parse_house_winners`/`parse_senate_winners` so `usa_wa_pipeline` staging depends on parsing without importing `transport`
      raw_harvest.py  — **Phase A** (#304, `python -m usa_wa_adapter_pdc.raw_harvest`, slug `pdc-raw-harvest`, run by `pipeline-nightly.sh`): every decisive cohort for the biennium (#121) — both House generals (`election_years_for_biennium`) + the three Senate cohorts (`senate_election_years_for_biennium` → `(start-1, start-3, start)`: WA Senate terms are 4-yr staggered, so the sitting senators come from the two most-recent even years, plus the odd `start` mid-biennium special) — fetched through `PDCClient` and written as pristine wires to `raw/usa_wa_pdc/` (`clearinghouse_core.rawstore`) under the archive's resource ids; per-cohort failures contained as `err` manifest entries, an uncontained one raised as `JobFailure` with the counters reached (#331); exit 4 = whole-source outage. A raceless year archives an empty SODA cohort (negative evidence, no error path). **Early capture (#135):** from the day after the next biennium's seating general it also fetches that year's House + Senate cohorts (`lookahead_election_year`; `today=` pins the date in tests) — raw only, since `match_pdc_wsl` pairs a cohort solely with the sponsors of the biennium it seats
      provisioning.py — get-or-create the `usa_wa_pdc` `Source` + seed its coverage claim; called nightly by `usa_wa_pipeline.coverage_seed`
      coverage.py     — **the PDC source's declared coverage claim** (#180): `election_year` 2008→open, **assumed** not verified — #79's `~2008 (the dataset's coverage)` was never probed, and the SODA feed has no error path at the floor (an under-served year archives an *empty* cohort), so a wrong bound is invisible
      normalize/positions.py — `PDC_SOURCE`, `PDC_PERSON_ID_SCHEME`, `pdc_person_identifier_source_id`: the Postgres-era `person_wa_pdc` identifier keying. No caller outside its tests since #412 PR F deleted the identifier emitter; the WA seat vocabulary it once carried lives in [`usa-wa-common`](MODULES-COMMON.md)
```
