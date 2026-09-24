# The dataset-publication pipeline

`packages/usa-wa-pipeline/` — the dbt-core + dbt-duckdb project at the center of the
#302 replatform (spec: [specs/2026-09-02-dataset-publication-replatform-design.md](specs/2026-09-02-dataset-publication-replatform-design.md)).
This page: layout, commands, and the TDD policy for dbt models. Scaffolded at #303;
each layer's models arrive with its sub-issue (#306–#309).

## Layout

```
packages/usa-wa-pipeline/
  src/usa_wa_pipeline/   — Python surface: staging/matching/parity/registry + the publisher
  dbt/                   — the dbt project
    dbt_project.yml      — three model layers: staging / matching / conformed
    profiles.yml         — duckdb target; USA_WA_PIPELINE_DB names the db file
                           (default data/pipeline.duckdb relative to the repo root;
                           gate + tests always override with a throwaway path)
    models/staging/      — one cleaning regime per source, NATURAL KEYS ONLY
    models/matching/     — cross-source link proposals feeding the registrar (#308)
    models/conformed/    — registry-joined products with stable ULIDs, plus the
                           structurally-keyed roles dimension (#309)
```

Layer rules are the spec's: staging never joins across sources and never sees a ULID;
matching proposes and never writes identity; conformed is a stateless join against the
registry crosswalk. `usa_wa_pipeline` sits beside `usa_wa_facts_seats` in the
import-linter layer order and, like it, may never import an adapter `transport` —
models re-parse the archive, they do not drive wires.

## Commands

```bash
# Build everything + run all schema/data tests against a throwaway db (what the gate runs)
scripts/dbt-gate.sh

# Iterate against a persistent local db
export USA_WA_PIPELINE_DB=data/pipeline.duckdb
uv run dbt build --project-dir packages/usa-wa-pipeline/dbt --profiles-dir packages/usa-wa-pipeline/dbt

# One model + its tests
uv run dbt build --project-dir packages/usa-wa-pipeline/dbt --profiles-dir packages/usa-wa-pipeline/dbt -s stg_scaffold_smoke
```

The pre-commit hook `dbt-build` runs the gate whenever a commit touches
`packages/usa-wa-pipeline/` (pinned by `scripts/tests/test_pipeline_gate.py`). dbt's
`target/` and `logs/` and the local `data/` db are git-ignored; the gate writes its
artifacts into a temp dir so the checkout stays clean.

## The raw tier (#304)

Upstream of dbt: pristine wires in a file store at `USA_WA_RAW_ROOT` (default
`raw/`), the file analog of the Postgres provenance pair and the input the
staging models read (#306).

```
raw/<source-slug>/
  objects/<sha[:2]>/<sha256>   — content-addressed wire bodies, immutable, deduped
  runs/<run_id>.json           — one manifest per harvest run (the FetchEvent analog)
  latest.json                  — resource_id → newest ok fetch, for TTL decisions
```

- Harvesters: `python -m usa_wa_adapter_legislature.raw_harvest` (daily SOAP set +
  member fan-out, committees enumerated from the run's own roster wire — no DB),
  `…usa_wa_adapter_pdc.raw_harvest` (winner cohorts), `…usa_wa_adapter_sos.raw_harvest`
  (filings + results). All reuse the adapters' transports, rate limiters, and the
  Postgres archive's resource-id vocabulary; per-resource failures are contained as
  `err` manifest entries; a byte-identical re-fetch is recorded but stored once
  (`skip_unchanged` parity). `--ttl-days N` skips fresh resources; the default 0
  forces the daily wire.
- Integrity: `python -m clearinghouse_core.raw_integrity` re-hashes objects against
  the sha256 they are stored under (the name is the baseline) — rolling
  `--byte-budget` with a cursor at `<root>/.raw_integrity_state.json`, exit 1 on any
  mismatch/missing object. The Postgres sweep keeps running beside it until #302
  cutover.
- Retention: the tracked sources are archival (#54) — nothing deletes; manifests are
  small and kept indefinitely.

## Staging: legislature (#306)

Eight models under `models/staging/`, each a thin adapter over a pytest-covered
row-builder in `usa_wa_pipeline.staging` (wsl.py / roster.py); the offline SOAP
parse goes through `usa_wa_adapter_legislature.parsing` (same operation
bindings as the live pulls; one WSDL GET per service, amortized):

| Model | Key | Notes |
|---|---|---|
| `stg_wsl_committees` | (biennium, committee_id) | newest `committees-roster:*` wire per biennium |
| `stg_wsl_sponsors` | (biennium, member_id, agency) | a chamber move lists both agencies |
| `stg_wsl_committee_members` | (biennium, committee_id, member_id, long_name) | chamber movers list twice; committee key rides the resource id (#82) |
| `stg_wsl_meetings` | none (raw refs) | all agencies kept; Joint/`Other` filter is downstream policy |
| `stg_roster_members` | (year, chamber, district, order, name) | order is seat-lineage order (#229): a successor inherits it |
| `stg_pdc_winners` | (chamber, election_year, filer_id) | #307; `person_id` is the `wa_pdc` link value |
| `stg_sos_results` | (election_date, race, candidate) | #307 |
| `stg_sos_filings` | — | #307; store empty until the raw harvest runs (no archived filings payloads existed to export) |
| `stg_raw_fetches` | (source, resource_id) | #313; the attestation dimension — sources DISCOVERED from the raw root, never configured |

**Every staging row names its own wire (#313).** `source` + `resource_id` are
appended to every builder's column list, so the chain
`entity → staging row → resource → sha256` closes without a lookup nobody
maintains. The digest, fetch time and URL are NOT duplicated onto those rows —
they live once per resource in `stg_raw_fetches`, which reads `latest.json` for
the digest and the run manifest it names for the URL and byte count. A pruned
manifest costs a row its colour, never the row itself.

Composite keys + coverage floors (sponsors 1991-92, roster 1889) live as
singular tests under `dbt/tests/` — vacuous on an empty store, so the hermetic
commit gate stays fast.

**Parity probe** (the transition oracle's comparator, write-free):

```bash
uv run python -m usa_wa_pipeline.parity_wsl --root /home/exedev/usa-wa/raw
uv run python -m usa_wa_pipeline.parity_pdc --root /home/exedev/usa-wa/raw   # subset mode: canonical ⊆ staging
```

Diffs staging key sets against live canonical Postgres; exit 1 on any
unexplained divergence. Accepted divergences are code (`parity_wsl.ACCEPTED`),
each with a named reason, and a stale acceptance fails the run. Verified clean
2026-09-03: committees 208/186 with 22 accepted (archived-meeting Joint/`Other`
bodies canonical never normalized), sponsors 640/641 with 1 accepted (the Lt.
Governor's ex-officio Rules seat from the retired `committee-members:`
vocabulary); PDC 312/312 exact. (#309 corrected the committee comparator to
`org_type IN ('committee','other')` — canonical files Joint/`Other` bodies as
`other` — which dissolved all 22 earlier committee acceptances: 208/208 exact,
none accepted.) SOS has no per-source probe on purpose —
results/filings corroborate spans, covered by #309's span parity.

## The conformed tier (#309, #313)

Crosswalks + entities, the tenure-span engine, the roles dimension and the
citations chain — the registry-joined products and every guard each one
carries: [`PIPELINE-CONFORMED.md`](PIPELINE-CONFORMED.md), with crosswalks + entities split out
into [`PIPELINE-CONFORMED-ENTITIES.md`](PIPELINE-CONFORMED-ENTITIES.md).

## Identity registry (#308)

`registry` Postgres schema (master state — the pipeline's ONLY mutable state):
`entities` / `entity_keys` / `adjudications`, machinery in
`clearinghouse_core.registry` (jurisdiction-blind by design — see
MODULES-FRAMEWORK.md). Key namespaces: `<source-slug>:<source_id>` and
`<scheme>:<value>` (e.g. `usa_wa_legislature:27992`, `wa_pdc:7710`).

**Three kinds since #313: `person`, `org`, `role`.** Roles are the odd one, and
deliberately so — a role has **no matching problem**. `role_for_span(kind,
discriminator)` is a pure function, two runs necessarily agree, and roles never
merge, so the ledger is always a 1:1 map from one natural key
(`usa_wa_legislature:<role_key>`) to one entity. It exists for the *other*
service a registry provides: a stable handle. `role_key` is a derived string,
and this repo's rule against keying on an exact upstream string applies just as
much to a public id — so `/api/v1` addresses a role by ULID while `role_key`
stays published beside it, because that key is what a subscriber matches a seat on
and mediating it away is what #309 refused.

**Order matters once, at deployment.** `registry_seed` carries the canonical
Role ULIDs across; the registrar's role pass *mints* for anything unregistered.
Run the seed **before** the first registrar pass that sees roles, or 312 fresh
ULIDs replace the canonical ones — and a seeded role's ULID is the `entity_id`
published in `roles` and joined from `assignments`, so every consumer's join
would silently re-point. **The seed is the only guard.** `role_entity_mismatches`
in `parity_spans`, gated at zero, counts only a *seeded* role (its canonical
ULID a registry entity) whose key moved: a role born after the seed has no
earlier published id to protect, so one whose canonical ULID the registry never
held is `role_post_seed`, reported but not gated (#402) — the orgs rule below.
A registrar pass ahead of the seed leaves *no* canonical ULID in the registry,
so it reads as `role_post_seed` ≈ every role and passes, exactly as
`parity_registry` would for persons and orgs. Only a registry rebuilt from empty
can repeat it; the live one is seeded (312/312 roles, 2026-09-24).

**Orgs register nightly too** (`registrar.load_org_keys`): singleton clusters
over every staged committee id (`stg_wsl_committees` ∪ `stg_wsl_meetings`) plus
`STRUCTURAL_ORGS`, under the same seed-first rule as roles. After the seed the
two tiers mint independently, so `parity_registry` counts a canonical row whose
ULID the registry never held as `post_seed`, not `mismapped`; an unbound key is
still `missing`.

**Persons: every WSL sponsor, not only the matched ones (#403).** `proposed_links`
holds only matched pairs, so a legislator no rule pairs — an appointee with no
PDC winner row, a member newer than the roster PDF — never reached the
registrar (111 of 640 sponsors on 2026-09-22, registered only by the seed), and
the next one would fail `parity-registry` (`person_missing`) and `parity-spans`
(`unregistered_spans`). `registrar.load_sponsor_keys` adds a singleton
`(key, key)` pair per staged `usa_wa_legislature:<member_id>`; union-find folds
a paired sponsor into its component, so matched clusters are unchanged. Only a
numeric id mints alone — the registry has no delete — and any other (blank,
NULL) degrades the job, named in `malformed_sponsor_ids`. **WSL keys only**
(decided 2026-09-23): a roster key (`usa_wa_legislature_roster:<fold>:<year>`)
is built by us from a name, so a parser or fold change would mint a published
duplicate — it stays pair-only and drift surfaces as `missing`, for
adjudication. A PDC id is a crosswalk key on a WSL person, never a standalone
one. Like a new seat or committee, a new legislator publishes one build after
the one that first stages them (`dbt build → registrar → publish`);
`parity-spans` re-reads the registry after the registrar, so that lag never
trips it.

```bash
# One-time: seed from canonical rows, ULIDs preserved (idempotent)
uv run python -m usa_wa_pipeline.registry_seed
```

```bash
# Nightly: cluster proposed_links and apply the decision table (dry-run first)
uv run python -m usa_wa_pipeline.registrar --db data/pipeline.duckdb [--dry-run]
# Human corrections (merge/move), each with a mandatory recorded note
uv run python -m usa_wa_pipeline.adjudicate merge --kind person --loser <ULID> --survivor <ULID> --note "…"
# A WRONG merge is corrected by unmerge (a reverse merge is refused — it would
# cycle the tombstones and drop both entities from conformed). Two steps, in
# THIS order (`move` refuses a tombstoned destination, so the revive comes
# first). Unmerge reports `keys_moved_away` in its counters — the keys still
# bound elsewhere; move each back onto the revived entity, or it stays keyless
# (absent from conformed, and the registry parity probe + seed alarm nightly):
uv run python -m usa_wa_pipeline.adjudicate unmerge --kind person --entity <revived-ULID> --note "…"
uv run python -m usa_wa_pipeline.adjudicate move --kind person --key <each reported key> --to <revived-ULID> --note "…"
# Invariant probe: canonical identity ⊆ registry crosswalk
uv run python -m usa_wa_pipeline.parity_registry
```

Matching models (`models/matching/`): `match_pdc_wsl` (SQL — same seat + seating
biennium + surname token-containment; PDC renders names in both orders) and
`match_roster_wsl` (Python — MUST use the adapter's `identity_fold`, the same
fold the seeded roster keys carry; join = biennium + chamber + district +
fold-equal names) union into `proposed_links`, the registrar's only source of
pairs — persons also take a singleton per staged WSL sponsor (§ above, #403).
Corrections are always adjudications — a matching-rule change can propose the
world and move nothing (sticky registry). Splink's fuzzy tail is deferred: the
seeded registry carries every historical link, so exact rules only need the
forward flow; verified live 2026-09-03 — 813 proposals → 0 mints, 0 conflicts,
505 crosswalk-key appends, and `parity-registry` clean (3,135 persons / 219
orgs, 0 missing, 0 mismapped).

## Publication (#311)

The publication contract — the versioned dataset tree, the catalog and its
heartbeat, per-dataset schema versions, tiers, the CSV dialect:
[`PIPELINE-PUBLICATION.md`](PIPELINE-PUBLICATION.md).

## Assignment gates (#359, #360, #363)

Historical seat occupancy over conformed `assignments`: the gate, the clipping that
drained it, and the duration check beside it.

### Seat occupancy is gated (#359)

`dbt/tests/assignments_seat_occupancy.sql` asserts that no two entities hold one
`seat:*` role over overlapping validity. Nothing else did: `assignments_key`
tests span *identity* (one tenure start per entity), which two different holders
of one seat pass cleanly, and the daily `succession-invariants` gate scopes to
`is_active` rows — the current cohort only, never history.

It ships **baselined at 35** (#360), via dbt's own thresholds:

```
{% set baseline = 0 if env_var('USA_WA_PIPELINE_HERMETIC', '0') == '1' else 35 %}
{{ config(severity='error', error_if='>' ~ baseline, warn_if='!=' ~ baseline) }}
```

`>baseline` errors on a new conflict; `!=baseline` warns on *fewer* too, so
repairing one is as loud as introducing one and the baseline announces its own
staleness. Drain to zero, then drop the thresholds for a plain `error`.

The baseline is **mode-aware on purpose**: the hermetic build materializes
conformed models empty, so its correct expectation is 0. A flat 91 made the gate
warn `Got 0 results` on every pre-commit and every CI run — noise that would
have cost the ratchet its point, since the day the real count drops the "ratchet
me down" warning would look exactly like the one everyone had learned to ignore.

A companion test, `assignments_seat_kinds_covered.sql`, fails if any `seat:*`
role carries a `span_kind` the occupancy gate does not cover. The gate is scoped
by an allow-list, and an allow-list narrows silently — a new seat family would
be ungated with every test still green, which is how `succession-invariants`
came to check only the current cohort without anyone noticing.

### Counterpart clipping (#360)

The gate first measured 91 overlaps. None were matching failures — every pair is
two genuinely different people — and none were regressions: they predate the
replatform, and no tier had ever checked historical occupancy.

They are quantization artifacts. The operator overlay applies a dated boundary
to **the span the event names**; the counterpart on the other side of the handoff
keeps its biennium-derived edge, and the two overlap across the gap between them.
`clearinghouse_domain_legislative.seat_clipping` closes that gap: where exactly
one side of an overlap carries a dated boundary, the other side's *quantized*
edge yields to it — a date the roster states is better evidence than a biennium
the builder derived.

Quantization is measured against **the span's own biennium**, never a Jan-1 /
Dec-31 pattern: a span whose `valid_from` equals its `start_biennium` floor was
put there by the builder, one that differs was dated by an event. Pattern
matching would read a genuine December 31 resignation as a ceiling and clip a
real boundary away.

It runs in `assignment_rows`, over the **union** of every span family, for two
reasons. One Senate seat's two holders routinely come from different builders —
a WSL-joined incumbent and a minted pre-1991 successor — so a per-family clip is
blind across exactly the seam the handoff crosses. And that join is the single
door every publication path goes through, so no caller can skip the invariant.

Boundaries move; **no row is dropped, added or reordered**, and no `is_active`
flips. A clipped edge is **derived**, and is never read back as a stated date by
a later pair on the same seat: clipping one holder's start onto a predecessor's
dated exit and then treating that new start as evidence cascades one clip into
the next, and collapsed Donn Charnley's LD-44 tenure to a single day in the first
cut. No clip may leave a span without duration — `assignments_span_duration`
(#363) asserts that independently, for the whole table.

The rule refuses more than it applies, and the refusals are the interesting part
— each is a different kind of unknown rather than a backlog of the same one. The
counts are on the baseline comment in the gate; the shapes are: **neither side
dated** (no stated boundary to clip to), **merged return** (the successor is nested
inside the predecessor, whose row is therefore two tenures — usa-wa#267 — so
clipping either side discards one of them; it needs a split), **crosses a biennium** (the roster
listed the successor *before* the predecessor's dated exit — the sources
contradict each other), and **both sides dated** (two stated dates that still
overlap: the #358 shape, for adjudication).

### A tenure has duration (#363)

`dbt/tests/assignments_span_duration.sql` asserts `valid_to is null or valid_to >
valid_from`. A plain `error`, no baseline — the corpus is clean on this, so a
threshold would only be somewhere for a regression to hide.

It exists because the occupancy gate above cannot see a single span. That gate
needs **two distinct holders** overlapping on one seat; `assignments_key` tests
span identity, which one degenerate span passes cleanly. So a tenure collapsed to
a point was invisible to the whole conformed tier — and one was: Derek Stanford's
LD-1 Senate seat, closed by a person-scoped `departed` at the instant a `seated`
opened it, taking 18 months of a sitting senator's service out of the published
record. The overlay now refuses to let a departure end a tenure that began at the
same instant (a chamber move is not an exit), and this gate is the independent
check that would have caught it without anyone triaging #360.

Short is not empty. Washington seats military substitutes for days at a time — Jon
Wyss held LD-6 for two days in 2005 while Brad Benson was on military leave — so
the gate asserts duration, never a minimum (#362).

Party and committee roles are excluded by `span_kind` rather than by an
exception list — they are legitimately multi-holder. One caveat if House
coverage deepens past 1965: pre-1965 House seats were at-large, two per district
with no Position, so a position-less `seat:house:ld-N` would legitimately carry
two holders.

## TDD for dbt models

Red → Green → Refactor applies; what changes is where each color lives:

- **A model's contract is its schema entry.** Before writing `stg_x.sql`, write the
  `schema.yml` block declaring its columns and data tests (`not_null`, `unique`,
  `accepted_values`, relationship tests). A declared model with no SQL fails `dbt
  build` — that is the red. The SQL that satisfies the tests is the green.
- **Behavior beyond column shape** (a survivorship rule, a dedup, a windowing edge)
  gets a dbt **data test** (`tests/*.sql` — a query that must return zero rows) or a
  seed-driven unit test: check in a minimal input seed + the expected output as a
  seed, and a test selecting the symmetric difference. Write it failing first.
- **dbt Python models** (the span engine, #309) keep their logic in importable,
  pytest-covered functions (`clearinghouse_domain_legislative` stays the home of the
  pure span code); the dbt model is a thin adapter over them. pytest owns the logic's
  red/green; dbt data tests own the wiring's.
- **A Python model's column types are declared, never inferred (#361).** Its row
  builder's module carries a `*_SCHEMA` — ordered column → duckdb type, with
  `*_COLUMNS = list(*_SCHEMA)` — and the model returns
  `frames.typed_relation(session, rows, SCHEMA)`, not a bare `pd.DataFrame`. duckdb
  reads an `object` column with no values as `INTEGER`, so a bare frame types every
  column of an empty model (the whole hermetic build, and any empty source in
  production) and any all-NULL column wrong — and a dbt test that is correct against
  real types then fails to bind. `test_model_schemas` checks every Python model
  against its schema in the hermetic build, and fails when a model is not listed.
  A data test therefore never needs a `cast` just to bind there.
- **Never weaken a test to go green.** Same rule as everywhere in this repo; a data
  test that fails on real source data is a finding about the source — record it
  (coverage claim, exclusion with a comment, or an upstream issue), don't delete it.

pytest still owns everything Python: `packages/usa-wa-pipeline/tests/` drives dbt
in-process (`dbtRunner`) and proves the harness end-to-end, including that a violated
data test fails the build.
