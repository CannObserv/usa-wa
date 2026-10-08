# Architecture — sourcing vs. application, and multi-source target packages

This is the reusable shape the clearinghouse follows for ingesting external data. It exists so a
new data source drops in without disturbing the published facts built on top of it, and so one
external *target* that publishes several data feeds stays one coherent package. Read it before
adding an adapter, a data source, or a span/seat builder.

The concrete design records are [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](specs/2026-05-25-usa-wa-mvp-design.md)
(the layers) and [`docs/specs/2026-09-02-dataset-publication-replatform-design.md`](specs/2026-09-02-dataset-publication-replatform-design.md)
(the #302 pipeline); this document is the pattern they instantiate. Pipeline commands, models and
gates: [`PIPELINE.md`](PIPELINE.md).

## The layers (recap)

| Layer | Package(s) | Owns |
|---|---|---|
| 1 — framework | `clearinghouse-core` | jurisdiction-agnostic primitives: the #304 raw store (`rawstore`, `raw_integrity`), the job harness, the identity registry machinery, `Source` + `SourceCoverage` |
| 2 — domain | `clearinghouse-domain-legislative` | the **biennium term calendar** (`terms`), the **span engine** (`tenure_spans`, `operator_overlay`, `seat_clipping`, `span_kinds`) and the operator attestation models |
| 2b — vocabulary | `usa-wa-common` | what is true about *Washington's* legislature rather than about any publisher of data on it: the election calendar, seat/position keying, name folding, party canonicalization, the ballot row types. **Source-free** |
| 3 — adapters | `usa-wa-adapter-*` | **per jurisdiction+target**: fetch a target's wire into the raw store, and parse it back offline. **Sourcing only** |
| 3b — facts | `usa-wa-facts-*` | **applications**: pure seat-fact logic that composes evidence across targets |
| 3c — pipeline | `usa-wa-pipeline` | the #302 dbt-duckdb staging / matching / conformed models, the registrar and the publisher |
| 4 — deployment | `usa-wa-api` | serve the published datasets |

**The layering is a contract, not a description** (#189, AR-14). It is checked by
`import-linter` (`uv run lint-imports`, in the pre-commit gate beside ruff; contracts in the
root `pyproject.toml`, proved to fire by `scripts/tests/test_import_contracts.py`):

- `usa_wa_adapter_* ↛ usa_wa_adapter_*` — an adapter never imports a peer
- `usa_wa_api`, `usa_wa_facts_*`, `usa_wa_pipeline ↛ usa_wa_adapter_*.transport`
- `usa_wa_common ↛` any adapter, fact or deployment package
- the layer order above, with no back-edges
- *The pipeline, the API and the raw harvests never import the retired Postgres tier* — by
  any chain. Since #412 PR F most of its entries are tombstones that refuse a deleted module's
  name if it ever returns; the live ones are the two operator stores, which the pipeline reads
  through SQL and never imports

Layers **2b** and **3b** were added by #189. Before them there was no home for composition, so
it happened inside whichever target-keyed adapter package first needed it: `usa-wa-adapter-sos`
imported 21 symbols from two peer adapters, and `usa-wa-adapter-legislature` became a shared
kernel by accident (the calendar, the span engine and name matching all lived inside a SOAP
adapter). The rule that prevents the recurrence is the one worth remembering: **when a second
target needs something, that is the signal it belongs in 2b or 3b — not that the first target's
package should export it.**

This document refines **Layer 3**: how one adapter package is organized internally, and how the
pipeline consumes it. Layer 3b's shape is in [MODULES-FACTS-SEATS.md](MODULES-FACTS-SEATS.md),
Layer 2b's in [MODULES-COMMON.md](MODULES-COMMON.md).

## Principle: sourcing is separate from application

Two distinct jobs hide inside "ingest a data source," and conflating them is the mistake this
pattern prevents:

- **Sourcing** — *faithfully archive what a target publishes.* Fetch the wire, store it under its
  sha256 in the raw store (#304), and re-parse it offline. A source is judged only on fidelity and
  coverage, never on what a downstream fact needs. It is inherently *append-only history*.
- **Application** — *derive a published fact from one or more archives.* "Who holds House seat
  LD-5 Position 1 across 2013–2025" is an application question answered by merging observations
  from whatever archives carry the evidence.

Keeping them separate means: a source can be added, re-audited, or found wanting **without
touching** the facts; and a fact can draw on a **new** source (or several) without a rewrite. The
2026-07 votewa outage is the cautionary tale — an application (House Position) welded to a single
source (votewa filings) broke wholesale when that source went dark for 2020+. The fix was a second
source, not a rewrite of the fact.

## Two phases, two packages

The split is physical: each job runs as a different process, owned by a different package.

- **Phase A — `raw_harvest`, in the adapter.** One module per target package, covering every
  source in it, plus one for the roster PDF (`usa_wa_adapter_legislature.raw_harvest`, `…pdc.raw_harvest`, `…sos.raw_harvest`, and the
  on-demand `…legislature.roster_pdf.raw_harvest`). Each fetches its wires through the source's
  transport and records them into `raw/<source-slug>/` — the nightly three through
  `clearinghouse_core.rawstore.record_fetch`, the roster through its own stamp-checked loop: content-addressed objects, one manifest per run, `latest.json` naming the
  newest ok fetch per resource id. No database.
- **Phase B — the pipeline's dbt build.** Staging models re-parse the raw store through the
  adapter's **pure** parsers and resource ids; matching proposes cross-source links; the registrar
  binds identity; conformed models build the products (spans through the Layer-2 engine); the
  publisher writes versioned datasets, which the API loads into Postgres `serving`.

`scripts/pipeline-nightly.sh` runs both in order: the three nightly harvests, `dbt build`,
`build_warnings`, registrar, publish, serving load, `coverage_seed`, probes. A harvest failure is contained there —
the raw store keeps the last good wires — while a build failure aborts before anything publishes.

**One input has no wire.** Operator attestations (mid-biennium successions, committee lineage)
are human decisions: `operators.cli` and `committees.succession_cli` write them to
`registry.operator_events` / `registry.committee_succession_events` and their serialized body to
the raw store under `usa_wa_operator`. The pipeline reads the tables through
`usa_wa_pipeline.operator_read`, the same way it reads the registry crosswalk — a curated input,
so the transform stays stateless while the judgment stays durable.

## One package per *target*, many sources inside

An adapter package is keyed on **jurisdiction + target**, not on a single feed. `usa-wa-adapter-sos`
is "everything the WA Secretary of State publishes," and it bundles every SOS data source. Each
**source** is a self-contained archive; the **application** code that reads it lives elsewhere.

```
usa_wa_adapter_<target>/
  <source_a>/           # SOURCE — a self-contained archive of one feed
    transport.py        #   client: fetch the wire + its offline parse function, courtesy rate-limit (#77)
    resources.py        #   pure: the archive key — resource-id prefix + builder
    normalize.py        #   pure: parsed wire rows -> typed rows (tolerant; see below)
  <source_b>/           # another feed from the same target — its own everything
    ...
  raw_harvest.py        # Phase A: every source's wires into the raw store
  parsing.py            # pure facade over each transport's offline parser — what staging imports
  coverage.py           # the audited coverage claims, as data (#180)
  provisioning.py       # get-or-create every Source row this package owns, reconciling its claims

usa_wa_pipeline/
  staging/<target>.py   # pure row builders: newest wire per resource -> parse -> rows
  conformed/*.py        # pure application logic: spans, entities, roles, citations
dbt/models/{staging,conformed}/*.py   # thin binders over the two above
```

**Single-source packages stay flat (#183).** `usa_wa_adapter_pdc` owns one `Source`, so there is
no `<source_a>/` vs `<source_b>/` to divide: the package top level *is* the source. Adding a `pdc/`
directory would restate the package name one level down and discriminate nothing — do not.
`usa_wa_adapter_legislature` sits between: its SOAP feed is one `Source` with several **archives**
(`sponsors:`, `committees-roster:`, `committee-members-hist:`, `committee-meetings:`), and *that* is
what its subpackages divide on, beside `roster_pdf/` (the roster PDF, a second `Source` with its own
transport, resources and raw harvest) and `operators/` (the wire-free `usa_wa_operator` source). The
rule generalizes: split on the axis that actually varies, and if none does, stay flat.

The vocabulary is load-bearing beyond directory layout: a module whose name does not say which
phase, layer or role it holds is the discoverability tax #183 measured. Prefer the names in the
tree above to a new coinage, and drop the noun the module path already carries.

### What makes a source "self-contained"

Each source owns an independent provenance chain, so it can be harvested, re-audited, integrity-
swept, and reasoned about in isolation:

- **Its own `Source` slug** — one per feed (`usa_wa_sos` filings vs `usa_wa_sos_results`
  results), never shared, and so its own slice of the raw store. An object traces unambiguously
  to one feed. Declared **once**, in the package's `coverage.py`, and imported by the raw harvest,
  the staging model and every registry-key reader: the slug is also the key namespace
  (`usa_wa_legislature:<member_id>`), so a retyped copy that misses a rename matches nothing,
  silently (#245; `scripts/tests/test_source_slug_literals.py` guards every adapter's slugs, #482).
  A registry namespace that is *not* the slug (PDC's `wa_pdc`) is declared beside the slug in
  `coverage.py` and guarded the same way.
- **Its own archive key** — the resource-id scheme in a pure `resources.py`
  (`sos-whofiled:<YYYYMMDD>` vs `sos-legresults:<YYYYMMDD>`), imported by both the raw harvest and
  the staging model. Keys never collide across sources, and a rename breaks the import rather than
  silently emptying a staging model.
- **Its own transport + parser + normalize** — the wire contract lives with the source that
  speaks it, and a parser quirk in one feed can't leak into another. The pipeline reaches the
  parser through `parsing.py`, never the transport (the import contract).
- **Archive-first re-parse** — staging reads the newest ok wire per resource
  (`staging.common.latest_wires`) and never fetches. Every staging row carries its
  `(source, resource_id)`, so `entity → staging row → resource → sha256` closes in the published
  `citations` and `stg_raw_fetches` datasets without a lookup nobody maintains.
- **Resilient harvest** — `record_fetch` contains a failed resource as an `err` manifest entry and
  the run manifest closes regardless; one bad resource must not cost the run. A **whole-source**
  outage — the source landed nothing, or every attempted fetch failed — degrades the job
  (`EXIT_DEGRADED`, exit 4) so `OnFailure=` fires. The count that matters is landed-vs-attempted:
  a byte-identical re-fetch still counts as fetched (`unchanged`), so a quiet day is not an alarm,
  and a TTL-masked outage still is. A *known* outage is accepted in code, with a reason and an
  issue, and the acceptance degrades the run the night the source recovers
  (`usa_wa_adapter_sos.raw_harvest.ACCEPTED_OUTAGES`, #333).

### What makes the application "source-agnostic"

The application depends on **staging rows, not on a wire**. A conformed model reads staging
tables whose columns are declared (`*_SCHEMA`, #361), and hands them to pure functions — the
Layer-3b House logic, the Layer-2 span engine — that know nothing about where the rows came from.
Swapping which archive feeds a fact is a change to the binder's inputs; adding a *second* archive
to corroborate it is additive. Because the logic is pure — no DB, no source knowledge — it is
pytest-covered in isolation and reused by the probes that recompute the same join
(`registry_coverage`). The Postgres tier expressed this seam as cohort-provider Protocols (#189);
they retired with it in #412.

One rule binds every application input: **an input whose absence silently deletes facts must
refuse, not return empty** (CR 57). `chamber-house` is ~4% of `assignments`, inside the publish
gate's 10% shrink threshold, so `build_house_spans` raises on an empty SOS archive under a live sponsor
corpus rather than publishing no House seats.

## Worked example — WA SOS House Position

The House Position seat (`chamber-house`, `Position 1/2`) is an **application** with two SOS
**sources** behind it:

| | `filings/` (source `usa_wa_sos`) | `results/` (source `usa_wa_sos_results`) |
|---|---|---|
| feed | votewa `ExportToExcel` candidate filings | `results.vote.wa.gov` legislative election results |
| coverage | 2008–2018 (retired to Power BI for 2020+) | 2008–present (incl. current cycle) |
| unique value | candidacy metadata (filing date, withdrawal, contact — #99) | ballot Position **+** vote counts, current-cycle |
| archive key | `sos-whofiled:<YYYYMMDD>` | `sos-legresults:<YYYYMMDD>` |
| today | staged and published (`stg_sos_filings`); feeds no span; an accepted outage (#333) | feeds the seat |

Traced end to end, through the results source:

1. **Phase A.** `usa_wa_adapter_sos.raw_harvest` fetches, for each election year seating the
   biennium (plus the next seating election once held, #135), the results export through
   `SOSResultsClient`, and records it under `legresults_resource_id(year)` in
   `raw/usa_wa_sos_results/`.
2. **Staging.** `stg_sos_results` binds `usa_wa_pipeline.staging.sos.result_rows`: the newest wire
   per `sos-legresults:` resource, parsed by `usa_wa_adapter_sos.parsing.parse_legislative_results`,
   one row per `(election_date, race, candidate)` with the verbatim CSV columns and
   `(source, resource_id)`. Staging holds no policy.
3. **Conformed.** `assignments` runs `conformed.spans.build_families`, whose House family is
   `conformed.house.build_house_spans`. WSL owns *who sits* (`stg_wsl_sponsors`: LD + party), SOS
   owns *which position*: `results.normalize.build_house_positions` / `build_house_winners` read
   the ballot, `usa_wa_facts_seats.house.positions.merge_positions` applies the #123 map,
   `facts_seats.house.roster` the #105 mover exclusion, `facts_seats.house.backchain` the #118
   carry-back and #103 elimination, and the domain's `operator_overlay` + `build_tenure_spans`
   produce one `chamber-house` span per tenure. The crosswalk join turns each into an
   `assignments` row under role key `seat:house:ld-N:position-P`.
4. **Gates and publish.** `seat_winners` re-reads the same ballot so
   `assignments_odd_year_winners_seated` can check every odd-year winner is seated. The registrar
   binds any new identity, `publish` writes `assignments`, `roles` and `citations`, and the
   serving load hands them to the API.

Which SOS archive supplies the position is the binder's concern, not the House logic's — filings
retain their standalone value, results serve the live seat, and a future feed joins the same way.
This is *yes-and*, never *either-or*: each source is kept for what only it covers.

## Audit before you build

A source's coverage is a claim to be **verified**, not assumed. Before an application is built on a
feed, audit it end-to-end across its full intended range and surface the gaps: availability per
period, filename/URL stability, schema drift, and label/value inconsistencies.

**The audit's output is data, not a comment (#180).** Each adapter package declares its sources'
coverage in `coverage.py` as `CoverageClaim`s — `(dimension, range_start, range_end, status,
audited_at, notes)` — and `provisioning.py` reconciles them into `clearinghouse_core.source_coverage`
alongside the `Source` row; the nightly's `coverage_seed` makes that call, so a re-audited claim
reaches `/api/v1/sources/{slug}/coverage` by the next morning. The claims are the single source of
truth: a probe's default floor reads one (`sponsors.probe_identity`), and the staging
coverage-floor tests (`dbt/tests/*_coverage_floor.sql`) fail a build whose archive no longer
reaches the claimed floor. `status` is `verified` (probed on `audited_at`) | `assumed` (believed,
never checked — say so) | **`absent`** (the feed does *not* serve this range, and that is a fact
rather than the silence a missing row is indistinguishable from — the votewa 2020+ retirement is
the worked example). `dimension` keys the axis, not the source, because one feed can serve several
with different bounds (WSL: `sponsor_roster` from 1991-92, `committee_membership` only from
1999-00).

The votewa episode produced two rules now baked into this pattern — the resilient harvest above,
and: **never key a parser on an exact upstream string.** WA SOS labels the same office three ways
(`State Representative Pos. 1`, `Representative, Position 1`, a bare `State Representative 2`),
sometimes differing between the two seats of one district in one file; a tolerant parser (match the
office, take the trailing position digit — `results.normalize.parse_house_race`) is mandatory, and
an exact-match parser silently drops real seats.

## Publishing bytes: one writer, landed atomically (#357)

Two rules for any code that writes a **published dataset's bytes**, both learned
the same way — by writing a second producer and watching it reinvent the gaps.

**One writer per dataset.** `publish.py` is it. A second producer of the same
rows is not a shortcut; it is a promise that two independent code paths will
serialize identically forever. They will not. The PM crosswalk shipped through
both the publisher and a hand-rolled export, and they disagreed on line endings
and row order — identical content, two sha256 values, and a consumer with no way
to tell a serialisation difference from corruption (#354). If a dataset must
also exist somewhere else, derive that copy from the published bytes or make the
second writer conform to [the declared dialect](PIPELINE-PUBLICATION.md#the-serialisation-is-part-of-the-contract-357)
and prove it with a test that runs the real publisher — never one that restates
the publisher's options and asserts they match.

**Land it atomically.** Write to a temp path and `os.replace`; never stream into
the file a reader may be holding. `publish.py` has done tmp+rename since #311 so
a crash leaves unlisted orphans rather than a listed partial. The crosswalk
export was written without it and streamed rows directly, so a rejected row left
a truncated CSV beside the *previous* run's manifest — again a hash mismatch
indistinguishable from tampering. An artifact and its integrity metadata must
never be observable in disagreement, including mid-write. The raw store follows
the same rule: objects, then the run manifest, each by tmp+replace.

## Checklist — adding a source to an existing target package

1. New `<source>/` subpackage: `transport` (+ its offline parse function, courtesy limiter),
   `resources` (pure resource-id prefix + builder), `normalize` (pure, tolerant); re-export the
   parse function from the package's `parsing.py`.
2. A new `Source` slug in `provisioning.py`, with a non-colliding archive-key scheme; add the
   provisioner to `usa_wa_pipeline.coverage_seed.PROVISIONERS`.
3. **Audit the feed across its range first, and record the result as coverage claims** — a
   `CoverageClaim` per dimension in the package's `coverage.py`. *Claims must exist before an
   application builds on the feed.* An unprobed bound is `assumed`, not `verified`; a known gap is
   an `absent` claim, not an omission. Encode every gap/variant as a test too.
4. Phase A: harvest it from the package's `raw_harvest` through `record_fetch` (per-resource
   containment, degraded on a whole-source outage), add the module to the nightly's harvest loop
   in `scripts/pipeline-nightly.sh` (or run it on demand, as the roster does), and add it to the
   retired-tier contract's `source_modules`.
5. Phase B: a pure row builder in `usa_wa_pipeline.staging` with a declared `*_SCHEMA` ending in
   `PROVENANCE_SCHEMA`, a thin `stg_<source>` binder, its `schema.yml` entry and key test; then
   hand its rows to the application — do **not** widen application logic to know about the
   source. Publish the staging table only by deciding to: a `PublishedDataset` entry in
   `publish.PUBLISHED_DATASETS`.
6. Document the CLI in [`docs/COMMANDS.md`](COMMANDS.md) and the modules in the package's
   `docs/MODULES-*.md`.
