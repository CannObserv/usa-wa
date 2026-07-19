# Architecture — sourcing vs. application, and multi-source target packages

This is the reusable shape the clearinghouse follows for ingesting external data. It exists so a
new data source drops in without disturbing the canonical facts built on top of it, and so one
external *target* that publishes several data feeds stays one coherent package. Read it before
adding an adapter, a data source, or a span/seat builder.

The concrete design record is [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](specs/2026-05-25-usa-wa-mvp-design.md);
this document is the pattern that record instantiates.

## The four layers (recap)

| Layer | Package(s) | Owns |
|---|---|---|
| 1 — framework | `clearinghouse-core` | jurisdiction-agnostic primitives: `BaseAdapter`, `AdapterRunner`, provenance (`Source`/`FetchEvent`/`RawPayload`/`Citation`), integrity sweep |
| 2 — domain | `clearinghouse-domain-legislative` | the legislative model: `Person`/`Organization`/`Role`/`Assignment`, tenure spans |
| 3 — adapters | `usa-wa-adapter-*` | **per jurisdiction+target**: turn a target's wire into canonical rows |
| 4 — deployment | `usa-wa-api`, `usa-wa-sync-powermap` | serve + sync to Power Map |

This document refines **Layer 3**: how one adapter package is organized internally.

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
  <source_b>/           # another feed from the same target — its own everything
    ...
  provisioning.py       # get-or-create every Source row this package owns
  <application>/        # e.g. house/ — canonical facts, SOURCE-AGNOSTIC
    projector.py        #   pure: cohort rows -> Observations
    build.py            #   Phase B: read a cohort provider -> merged spans -> emit
    emit.py migrate.py refresh.py
```

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
- **Resilient harvest** — how a Phase A sweep handles a bad year depends on the range. A source
  with **unheld or future years** (e.g. `results`, which 404s a not-yet-certified election)
  skips-and-logs the bad year in its own SAVEPOINT and commits the years it reached — one bad year
  must not roll back the sweep, and a *whole-source* outage (every year skipped) raises a distinct
  signal rather than reading as "nothing to do". A source whose range is **frozen and closed**
  (e.g. `filings`, retired at 2018 — every year either exists or the feed is dead) may instead
  deliberately abort-and-resume (a mid-sweep failure rolls back; re-run from the floor, closed
  years cache-hit): with no future years to skip past, all-or-nothing costs nothing.

### What makes the application "source-agnostic"

The `build.py`/`refresh.py` layer depends on a **cohort interface** (`{election_year: {LD:
[position]}}`, a per-key citation-target accessor), not on a concrete source. Swapping which
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
period, filename/URL stability, schema drift, and label/value inconsistencies. The votewa episode
produced two rules now baked into this pattern — the resilient harvest above, and: **never key a
parser on an exact upstream string.** WA SOS labels the same office three ways
(`State Representative Pos. 1`, `Representative, Position 1`, a bare `State Representative 2`),
sometimes differing between the two seats of one district in one file; a tolerant parser (match the
office, take the trailing position digit) is mandatory, and an exact-match parser silently drops
real seats.

## Checklist — adding a source to an existing target package

1. New `<source>/` subpackage: `transport` (+ offline re-parser, courtesy limiter), `adapter`
   (`BaseAdapter`; archive-only unless the fact is single-cohort-derivable), `normalize` (pure),
   `cohort` (archive-first), `harvest` (resilient — per-year skip for unheld/future years, else
   abort-and-resume for a frozen closed range; see *Resilient harvest* above).
2. A new `Source`/`source_slug` in `provisioning.py`; a non-colliding archive-key scheme.
3. Audit the feed across its range first; encode every gap/variant as a test.
4. Point (or add) the application's cohort provider — do **not** widen an application module to
   know about the source.
5. Wire the Phase A harvest + any daily refresh into `deploy/`; document the CLI in
   [`docs/COMMANDS.md`](COMMANDS.md) and the module in [`AGENTS.md`](../AGENTS.md).
