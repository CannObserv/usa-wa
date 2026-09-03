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

## Conformed: crosswalks + entities (#309, in progress)

`models/conformed/`: `person_crosswalk` / `org_crosswalk` (the registry's
published identity surface — every natural key, its entity ULID, and the
`merged_into` tombstone, read via `usa_wa_pipeline.registry_read`; empty only
under `USA_WA_PIPELINE_HERMETIC=1` — a missing `DATABASE_URL` fails the build,
§ Nightly chain below) and `persons` /
`organizations` (one row per LIVE entity; logic in
`usa_wa_pipeline.conformed.entities` — person names roster > WSL > PDC with
newest-attestation-wins, org attributes from the newest biennium's roster wire,
meeting-ref fallback for Joint/`Other`, and the synthesized structural orgs —
legislature, chambers, parties — from `usa_wa_common.orgs.STRUCTURAL_ORGS`).
`profiles.yml` pins `threads: 1`: threaded Python models race first-imports of
the workspace packages. Verified on the real archive 2026-09-03: 3,135 persons
(2,999 roster-named / 135 WSL / 1 known gap — the Heck acceptance) and 219
orgs, type distribution matching canonical exactly. Assignments (the span
engine as a Python model) landed next — see below; roles/seats complete the
layer.

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
registrar → publish → parity probes; any counted failure exits 1 so
`OnFailure=` emails the operator.

A dev/CI build with NO database must say so: `USA_WA_PIPELINE_HERMETIC=1`
(set by `scripts/dbt-gate.sh` and the dbt tests) is the only thing that lets
the conformed crosswalk models materialize empty — otherwise a missing
`DATABASE_URL` fails the build loudly (#302 CR: empty identity must never
publish with a green build).

## Conformed: tenure spans (#309 part 2)

`models/conformed/assignments.py` is a thin binder over
`usa_wa_pipeline.conformed.spans` — merged tenure spans for all four kinds
(`party`, `chamber-senate`, `committee`, `chamber-house`), joined to the person
crosswalk. The span `source_id`'s parts become real columns
(`span_kind` / `span_discriminator` / `span_start_biennium`), retiring the
string-splitting workaround `docs/API.md` documents.

**Two families, one table, disjoint identity spaces.** The WSL archive keys on
numeric member ids from 1991; the roster PDF keys on minted
`<fold>:<first-session-year>` identities before it. `source` names which space
a row's `member_id` belongs to, and the crosswalk lookup is
`<source>:<member_id>` for both — a row must never inherit a module default.
The roster family is `roster_pdf.build.build_pre1991`'s emission half minus
everything that existed to mutate Postgres (minting Persons, retiring
unasserted rows, the anchor bootstrap, citation writes); what it keeps is the
operator overlay scoped to its own members — every pre-1991 span is this
builder's, so the roster's 922 dated mid-term boundaries take effect here or
nowhere (#226) — and the unattested-span check, which refuses a seat the
overlay synthesized from an event the edition never listed.

**One resolve feeds both.** `roster_resolution()` runs the ~8,600-record
identity resolve once and partitions by disposition: WSL-joined observations
deepen the sponsor build (#228), minted ones are the roster family. Resolving
twice would double the cost and let the halves disagree about who is joined.
The acceptance oracle (`verify_pre1991` — partition exactness, person-side
Senate simultaneity — plus the party vocabulary) is imported unchanged and runs
before anything is built, as the Postgres tier runs it before anything is
written.

Nothing about the span engine is re-implemented. The pure engine
(`build_tenure_spans`, `apply_operator_events`), the projections, the #105
roster hygiene, the #145 biennium-scoped exemption and the #144 artifact
denylist are all **imported unchanged** and applied in the Postgres tier's own
order — each encodes a production incident. Two structural differences:

- **No DB half.** `close_stale_spans` (#83), the synthetic-anchor bootstrap and
  the `load_context_spans` read exist to mutate a durable table; a stateless
  transform recomputes everything, so a span the archive stops asserting is
  simply absent (retraction-as-absence, the publication contract).
- **Context spans come from the same run** (#267): committee spans build first,
  then House, and both serve as the sponsor build's context — no cross-builder
  blindness and no DB read. With `chamber-house` landed the seam is complete:
  Liz Pike's 2,190-day party gap, the incident #267 is named for, is exactly a
  member who returned only to a House seat.

**The House Position seat** (`conformed/house.py`) is the Layer-3b composition
the other families do not need: WSL owns *who sits* (the sponsor roster — LD +
party), SOS owns *which position* (the ballot's Position 1/2). The #105 (a)
mover exclusion, the #123 even-seating ∪ odd-special-**winners** map, the #118
back-chain and the #103 within-LD elimination are imported unchanged from
`usa_wa_facts_seats.house`. `restrict_to_biennium` dissolves with the rest of
the DB half — a stateless rebuild is unconditionally the unrestricted, deep
one, so the #100 depth-mismatch question cannot arise here at all.

**Roles and seats are structural, not registered** (`conformed/roles.py`). A
Role is a named slot in an Organization; an Assignment binds one in time
(ONTOLOGY.md § 2). The span already carries the slot's identity as
`(span_kind, span_discriminator)`, so the `role_key` is a pure function of it —
`seat:house:ld-5:position-1`, `party-role:democratic`,
`committee-member-role:28240`, `seat:senate:ld-22` — identical on every run and
aligned 1:1 with Power Map's seat match key. No ULID mediates it; only the
*organization* the slot belongs to is registry-joined. Every key function is
imported unchanged from the adapter's normalizer and the WA vocabulary.

**#313 adds a role's own `entity_id`** without disturbing that. The key is still
structural and still what PM matches on; the ULID is a stable handle for the API
to address, minted through the registry's third kind (§ Identity registry above)
and carried across from `canonical.roles` so the #312 anchors keep naming the
same rows. Neither crosswalk may drop a role: a seat exists whether or not the
registry has reached it, and the nightly runs `dbt build → registrar → publish`,
so a brand-new seat is unregistered in the build that first sees it and bound by
the next. `unregistered_roles` and `unregistered_orgs` make that one-run latency
visible; `role_entity_mismatches` separates it from a *broken* anchor.

A deterministic join that has forked is the one failure this design cannot
tolerate — but the dbt `assignments_name_a_role` test does **not** detect it
(CR 77). `roles` is generated by iterating `assignments` through the same
`role_for_span`, so `assignments.role_key ⊆ roles.role_key` holds by
construction and that query is unfalsifiable; it pins containment, which is
worth pinning, and nothing more. The fork that can actually happen is ours
drifting from the Postgres tier that already publishes these keys to Power Map,
and the oracle for it is `canonical.roles` — 312 rows against our 312, exact in
both directions, measured 2026-09-03. `parity_spans` diffs them on its own
ratchet (`--role-baseline`, default 0), because dbt has no session to reach
that table.

The diff covers the **attributes too**, not just the key (CR 84): `role_type`,
`name` and `qualifier` are each derived here independently of the tier, and the
one production instance of this fork changed no key at all — #110 churned 305
party roles on local `member` against PM's `party_member`. All three measure 0
mismatches across the 312 roles, so `role_attribute_mismatches` is gated at zero
rather than ratcheted.

Each family's input carries a **refusal**, on one rule: an input whose absence
silently deletes facts must refuse, not return empty (CR 57). The roster tier
for the #228 deepening, the SOS ballot for the House seat — chamber-house is
~4% of the table, inside the publish gate's 10% shrink floor, so its
disappearance is exactly the kind nothing downstream would catch. Both have an
explicit seam (`extra_observations`, `house_spans`) for stating the family
rather than deriving it.

Two curated Postgres inputs, both read through explicit seams and both empty
only under `USA_WA_PIPELINE_HERMETIC=1`: the registry crosswalk
(`registry_read`) and the operator succession events (`operator_read`). The
event read orders by `(effective_date, id)`: the overlay sorts **stably**, so
input order settles same-date ties (prod holds seven such pairs) and a
content-hashed dataset cannot inherit Postgres's unspecified order.

**The #228 deepening is a standing input, not an enrichment.** An empty roster
under a live sponsor corpus is *refused*, because the failure is invisible
downstream: the key set shifts to shallow 1991-start spans while the row count
barely moves, so the publish shrink gate sees nothing and the parity probe only
runs afterward. Both routes to an empty deepening are refused — an empty roster
tier, and a roster tier present but parsing to zero records (an upstream
rename) — and the refusal lives on **`roster_resolution`**, not only on
`build_all_spans` (CR 76). That distinction is the whole point: `build_all_spans`
raises only when `extra_observations is None`, and neither production caller
passes `None` — the `assignments` model and `parity_spans` both hand it
`roster_resolution(...).joined`, so the resolve runs once for two families. The
guard therefore sat on a door production never opens. It now sits on the resolve,
which is the door they use. Pass `extra_observations` — `[]` included — to state
the deepening rather than derive it; an empty *corpus* (no sponsors, the
hermetic build) still resolves to an empty partition without complaint.

**Python models cannot log.** A `dbt build` never calls `configure_logging()`,
so a `get_logger()` call inside a model emits nothing — the info path is
dropped and the warning path reaches `logging.lastResort`, which prints the
message and discards `extra`. Counters that must reach an operator therefore
belong in a job, not a model: `parity_spans` recomputes the crosswalk join and
reports it under the harness, where records serialize as JSON.

**Reported is not enough — five counters are gated at zero.** The nightly's
`OnFailure=` alerting fires on the *exit code*, so a counter that only reaches
journald tells nobody while the job passes. `unregistered_spans` (a registrar
gap silently shrinking the published table), `unregistered_orgs` (the same gap
in the role dimension — a role whose org is unregistered still publishes, by
design, so nothing else notices it going headless), `malformed_roster_rows`
(partial roster corruption quietly degrading the #228 deepening),
`unparsable_canonical_keys` and `role_attribute_mismatches` (the #110 shape —
same key, different classification) each carry no known-stale story — unlike the
two divergence ratchets — and each measures 0 on the live corpus, so any of them
nonzero exits 1 and names itself in `integrity_failures`. The two ratchets name
themselves in `ratchet_failures` for the same reason (CR 89): they share the
exit code, so the alert has to say which one moved.

`unregistered_orgs` reaches the probe rather than the model for the reason
above, and the role keys are derived there from the **spans**, not from the
crosswalk-joined rows: a slot exists whether or not the person filling it is
registered, so reading the joined rows would make the role parity a statement
about the registry instead of about the derivation (CR 86). A registrar gap
would then report as a phantom role fork — sending the operator after the wrong
defect — and would *hide* a genuine fork whose only spans happen to be
unregistered.

The probe's crosswalk read is **not** the read the model made: the nightly runs
`dbt build → registrar → publish → parity`, so the registrar may have bound
keys in between. `registered_spans` therefore describes the registry as it
stands *now* — the state tomorrow's build publishes from, which is the gap
worth alarming on. A gap the registrar has since closed is transient and
correctly reads as zero.

**The canonical oracle is stale.** `python -m usa_wa_pipeline.parity_spans`
diffs both families against `canonical.assignments` — keyed on
`(source, source_id)` — and gates on a **ratchet**, not equality: measured
2026-09-03 the stored rows diverge by 82 (79 missing / 2 extra / 1 dated
differently). `chamber-house` contributes **none** of that: 329 built, 329
canonical, exact. For the WSL family (45 of those) running the Postgres-tier
adapter's *own* pipeline fresh that day reproduced the identical divergence,
because the stored rows predate the current identity resolve (#277/#281); port
and adapter agreed with each other exactly, 4,851 = 4,851, zero differences.

The roster family's 37 are **the same story from the other side**: 15
identities the snapshot minted as roster persons and today's resolve joins to
WSL members. Cliff Bailey is the worked example — canonical holds both a
shallow `15:*:1991-92` pair and a minted `cliffbailey:1985:*` pair, where this
build asserts the one merged `15:*:1985-86` tenure the deepening produces.
Nothing is lost; the tenure moved families, which is what the #97 collapse is
for.
Any growth past the baseline is a regression; a Postgres-tier rebuild would
take the baseline to zero, and lowering it then is the point.

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
