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
carries: [`PIPELINE-CONFORMED.md`](PIPELINE-CONFORMED.md).

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
stays published beside it, because that key is what Power Map matches a seat on
and mediating it away is what #309 refused.

**Order matters once, at deployment.** `registry_seed` carries the canonical
Role ULIDs across; the registrar's role pass *mints* for anything unregistered.
Run the seed **before** the first registrar pass that sees roles, or 312 fresh
ULIDs replace the ones PM's #312 anchors name. The `role_entity_mismatches`
counter in `parity_spans` is the backstop, gated at zero — it catches the
mistake, but the seed is what prevents it.

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
fold-equal names) union into `proposed_links`, the registrar's sole input.
Corrections are always adjudications — a matching-rule change can propose the
world and move nothing (sticky registry). Splink's fuzzy tail is deferred: the
seeded registry carries every historical link, so exact rules only need the
forward flow; verified live 2026-09-03 — 813 proposals → 0 mints, 0 conflicts,
505 crosswalk-key appends, and `parity-registry` clean (3,135 persons / 219
orgs, 0 missing, 0 mismapped).

## Publication (#311, in progress)

`python -m usa_wa_pipeline.publish` materializes each dataset in
`publish.PUBLISHED_DATASETS` (staging tier + conformed products; deliberate
config — publishing is a decision; lineage comes from the dbt manifest) as an
immutable `USA_WA_DATASETS_ROOT/<name>/<version>/data.csv + datapackage.json`
and flips `catalog.json` last (tmp+rename both — a crash leaves unlisted
orphans, never a listed partial). Skip-if-unchanged: no version churn on a
quiet day. Producer-side gates: a missing table or a row shrink beyond
`--max-shrink` (default 10%) refuses the whole run with nothing minted —
retraction=absence means a degraded build must never ship as mass retraction.
The API serves the tree at `/datasets/*` with `/health/datasets` as the
publication probe. The nightly systemd chain (`scripts/pipeline-nightly.sh`,
`usa-wa-pipeline.timer`, daily 08:00 UTC) runs harvests → dbt build →
registrar → publish → serving load → parity probes (`parity_citations` last);
any counted failure exits 1 so `OnFailure=` emails the operator.

A dev/CI build with NO database must say so: `USA_WA_PIPELINE_HERMETIC=1`
(set by `scripts/dbt-gate.sh` and the dbt tests) is the only thing that lets
the conformed crosswalk models materialize empty — otherwise a missing
`DATABASE_URL` fails the build loudly (#302 CR: empty identity must never
publish with a green build).

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
- **Never weaken a test to go green.** Same rule as everywhere in this repo; a data
  test that fails on real source data is a finding about the source — record it
  (coverage claim, exclusion with a comment, or an upstream issue), don't delete it.

pytest still owns everything Python: `packages/usa-wa-pipeline/tests/` drives dbt
in-process (`dbtRunner`) and proves the harness end-to-end, including that a violated
data test fails the build.
