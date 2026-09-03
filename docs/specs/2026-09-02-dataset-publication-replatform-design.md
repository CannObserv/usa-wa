# Dataset-publication replatform — usa-wa side

- **Date:** 2026-09-02
- **Status:** approved
- **Issues:** usa-wa#302 (epic), power-map#490 (subscriber side)
- **Cross-repo contract:** power-map `docs/plans/2026-09-01-dataset-subscription-architecture-design.md` (approved)

## Goal

Replace the bidirectional PM observation/change-feed sync with unidirectional dataset
publication, and replatform the ingestion pipeline itself onto a file-based
raw/staging/conformed layered architecture (dbt-core + dbt-duckdb). usa-wa becomes
single-master for its slice; PM pulls versioned snapshots; usa-wa never reads PM. The
LWW-NOOP-GATE problem class (#65/#85/#102/#104/#109/#112/#132/#160/#247) becomes
inapplicable rather than mitigated.

Full replatform (option B) was chosen over a downstream-publication-only layer (option A)
deliberately: consistent layer boundaries, file-level introspection and pruning, and
idiom parity with PM's own dbt-duckdb mapping pipeline.

## Corrections to issue #302 as filed

- **`clearinghouse-core` is not deletable.** The "698 files across four PM-facing
  packages" count includes it (35 files), but it is the Layer-1 framework every package
  imports: job harness (#179, 44 CLIs), run ledger (#178), integrity sweep (#54/#55),
  coverage-as-data (#180), db/config/logging. #302 itself keeps "systemd timers + the
  run ledger" — which lives there. The deletable set is **663 files across three
  packages** (`powermap-client` 566, `usa-wa-sync-powermap` 69,
  `clearinghouse-sync-powermap` 28) plus surgical cuts (below).
- **Three inbound PM flows need local replacements**, unstated in #302:
  jurisdictions (the seat model's `Role.jurisdiction_id` FKs a PM-mirrored table),
  PM-curated names/acronyms/dated org names (read-mirrors lose their writer; usa-wa
  becomes source-faithful — an accepted product change), and PM merge decisions
  (local duplicates stay local; Splink is the eventual answer).

## Decisions log

| Decision | Choice |
|---|---|
| Pipeline scope | (B) full replatform: harvest-to-files, dbt-duckdb staging/conformed |
| Entity identity | Stateful **identity registry** (Postgres), separated from the stateless pipeline via a registrar step; persons + orgs only |
| `/api/v1` | **Kept**, all 13 routes, served from a disposable `serving` schema loaded from the published datasets (consumers expected) |
| Published provenance | Coarsened to dataset-level lineage; a `citations` dataset may ship later if demand warrants; `/provenance` keeps working via an internal (unpublished) citations artifact |
| Health surface | Retained on every path; `/health/sync` + `POST /sync/redrive` retire with the outbox; new publication-health probe replaces them |

## Target architecture

```
harvesters (Python, per source; zeep/httpx transports, rate limiters, PDF parser survive)
   → raw/<source>/<run>/…       immutable files + hash manifests
                                 (replaces RawPayload/FetchEvent)
dbt-duckdb pipeline (usa-wa-pipeline)
   staging/     stateless, one cleaning regime per source, NATURAL KEYS ONLY
   matching     exact-rule SQL models + Splink fuzzy tail → proposed_links
   ── registrar (Python job) ⇄ Postgres registry ──  ★ the only mutable pipeline state
   conformed/   staging ⨝ registry crosswalk → stable ULIDs; span engine as
                dbt Python models (tenure_spans/operator_overlay imported unchanged)
publisher       datapackage.json per dataset-version + catalog.json,
                generated from the dbt manifest/exposures
   → /datasets/<name>/<version>/  static, immutable, existing FastAPI service
serving loader  published datasets → Postgres `serving` schema (disposable)
   → /api/v1    13 read-only routes (contract diff below)
```

Postgres holds exactly four things: **registry** (master, small, backed up),
**serving** (disposable projection), **run ledger + coverage** (operational), and
**operator events** (curated succession facts the pipeline reads — usa-wa's analog of
PM's curation overlay). Everything else is replayable from `raw/`.

usa-wa's own API is deliberately the **first consumer of its own published datasets**
(serving loads from `published/`, not from the pipeline directly), so a broken publish
breaks us before it confuses PM.

## Identity registry & registrar

Cross-source matching is a transform; identity assignment is a ledger.

- `registry.person_keys` / `registry.org_keys`: natural key → ULID, append-only in
  spirit, plus adjudication override rows. Seeded once from today's canonical tables —
  **existing ULIDs survive the replatform**, which keeps PM's crosswalk seed valid.
- Only persons and orgs register. Seats are structural tuples
  `(org, role_type, district, qualifier)`; assignments key on `(person, seat, start)` —
  deterministic derived keys, no ledger.
- Registrar decision table, per proposed cluster:

| Registry state of cluster members | Action |
|---|---|
| None known | Mint ULID, register all members |
| All known members map to one ULID | Append the new keys; ULID unchanged |
| Members map to ≥2 ULIDs | Conflict — **no write**; triage row (merge is human adjudication) |
| Registered key proposed into a different cluster | Sticky — registry wins; triage row |
| Registered key absent from all clusters | Nothing; registry membership does not decay |

- Tradeoffs locked in: **sticky over self-healing** (a matching-rule/Splink change can
  propose the world but moves nothing without adjudication — the report size is the
  alarm); **mint-eagerly for unambiguous new keys, quarantine only multi-cluster
  ambiguity** (duplicates are recoverable via merge tombstone; wrong merges are not);
  **merge tombstones are contract** — `merged_into` in the published crosswalk is the
  only signal PM gets to re-point a merged-away person (PM's persons policy is
  report-only-on-absence).
- Edge cases designed for: generational suffixes are identity-bearing and the link
  predicate is term-*overlap*, never adjacency (Jr/Sr same district); deterministic
  source ids anchor clusters through name changes; a source re-key (WSL committees
  across eras) resolves through the append case with era-overlap required in org rules;
  registrar failure or triage backlog leaves the pipeline publishable — unregistered
  keys drop from conformed (inner join) and are counted by the publish gate.
- The registrar is WA-blind: clusters, keys, scores, one decision table. All WA
  knowledge lives in matching rules (dbt SQL + Splink config), mirroring PM's
  domain-free-machinery discipline.
- Reproducibility: conformed = f(staging, registry-at-T); the registry watermark is
  stamped into each datapackage's lineage.

## Publication contract

- **Atomicity:** write the version dir fully, flip `catalog.json` last. A crash mid-
  publish leaves an unlisted orphan, never a listed partial.
- **Publish gates** (producer-side, `JobResult.degraded` idiom): refuse to mint a
  version when row-count delta vs. the previous version exceeds per-dataset thresholds,
  when registrar conflict/quarantine counts exceed floor, or when a staging input is
  absent. Complements PM's applier thresholds — retraction=absence makes a degraded
  harvest look like mass retraction, so both sides gate.
- **Versioning:** per-dataset schema semver in the catalog. Additive = minor;
  rename/removal/semantic change = major, requiring a PM-side issue before publish.
  No dual-publish window while PM is the only subscriber.
- **Retention:** prune to last N versions (N≈14); the catalog lists only retained
  versions. Immutable means never rewritten, not never deleted.
- **Lineage/metadata:** `derived_from`, registry watermark, raw-manifest hashes per
  datapackage. #180 coverage claims (including `absent` spans) ride as metadata on the
  staging datasets they describe.
- **Serialization:** ULIDs as 26-char Crockford base32 everywhere in exported files,
  pinned by test (the `::text` UUID-hex form 404s at PM).
- Two catalog tiers: staging datasets (per source — triage/lineage surface) and
  conformed products (persons, organizations, roles/seats, assignments,
  person_crosswalk, org_crosswalk, jurisdictions). No per-consumer logic, ever.

## Contract diff — current `/api/v1` vs. published datasets

- Survive: entity structural fields; name survivorship columns become explicit dbt
  outputs; `holder_name_raw`; `Organization.active`; base32 discipline.
- Transform: the assignment 4-part `source_id` span key becomes real columns
  (`span_kind`, `span_discriminator`, `span_start_biennium`); `PersonDetail.identifiers`
  un-embeds into `person_crosswalk`; coverage moves into catalog metadata.
- Dropped from contract: `pm_*` anchor ids; `source`/`source_id` scalars on persons and
  orgs (multi-source by construction); row-level `created_at`/`updated_at` (the dataset
  version is the clock); lifecycle tombstone columns (absence + crosswalk tombstones
  replace them); pagination/liveness/`include_hidden` query semantics (snapshot = the
  whole live set).
- `/api/v1` keeps its route inventory; API.md gains migration notes where response
  fields die with the serving-schema flip.

## Provenance & integrity

- Published contract: dataset-level lineage only (accepted coarsening).
- `/provenance/{type}/{id}` keeps working: the pipeline emits an internal, unpublished
  citations artifact the serving loader materializes.
- The #54 integrity sweep re-points at `raw/` manifests (re-hash files vs. manifest,
  rolling byte budget as today). `seed_manifest.py`'s sidecar convention generalizes
  into the raw-manifest format. The RawPayload corpus gets a one-shot hash-preserving
  export to `raw/` before its tables retire.

## Package disposition

| Package | Fate |
|---|---|
| `powermap-client`, `clearinghouse-sync-powermap`, `usa-wa-sync-powermap` | Deleted (663 files) + sidecar unit, `/sync/redrive`, `/health/sync`, `docs/LWW-NOOP-GATE.md`, MODULES-SYNC* docs |
| `clearinghouse-core` | Shrinks: keep logging, config, db/ulid, job harness, run ledger, coverage; retire `adapter.py`, `runner.py`, `provenance.py`, `jurisdictions.py`; `integrity.py` rewritten for files |
| `clearinghouse-domain-legislative` | Survives as the pure-logic library dbt Python models import (`terms`, `tenure_spans`, `operator_overlay`, `span_kinds`, `operator_events`); identity models become serving-schema models, anchor columns dropped |
| `usa-wa-common` | Survives; gains the locally-owned WA jurisdictions registry (replacing the PM mirror) — published as a dataset |
| `usa-wa-adapter-*` | Transports, parsers, rate limiters, coverage declarations survive; runner integration → file-writing harvest jobs; normalizers port to staging models |
| `usa-wa-facts-seats` | Logic ports to conformed-tier models |
| New: `usa-wa-pipeline` | dbt project (staging/matching/conformed) + publisher |
| New: `usa-wa-registry` | registry schema, registrar job, triage CLI |
| New: serving loader | in `usa-wa-api` |

## Orchestration & ops

Systemd timers as today. Nightly chain: harvest → dbt staging/matching → registrar →
dbt conformed → publish → serving load; each stage on the #179 job harness (ledger row,
`--dry-run`/`--json`, `OnFailure=` alerting). New `/health/datasets` probe: latest
catalog version + age per dataset, last publish outcome, gate status, registrar triage
backlog. No broker, no Dagster/Airflow.

## Transition plan

**The running system is the oracle.** Build the new pipeline in parallel; a parity
harness diffs new-pipeline outputs against today's canonical tables until clean; the
old system keeps running (including the PM sync) until cutover.

1. Toolchain: dbt-core + dbt-duckdb into the uv workspace; gates policy.
2. Raw tier: harvest-to-files framework + manifests; port harvesters; re-point
   integrity sweep; one-shot RawPayload export.
3. Staging models per source, parity-checked against canonical per-source rows.
4. Matching models + Splink tail; registry seeded from canonical ULIDs; registrar;
   crosswalk parity.
5. Conformed models (span engine as Python models) + span parity vs. assignments.
6. Publication: datapackage/catalog + gates + `/datasets/` + `/health/datasets`.
7. PM anchor export (base32) → PM crosswalk seed (their transition steps 2–4).
8. Serving loader; flip `/api/v1` to the serving schema; API.md migration notes;
   retire `/health/sync` + `/sync/redrive`.
9. Freeze: PM revokes usa-wa write scopes; PM flips applier to execute (their sequence).
10. Deletion sweep + recalibration: three sync packages, old canonical write path,
    provenance tables, anchor columns; AGENTS.md layer table, MODULES docs, context
    manifest, `verify-units.sh`, coverage floors.

## Testing & gates

- dbt schema + data tests run against duckdb (no Postgres) — wired into pre-commit/CI.
- pytest for harvesters, registrar, publisher, serving loader, API (TDD as ever).
- The parity harness is the integration tier during transition; it retires at step 10.
- Coverage floors recalibrate after the deletion sweep (#198/#216 gates).

## Out of scope

- A published `citations` dataset (revisit on demand).
- Splink beyond the roster↔WSL person tail.
- DCAT interop; any second-consumer accommodations.
- Dropping operational Postgres.
- PM-side pipeline, applier, overlay, and retirements (power-map#490 and its design doc).
