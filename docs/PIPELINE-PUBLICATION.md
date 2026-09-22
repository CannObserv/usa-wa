# The publication contract

What `usa-wa` publishes and what each field promises: the versioned dataset
tree, the catalog and its heartbeat, per-dataset schema versions, the tiers, and
the CSV dialect. Written for a subscriber as much as for this repo — power-map
builds against it. Pipeline layout, commands and the models upstream of
publication: [`PIPELINE.md`](PIPELINE.md).

## Publication (#311)

`python -m usa_wa_pipeline.publish` materializes each dataset in
`publish.PUBLISHED_DATASETS` (deliberate config — publishing is a decision;
lineage comes from the dbt manifest) as an
immutable `USA_WA_DATASETS_ROOT/<name>/<version>/data.csv + datapackage.json`
and flips `catalog.json` last (tmp+rename both — a crash leaves unlisted
orphans, never a listed partial). Skip-if-unchanged: no version churn on a
quiet day. Producer-side gates: a missing table or a row shrink beyond
`--max-shrink` (default 10%) refuses the whole run with nothing minted —
retraction=absence means a degraded build must never ship as mass retraction.
The API serves the tree at `/datasets/*` with `/health/datasets` as the
publication probe. The nightly systemd chain (`scripts/pipeline-nightly.sh`,
`usa-wa-pipeline.timer`, daily 08:00 UTC) runs harvests → dbt build →
registrar → publish → serving load → parity probes
(`parity_citations` last); any counted failure exits 1 so `OnFailure=` emails
the operator.

Three tiers, each answering a different question about who may depend on it.
`tier` is per-dataset in the catalog and `/health/datasets` returns it, so the
tier is published rather than inferred:

| Tier | Datasets | Contract |
|---|---|---|
| `staging` | `stg_*` | The triage/lineage surface — one row per wire, source coordinates attached |
| `conformed` | `persons`, `organizations`, `roles`, `assignments`, the crosswalks | The subscriber contract; schema-stable, semver'd |
| `internal` | `citations` | Published bytes, no stability promise; its columns follow the API, not consumers |

A fourth tier, `cutover`, held one dataset — `pm_anchors`, the PM crosswalk
seed (#312) delivered as a dataset (#354, power-map#495) — and **#314 retired
both**. power-map#525 re-keyed its assignment crosswalk off the Postgres ULIDs
that dataset carried and onto `assignments.span_key`, and reported it needs no
further seed, so the producer stopped asserting the mapping. Delisting is the
whole retraction: `catalog.json` is rebuilt from `PUBLISHED_DATASETS` every
run, so the entry stopped appearing, while the version dirs already minted stay
on disk and keep answering at their URLs. `PUBLISHED_DATASETS` pins the tier
empty (`test_the_cutover_tier_is_empty`), which is also what keeps the column
drop unblocked: the publisher refuses a run whose table is missing, so a
lingering entry would have wedged the nightly publish for every other dataset
the day the `pm_*` columns went.

That removal took the old catalog-wide `SCHEMA_VERSION` to **2.0.0** — and
produced the defect #385 filed. See § Schema versions are per-dataset below.

## Schema versions are per-dataset (#385)

Each entry in `publish.PUBLISHED_DATASETS` carries its own version history — a
tuple of `ContractRelease(version, columns, note)`. `schema_version` in that
dataset's `datapackage.json` and catalog entry is the latest release's version,
and it moves **only when that dataset's contract moves**.

It used to be one module constant, `SCHEMA_VERSION`, stamped onto whatever
dataset minted next. Carry-forward (an unchanged dataset keeps its prior catalog
entry) plus skip-if-unchanged meant the corpus spanned versions indefinitely —
sixteen datasets across seven values — so a dataset's major encoded *when it last
minted*, not what its shape was. power-map's puller pins a major and refused
`persons` and `person_crosswalk` over #314's 2.0.0 bump, whose whole content was
`pm_anchors` leaving the catalog; neither dataset's shape had moved by a field.
A consumer correctly implementing semver was refusing a dataset over a bump that
asserted nothing about it.

**Who bumps.** Whoever changes the contract, at the moment the drift test goes
red. Never edit a standing release: append a new `ContractRelease` with the next
version and the columns the build now produces. Additive = minor, rename or
removal = major, per dataset.

**Two gates, catching different mistakes.**

| Gate | Where | Catches |
|---|---|---|
| `test_every_declared_contract_matches_the_build` | unit tier, hermetic dbt build | a model's columns moved and its entry did not |
| the publisher's own contract gate | nightly publish, against what was last published | a declaration edited in place, and a contract that moves with no code change at all |

The declaration records column **names and order**, not types: a hermetic build
reads empty sources and duckdb types an all-NULL column `INTEGER`, so types are
unknowable in a database-free tier and declaring them would declare a fiction.
Names and order were verified identical between the hermetic and live builds for
all sixteen datasets (2026-09-18). Types are covered by the publish-time gate,
which compares the full `contract_hash` — the dataset's name, tier, dialect and
its ordered fields *with* types — against what was last published, both sides
read from a real build. Lineage is deliberately **not** in the fingerprint:
`derived_from` comes from the dbt manifest, so folding it in would churn every
downstream dataset's version when an intermediate model is refactored.

The gate is enforced one way — a contract change requires a bump — and not as an
"if and only if". The published fields are `{name, type}` with no descriptions,
so a semantics-only change (a column re-derived, its meaning shifted, its type
unmoved) has no fingerprint to move; demanding the iff would forbid the honest
major. A bump with no shape change is allowed, and re-mints.

**`contract_hash` ships beside `schema_version`** in both the datapackage and
the catalog entry. It answers the question a consumer actually asks — "is this
the shape I validated?" — and unlike a major it cannot lie. The semver stays as
the cheap human-readable gate.

**The transition froze each dataset in place.** Every dataset kept the number it
was already publishing on 2026-09-18 (`persons` 2.0.0, `organizations` 1.0.0,
the six other `stg_*` 1.4.0, and so on), and moves only on its own contract from
there. Resetting to a clean 1.0.0 would have *downgraded* four datasets on the
wire, refusing for the consumer who had just re-pinned to major 2; renumbering
everything up to 2.0.0 would have asserted a major change for twelve datasets
that had none, and re-minted them to say it. The starting values are therefore
arbitrary and harmless: a major is only ever compared within a dataset.
`test_the_transition_froze_each_dataset_at_the_version_it_was_publishing`
records them, because nothing else joins the pre-cutover archive to the numbers
published after it.

**Each published assignment carries its `span_key`** (usa-wa#370,
power-map#490), and that outlives the crosswalk. A published assignment has no
id of its own — #302 gave assignments deterministic structural keys and no
registry — so PM's applier, which measures retraction-as-absence in the
dataset's own key space, had nothing for an assignment anchor to be absent
*from*: the crosswalk's `usa_wa_id` appeared in no published column, and
crosswalk- vs dataset-membership overlapped on **0 of 8,777** assignment rows
(roles coincide at 312/312, which is why roles worked as built and assignments
did not). The column is serialized once by
[`conformed/span_key.py`](../packages/usa-wa-pipeline/src/usa_wa_pipeline/conformed/span_key.py).
It is now the *only* handle PM holds on an assignment row, which is the point:
the crosswalk was the cutover artifact and the key is the thing that replaced
it.

## The catalog's heartbeat (#386)

| Field | Where | Means | No-mint run |
|---|---|---|---|
| `checked_at` | catalog top level | the publisher completed a run | **advances** |
| `stale_after` | catalog top level | the deadline for the next `checked_at` | moves to the next run's |
| `generated_at` | each entry + its `datapackage.json` | that version's mint time | carried forward |

`checked_at` is the producer's liveness signal — a quiet day and a dead pipeline
differ only here. Scope is the **publisher**, not the chain: a contained harvest
failure, an SOS `ACCEPTED_OUTAGES` outage, registrar conflicts and any failure
after publish all leave it **fresh**; a `dbt build` failure, a publish
refusal/crash, or a unit that never runs leave it **stale**. Fresh = "published
over a successful build", not "every source is fresh".

Half a consumer's check: `now > stale_after` = the producer is behind the clock. Built from ≠ the entry's `latest_version` = the consumer is
behind the producer (power-map#535 — the incident behind this issue, during which
`checked_at` was fresh).

`stale_after` = the next scheduled run after `checked_at` (08:00 UTC) +
`publish.PUBLISH_GRACE` (45 min: 5-min jitter + 30-min `TimeoutStartSec=` +
margin). A deadline, not a duration: a 26h `stale_after_seconds` left a single
missed night invisible to power-map's 09:00 pull (24h55m old, then repaired by
the next run before the pull after). `scripts/tests/test_catalog_staleness_threshold.py`
pins schedule and grace to the unit files.

Per-entry `generated_at` is mint time, not data-change time: a contract-only
re-mint moves it over identical bytes (2026-09-19, #385's `contract_hash`:
sixteen re-mints, `persons` sha256 unchanged). `hash` says whether data changed.
A pre-#386 catalog has a top-level `generated_at` and no deadline;
`/health/datasets` reads it as `checked_at` with `stale: null`.

## The serialisation is part of the contract (#357)

The bytes are the product: a `hash` only means something once what it hashes is
pinned. Every published `data.csv` is written by duckdb `COPY` and obeys one
dialect, now declared in each resource's `dialect` (Frictionless) as well as
here, so a strict parser does not have to sniff and a second producer has
something to conform to:

| Property | Value |
|---|---|
| encoding | UTF-8 |
| delimiter | `,` |
| line terminator | `\n` — **not** CRLF |
| header | present, matching `schema.fields` in order |
| quoting | `"`, doubled to escape (`""`) |
| NULL | bare empty field; an empty *string* is `""`, so the two stay distinct |
| row order | `order by all` — every column, left to right, ascending |

Row order is load-bearing: it is what makes skip-if-unchanged mean "nothing
moved" rather than "duckdb returned rows differently". It was load-bearing a
second way while `pm_anchors` had two writers — row order and line endings
together were why the local artifact and the published one agreed byte for
byte, `csv`'s default excel dialect writing CRLF being one byte per row, 12,462
bytes and a second conflicting digest for identical content (#354). #357 made
one writer per dataset the rule and #314 retired that dataset, so the second
reason is history; the first still holds every night.

`SCHEMA_VERSION` 1.6.0 added `dialect`, and under the old carry-forward rule a
bump reached a dataset only when it next minted — so version dirs published
before 1.6.0 keep the datapackage they shipped with, and this document is the
declaration for them. #385 closed that gap for everything after it: a dataset
now re-mints when its contract changes even if its bytes did not, so a
metadata-only change reaches every dataset on the next run instead of waiting on
unrelated data to move. `test_published_bytes_obey_the_declared_dialect` parses every published CSV back with its own
declared dialect and checks the shape, so the table is enforced rather than
aspirational.

A dev/CI build with NO database must say so: `USA_WA_PIPELINE_HERMETIC=1`
(set by `scripts/dbt-gate.sh` and the dbt tests) is the only thing that lets
the conformed crosswalk models materialize empty — otherwise a missing
`DATABASE_URL` fails the build loudly (#302 CR: empty identity must never
publish with a green build).
