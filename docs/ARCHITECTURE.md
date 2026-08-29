# Architecture — sourcing vs. application, and multi-source target packages

This is the reusable shape the clearinghouse follows for ingesting external data. It exists so a
new data source drops in without disturbing the canonical facts built on top of it, and so one
external *target* that publishes several data feeds stays one coherent package. Read it before
adding an adapter, a data source, or a span/seat builder.

The concrete design record is [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](specs/2026-05-25-usa-wa-mvp-design.md);
this document is the pattern that record instantiates.

## The pattern in one paragraph

The summary `AGENTS.md` used to carry inline, moved here in full (#263):

**Read [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) before adding an adapter, a data source, or a span/seat builder.** It is the reusable Layer-3 pattern: one adapter package per *jurisdiction+target* bundling every source that target publishes; each **source** a self-contained archive (own `Source`/`source_slug`/archive-key/transport/adapter/normalize/cohort/harvest); the **application** (spans/seats) source-agnostic, consuming a cohort interface — so a fact can draw on a new source without a rewrite (the `usa-wa-adapter-sos` filings + results sources are the worked example). Audit a source's coverage before building on it; never key a parser on an exact upstream string. Inside a package (#183): single-source target ⇒ flat top level (no `pdc/` inside `usa_wa_adapter_pdc`); subpackage only on an axis that varies (WSL splits on its four archives); **`harvest.py` = Phase A, `build.py` = Phase B**, plus `projector`/`emit`/`migrate_*`.

## The four layers (recap)

| Layer | Package(s) | Owns |
|---|---|---|
| 1 — framework | `clearinghouse-core` | jurisdiction-agnostic primitives: `BaseAdapter`, `AdapterRunner`, provenance (`Source`/`FetchEvent`/`RawPayload`/`Citation`), integrity sweep |
| 2 — domain | `clearinghouse-domain-legislative` | the legislative model: `Person`/`Organization`/`Role`/`Assignment`; the **biennium term calendar** (`terms`), the **span engine** (`tenure_spans`/`span_emit`/`operator_overlay`) and the **`CohortProvider` Protocols** (`cohorts`) |
| 2b — vocabulary | `usa-wa-common` | what is true about *Washington's* legislature rather than about any publisher of data on it: the election calendar, seat/position keying, name folding, party canonicalization, the ballot interfaces. **Source-free** |
| 3 — adapters | `usa-wa-adapter-*` | **per jurisdiction+target**: turn a target's wire into canonical rows. **Sourcing only** |
| 3b — facts | `usa-wa-facts-*` | **applications**: compose cohort providers across adapters into a canonical fact |
| 4 — deployment | `usa-wa-api`, `usa-wa-sync-powermap` | serve + sync to Power Map |

**The layering is a contract, not a description** (#189, AR-14). It is checked by
`import-linter` (`uv run lint-imports`, in the pre-commit gate beside ruff; contracts in the
root `pyproject.toml`, proved to fire by `scripts/tests/test_import_contracts.py`):

- `usa_wa_adapter_* ↛ usa_wa_adapter_*` — an adapter never imports a peer
- `usa_wa_sync_powermap`, `usa_wa_api`, `usa_wa_facts_* ↛ usa_wa_adapter_*.transport`
- `usa_wa_common ↛` any adapter, fact or deployment package
- the layer order above, with no back-edges

Layers **2b** and **3b** were added by #189. Before them there was no home for composition, so
it happened inside whichever target-keyed adapter package first needed it: `usa-wa-adapter-sos`
imported 21 symbols from two peer adapters, `usa-wa-adapter-legislature` became a shared kernel
by accident (the calendar, the span engine and name matching all lived inside a SOAP adapter),
and the PM sync sidecar made live SOAP calls to the Legislature from five modules. The rule
that prevents the recurrence is the one worth remembering: **when a second target needs
something, that is the signal it belongs in 2b or 3b — not that the first target's package
should export it.**

This document refines **Layer 3**: how one adapter package is organized internally.
Layer 3b's shape is in [MODULES-FACTS-SEATS.md](MODULES-FACTS-SEATS.md), Layer 2b's in
[MODULES-COMMON.md](MODULES-COMMON.md).

## Principle: sourcing is separate from application

Two distinct jobs hide inside "ingest a data source," and conflating them is the mistake this
pattern prevents:

- **Sourcing** — *faithfully archive what a target publishes.* Fetch the wire, hash + store it
  (`RawPayload`, #54), and re-parse it offline (#56). A source is judged only on fidelity and
  coverage, never on what a downstream fact needs. It is inherently *append-only history*.
- **Application** — *derive a canonical fact from one or more archives.* "Who holds House seat
  LD-5 Position 1 across 2013–2025" is an application question answered by merging observations
  from whatever archives carry the evidence.

Keeping them separate means: a source can be added, re-audited, or found wanting **without
touching** the facts; and a fact can draw on a **new** source (or several) without a rewrite. The
2026-07 votewa outage is the cautionary tale — an application (House Position) welded to a single
source (votewa filings) broke wholesale when that source went dark for 2020+. The fix was a second
source, not a rewrite of the fact.

## One package per *target*, many sources inside

An adapter package is keyed on **jurisdiction + target**, not on a single feed. `usa-wa-adapter-sos`
is "everything the WA Secretary of State publishes," and it bundles every SOS data source. Each
**source** is a self-contained archive; the **application** modules are source-agnostic.

```
usa_wa_adapter_<target>/
  <source_a>/           # SOURCE — a self-contained archive of one feed
    transport.py        #   client: fetch the wire (+ offline re-parser), courtesy rate-limit (#77)
    adapter.py          #   BaseAdapter: discover / fetch_one / (archive-only or normalize)
    normalize.py        #   pure wire -> typed rows
    cohort.py           #   archive-first provider: {key: [rows]} re-parsed from RawPayload (#56/#82)
    harvest.py          #   Phase A CLI: sweep the range, archive each wire, resilient (see note)
    archive_refresh.py  #   Phase A daily: re-archive THIS biennium's cohorts, forced (#201).
                        #   Sourcing is the source's job; the fact only rebuilds from it
  <source_b>/           # another feed from the same target — its own everything
    ...
  provisioning.py       # get-or-create every Source row this package owns
  <application>/        # e.g. house/ — canonical facts, SOURCE-AGNOSTIC
    projector.py        #   pure: cohort rows -> Observations
    build.py            #   Phase B: read a cohort provider -> merged spans -> emit
    emit.py migrate.py refresh.py
```

### When a target publishes only one source (#183)

`usa_wa_adapter_pdc` and `usa_wa_adapter_legislature` each own exactly **one** `Source` row, so
there is no `<source_a>/` vs `<source_b>/` to divide: **the package top level *is* the source**, and
`transport.py` / `adapter.py` / `normalize/` / `cohort.py` / `harvest.py` sit at it. Adding a `pdc/`
or `wsl/` directory under `usa_wa_adapter_pdc` / `usa_wa_adapter_legislature` would restate the
package name one level down and discriminate nothing — do not.

What a single-source package *can* still have is several **archives** under that one `Source`: one
feed, several resource-id schemes. WSL is the case — four SOAP services, four archive keys
(`sponsors:`, `committees-roster:`, `committee-members-hist:`, `committee-meetings:`) — and *that*
is what its subpackages divide on (plus `operators/`, the wire-free `usa_wa_operator` attestation
Source). One archive per directory, the module names below inside each. The rule generalizes: split
on the axis that actually varies, and if none does, stay flat.

The vocabulary is load-bearing beyond directory layout. `harvest.py` means **Phase A** (archive the
wire) and `build.py` means **Phase B** (spans from that archive) — before #183 the WSL package
spelled those two `harvest.py` and `harvest_sponsor_spans.py`, one plural apart, and the
same for committee membership. A module whose name does not say which phase, layer or role it holds
is the discoverability tax finding 13 measured; prefer the names in the tree above to a new coinage.

**Function names no longer stutter (#183, swept at #179b).** The entry point in
`sponsors/harvest.py` is `harvest()`, not `harvest_sponsors()`; `committees/harvest.py`,
`membership/harvest.py`, `meetings/harvest.py` and `usa_wa_adapter_pdc/harvest.py` are the
same. The rule is *drop the noun the module path already carries*: `sponsors/build.py` has
`build_spans()`, `sponsors/migrate_spans.py` has `migrate_spans()`,
`committees/ingest_seed.py` has `ingest_seed()`, and `committees/probe_extent.py` has
`probe_floor()` beside `probe_extent()`.

#183 deferred this because renaming a function forces assertion edits, and a move whose
tests had to change is a move that changed behaviour (CR #196 finding 46). #179b was
already rewriting the same entry points onto the job harness, so the edits landed once.

Two names deliberately keep a qualifier, and the test is **call-site ambiguity, not the
module path**:

- `membership/build.py` keeps `build_committee_member_spans()` — it is not in #183's list,
  and `refresh.py` imports it beside the sponsor builder.
- Because of that neighbour, `refresh.py` reaches the sponsor builder **module-qualified**
  (`sponsor_build.build_spans(...)`) rather than importing a bare `build_spans` that would
  sit one line from `build_committee_member_spans` telling the reader nothing about which
  family it belongs to. Qualify at the call site; do not put the noun back in the name.

`probe_extent()` also keeps its module's noun: the module exports two probes, only one can
be the bare `probe()`, and `probe_extent()`/`probe_floor()` says more than `probe()`/
`probe_floor()` would.

### What makes a source "self-contained"

Each source owns an independent provenance chain, so it can be harvested, re-audited, integrity-
swept, and reasoned about in isolation:

- **Its own `Source` row / `source_slug`** — one per feed (`usa_wa_sos` filings vs
  `usa_wa_sos_results` results), never shared. A `RawPayload` traces unambiguously to one feed.
- **Its own archive key** — the `FetchEvent.resource_id` scheme (`sos-whofiled:<YYYYMM>` vs
  `sos-legresults:<YYYYMMDD>`). Keys never collide across sources.
- **Its own transport + adapter + normalize** — the wire contract lives with the source that
  speaks it. A parser quirk in one feed can't leak into another.
- **Archive-first re-parse** — the `cohort` provider re-derives rows *offline* from `RawPayload`
  (a live fetch is a fallback for an un-archived key only). Joining `RawPayload` is load-bearing:
  a forced daily re-pull re-records a payload-less `FetchEvent`, so "latest" means *latest
  payload-bearing* event (#82).
- **Resilient harvest** — a Phase A sweep skips-and-logs a bad year in its own SAVEPOINT and
  commits the years it reached; one bad year must not roll back the sweep, and a *whole-source*
  outage — **every** year skipped, not merely "nothing fetched" — raises a distinct signal rather
  than reading as "nothing to do", and exits non-zero (`EXIT_DEGRADED`) so `OnFailure=` fires.
  The count that matters is skipped-vs-total: an archive-only harvest returns False on a cache
  hit, so "nothing fetched" is the *normal* re-run, and keying the alarm on it fires loudest
  exactly when nothing is wrong.
  This holds for a **closed** range too (#169): abort-and-resume looks free only inside the cache
  TTL — past it, a re-run re-pulls every already-fetched year against a low-QPS government host,
  which is exactly the traffic the courtesy limiter exists to avoid. What *does* vary with the
  range is the tally: a source with per-year discovery distinguishes an expected absence from a
  failure (`results`: `cohorts_absent` vs `cohorts_skipped`), a source without it needs one tally
  (`filings`).

### What makes the application "source-agnostic"

The `build.py`/`refresh.py` layer depends on a **cohort interface** (`{election_year: {LD:
[position]}}`, a per-key citation-target accessor), not on a concrete source. Since #189 that
interface is a real `Protocol`, not a convention: `usa_wa_common.ballot.HousePositionCohortProvider`
for this fact, the generic `clearinghouse_domain_legislative.cohorts.*` for the rest, with
conformance pinned by `scripts/tests/test_cohort_seam.py`. It had to be made real because the
claim below was **not true** when #189 checked it: `SosResultsCohortProvider` exposed
`house_positions` while `SosFilingCohortProvider` exposed `house_filings`, over an identical row
type — so the two archives this section presents as interchangeable were not substitutable under
any name, and nothing tested that they agreed. Swapping which
archive feeds a fact is a one-line provider change; adding a *second* archive to corroborate it is
additive. The projector (`projector.py`) is pure — no DB, no source knowledge — so it is trivially
testable and reused across sources that yield the same row shape.

## Worked example — WA SOS House Position

The House Position seat (`state_representative`, `Position 1/2`) is an **application** with two SOS
**sources** behind it:

| | `filings/` (source `usa_wa_sos`) | `results/` (source `usa_wa_sos_results`) |
|---|---|---|
| feed | votewa `ExportToExcel` candidate filings | `results.vote.wa.gov` legislative election results |
| coverage | 2008–2018 (retired to Power BI for 2020+) | 2008–present (incl. current cycle) |
| unique value | candidacy metadata (filing date, withdrawal, contact — #99) | ballot Position **+** vote counts, current-cycle |
| archive key | `sos-whofiled:<YYYYMM>` | `sos-legresults:<YYYYMMDD>` |

`house/build.py` reads a **cohort provider** for the `{LD: [position]}` lookup and merges it with
the WSL sponsor roster (who sits) into `usa_wa_legislature`-sourced seat spans (symmetric with the
Senate seat, #75). Which SOS archive supplies the position is the provider's concern, not the
builder's — filings retain their standalone value, results serve the live seat, and a future feed
joins the same way. This is *yes-and*, never *either-or*: each source is kept for what only it
covers.

## Audit before you build

A source's coverage is a claim to be **verified**, not assumed. Before an application is built on a
feed, audit it end-to-end across its full intended range and surface the gaps: availability per
period, filename/URL stability, schema drift, and label/value inconsistencies.

**The audit's output is data, not a comment (#180).** Each adapter package declares its sources'
coverage in `coverage.py` as `CoverageClaim`s — `(dimension, range_start, range_end, status,
audited_at, notes)` — and `provisioning.py` seeds them into `clearinghouse_core.source_coverage`
alongside the `Source` row. The claims are the single source of truth: a harvest's floor/ceiling is
derived from one in pure Python (so a CLI default costs no query), and the table is the same object
projected for querying. `status` is `verified` (probed on `audited_at`) | `assumed` (believed, never
checked — say so) | **`absent`** (the feed does *not* serve this range, and that is a fact rather
than the silence a missing row is indistinguishable from — the votewa 2020+ retirement is the
worked example). `dimension` keys the axis, not the source, because one feed can serve several with
different bounds (WSL: `sponsor_roster` from 1991-92, `committee_membership` only from 1999-00).

The votewa episode
produced two rules now baked into this pattern — the resilient harvest above, and: **never key a
parser on an exact upstream string.** WA SOS labels the same office three ways
(`State Representative Pos. 1`, `Representative, Position 1`, a bare `State Representative 2`),
sometimes differing between the two seats of one district in one file; a tolerant parser (match the
office, take the trailing position digit) is mandatory, and an exact-match parser silently drops
real seats.

## Checklist — adding a source to an existing target package

1. New `<source>/` subpackage: `transport` (+ offline re-parser, courtesy limiter), `adapter`
   (`BaseAdapter`; archive-only unless the fact is single-cohort-derivable), `normalize` (pure),
   `cohort` (archive-first), `harvest` (per-year SAVEPOINT + skip-and-log + a total-outage signal,
   whether or not the range is closed; see *Resilient harvest* above).
2. A new `Source`/`source_slug` in `provisioning.py`; a non-colliding archive-key scheme.
3. **Audit the feed across its range first, and record the result as coverage rows** — a
   `CoverageClaim` per dimension in the package's `coverage.py`, seeded by `provisioning.py`.
   *Coverage rows must exist before an application builds on the feed.* An unprobed bound is
   `assumed`, not `verified`; a known gap is an `absent` claim, not an omission. Encode every
   gap/variant as a test too, and derive the harvest's floor/ceiling from the claim rather than
   restating it as a constant.
4. Point (or add) the application's cohort provider — do **not** widen an application module to
   know about the source.
5. Wire the Phase A harvest + any daily refresh into `deploy/`; document the CLI in
   [`docs/COMMANDS.md`](COMMANDS.md) and the module in [`AGENTS.md`](../AGENTS.md).
