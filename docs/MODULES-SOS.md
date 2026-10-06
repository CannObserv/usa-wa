# Modules — WA Secretary of State adapter

Layer 3, `packages/usa-wa-adapter-sos/`: the multi-source target package for
WA Secretary of State — one source subpackage per SOS feed, each a self-contained
archive. The pattern it follows is in [ARCHITECTURE.md](ARCHITECTURE.md). The
House Position seat the results feed decides is composed by
`usa_wa_facts_seats.house` ([MODULES-FACTS-SEATS.md](MODULES-FACTS-SEATS.md)) and
the pipeline's conformed House. The Postgres half — adapters, cohort providers,
harvests, archive refresh, the `house/` builder and the two corroboration units —
was deleted in #412 PR F.

```
  usa-wa-adapter-sos/                 — Layer 3: **everything WA Secretary of State**
    src/usa_wa_adapter_sos/
      ratelimit.py    — package util: `AsyncRateLimiter` (the #77 async min-interval gate) + `env_float`. Each source owns its own limiter *instance* + env knob (distinct upstream hosts) but shares this impl
      coverage.py     — **the two SOS feeds' declared coverage claims** (#180), and where `absent` earns the schema: filings `election_year` **verified** 2008–2018 *plus* **absent** 2020→open (the Power BI retirement — `electionDate=202011` → HTTP 500, probed live 2026-08-06 — which lived only as prose in ARCHITECTURE.md, so "does any source cover 2020?" was rediscovered by running a harvest into a 500); results `election_year` **verified** 2008→open, no gap. That asymmetry is why this package holds two sources, and it is now queryable rather than inferred from two docstrings
      provisioning.py — get-or-create the **two** SOS `Source` rows (+ seed each feed's coverage claims, #180): `get_or_create_source` (`usa_wa_sos`, filings) + `get_or_create_results_source` (`usa_wa_sos_results`, results); both archival retention. Called nightly by `usa_wa_pipeline.coverage_seed`; `raw_harvest` reads the two slugs from here
      parsing.py      — offline parse seam (#307): re-exports `parse_whofiled` + `parse_legislative_results` so `usa_wa_pipeline` staging depends on parsing without importing either `transport`
      raw_harvest.py  — **Phase A** (#304, `python -m usa_wa_adapter_sos.raw_harvest`, slug `sos-raw-harvest`, run by `pipeline-nightly.sh`): both SOS sources one run — WhoFiled filings + legislative results per decisive year, written as pristine wires to `raw/usa_wa_sos/` + `raw/usa_wa_sos_results/` under the archive's resource ids; per-cohort and per-source failures contained, an uncontained one raised as `JobFailure` with the per-source counters reached (#331). **Early capture (#135):** from the day after the next biennium's seating general it also fetches that year (`lookahead_election_year`; `today=` pins the date in tests), so election-night returns and then the certified export land as successive sha256s of one resource — raw only, since the House Position join seats a ballot solely through the sponsor roster of the biennium it seats. `ACCEPTED_OUTAGES`: below
      filings/        — **SOURCE 1**: votewa `ExportToExcel` candidate filings (`usa_wa_sos`, 2008–2018; SOS retired it to Power BI for 2020+). Unique value = candidacy metadata (`Email`/`FilingDate`/`IsWithdrawn`, #99). Drives no seat (results does) — kept per *yes-and*; staged as `stg_sos_filings`
        transport.py  — `SOSFilingsClient` (`eledataweb.votewa.gov/Candidates/ExportToExcel?electionDate=<YYYYMM>`) → `WireFetch` (#54) + `parse_whofiled` (BOM-tolerant); own limiter env `USA_WA_SOS_MIN_REQUEST_INTERVAL`; `general_election_date(year)`→`<year>11`
        resources.py  — `sos-whofiled:<YYYYMM>` prefix, builder and year parser (#412). Shared by `raw_harvest` and the pipeline's SOS staging
        normalize.py  — filings-CSV → `{LD: [HousePosition]}`: `house_position_qualifier`/`filing_ld`/`build_house_filings` (`HouseFiling` is a back-compat alias of the shared `HousePosition`). No caller outside its tests since #412 PR F deleted the filings cohort
      results/        — **SOURCE 2**: `results.vote.wa.gov` legislative election results (`usa_wa_sos_results`, 2008→present incl. the current cycle; **the seat's Position source since #101**). Unique value = ballot Position **+** vote counts, current-cycle coverage the filings export can't serve
        transport.py  — `SOSResultsClient`: `general_election_date(year)` (YYYYMMDD = first Tue after first Mon of Nov), **traverses `export.html`** to discover the Legislative CSV href (filenames vary — 2012 carries a certification timestamp), redirect-follows to the lowercase path → `WireFetch` (#54) + `parse_legislative_results`. `LegislativeExportNotFound` (a no-legislative-race year, 2021/2023) vs `httpx` errors are distinct failures; own limiter env `USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL`
        resources.py  — `sos-legresults:<YYYYMMDD>` prefix, builder and year parser (#412), shared the same way
        normalize.py  — **robust race-label parser** (the #101 audit): office+LD+position live in one `Race` string, labelled **three** ways (`State Representative Pos. N` ~99% / `Representative, Position N` at 2020 LD15 / bare `State Representative N` at 2014 LD30 — sometimes differing between one district's two seats). Rule: a `Race` naming a *Representative* (not *Senator*) → LD after `DISTRICT` + the **trailing** `1/2`; `WRITE-IN` dropped. `build_house_positions` → `{LD: [HousePosition]}`. An exact-string parser silently drops real seats — never do that (see ARCHITECTURE.md). Also **`parse_senate_race`/`build_senate_winners`** (#106 A′; Senate labels **audited clean 2008→2025** at #123 — one `Legislative District N - State Senator` shape, case-insensitive, zero variants) — the Senate half of the same wire the House parser drops: `{LD: SenateWinner}` (the top-`Votes` non-write-in candidacy per LD; a vote tie / unparseable multi-candidacy LD is omitted, never guessed). The Senate seat is unqualified, so this is **attestation** (an elected senator's ballot line) not structure. **`build_house_winners`** (#123) is the House sibling — the winning candidacy **per (LD, position) race** (the odd-year merge filter); it + `build_senate_winners` share the never-guess `_top_vote_winner`. Consumers: the pipeline's `conformed.house` (positions + odd-year winners) and `conformed.winners` (the `seat_winners` model behind the odd-year-winners-seated gate that replaced the two corroboration units)
```

## Accepted outages (#333)

`raw_harvest.ACCEPTED_OUTAGES` names sources whose **total** failure is known,
tracked and not worth an alert. A named source landing nothing exits 0 and logs
`sos_raw_harvest_accepted_outage` with the issue and the date first observed;
the counters still carry `errors` and `accepted_outages`, so the outage stays
legible in the journal — it is unalarmed, not unreported.

The reason it is safe to add at all is the other half: an accepted source that
**recovers** exits 4 and names itself in `stale_acceptances`. Without that the
exemption outlives the outage and the source can go dark with nothing left to
notice. Acceptances expire by the source recovering, never by the calendar.

That signal fires **once**, so it carries the whole cleanup rather than just its
own removal: `AcceptedOutage.follow_up` is required, not defaulted, and lists
every chore the recovery unblocks. For #333 that is two — drop the entry, and
ratchet `dbt/tests/stg_sos_filings_key.sql` from `severity: warn` back to error
(#330), since that key is a contract stated before any real WhoFiled wire ever
landed and a first real wire is what finally verifies it. An outage's deferred
chores are exactly the ones nobody remembers when the outage ends.

Currently accepted: **filings** (#333, observed 2026-09-03) — votewa.gov's
WhoFiled `ExportToExcel` returns HTTP 500 for every election date. Nothing
downstream reads it (`stg_sos_filings` is published but feeds no span, citation
or conformed product), so the outage costs coverage this deployment does not
yet use. It had mailed the operator on five consecutive nightly runs.

The granularity is the source, not the HTTP status: the counters record that a
source landed nothing and do not carry the reason. So while an acceptance
stands, a *different* cause of total failure in that same source is also
accepted — stated here rather than hidden, and bounded by the staleness rule.
