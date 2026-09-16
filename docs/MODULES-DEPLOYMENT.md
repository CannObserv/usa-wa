# Modules — Layer 4 deployment

`packages/usa-wa-api/` (the FastAPI deployment) and the repo-root directories.
The Layer-1/2 primitives it builds on are in
[MODULES-FRAMEWORK.md](MODULES-FRAMEWORK.md).

**Layer 4 has one package.** It had two until usa-wa#314 retired the Power Map
sync stack — `usa-wa-sync-powermap` (the sidecar daemon, its entity descriptors
and eleven one-shot producer CLIs), the portable engine it bound
(`clearinghouse-sync-powermap`) and the generated client under it
(`powermap-client`). PM now pulls the published datasets nightly instead of
being pushed to, so the whole producer path — outbox, descriptors, reconcilers,
the LWW no-op gate — went with it, along with `MODULES-SYNC-ENGINE.md`,
`MODULES-SYNC-PRODUCERS.md`, `COMMANDS-SYNC.md` and `LWW-NOOP-GATE.md`. Read the
replatform decision in
[docs/specs/2026-09-02-dataset-publication-replatform-design.md](specs/2026-09-02-dataset-publication-replatform-design.md)
and the publication contract in [PIPELINE.md](PIPELINE.md).

```
  usa-wa-api/                         — Layer 4: WA deployment (FastAPI + MCP + REST)
    src/usa_wa_api/api/               — the HTTP surface
      main.py         — App factory, lifespan, router registration
      deps.py         — FastAPI dependencies (DB session). The `X-Operator-Token` gate retired at #313 with the one route it guarded
      datasets.py     — **the published-dataset surface (#311)**: `GET /datasets/{path}` serves the publisher's output tree (`catalog.json`, `<name>/<version>/data.csv|datapackage.json`) off `USA_WA_DATASETS_ROOT`, resolved per request and traversal-guarded — an unpublished box is a 404, not an error · `GET /health/datasets` is the pipeline's ops probe
      serving.py      — `GET /health/serving` (#313): did THIS deployment load what the pipeline published? The sibling probe to `/health/datasets`, and a version comparison rather than a row count, since an unchanged count is the normal case for this corpus. Not to be confused with the `serving/` package below
    src/usa_wa_api/api/v1/            — **read-only product surface (#184)**: 13 GET routes
      schemas.py      — the published models; `ULIDStr` rejects the UUID-hex form PM 404s on
      pagination.py   — keyset `Page[T]`, max 200, no total count
      ops.py          — #178 ledger, #180 coverage, provenance — the consumer those tables shipped without; an empty answer is a 200, since it *is* the finding
      products.py     — persons/orgs/roles/assignments off the `serving` schema since #313 — assignments being the span route, ONTOLOGY.md § 2, addressed by their 4-part span key rather than a ULID
                        Contracts + inventory: **[API.md](API.md)**, pinned live by `tests/test_v1_contract.py`
    src/usa_wa_api/serving/           — the #313 serving tier. A SIBLING of `api/`, not a child
      schema.py       — the disposable `serving` projection, its own MetaData so alembic never sees it
      load.py         — catalog-driven loader — digest + row-count + header + contract verified before a single write, one transaction, replacement not merge
    src/usa_wa_api/cli/               — operator CLIs, thin wrappers over the API's service functions. Indexed by name in [COMMANDS.md](COMMANDS.md)
    tests/            — API tests; conftest adds the AsyncClient over the root db_session
alembic/              — single alembic root; env.py imports clearinghouse_core.models.Base
conftest.py           — DB-free test base: prod-DSN guard (CR #191), `db` auto-marker (#185)
conftest_db.py        — the `db` tier's fixtures (test_engine/db_session); fails lazily
conftest_coverage.py  — the second coverage profile: the #198 unit-tier floor and the #216 integration exemption, both applied here rather than by a flag
docs/specs/           — Architecture specs (source of truth for design decisions)
docs/plans/           — Per-phase implementation plans
docs/research/        — Discovery outputs (Archiver/Watcher contracts, multi-state IA delta)
docs/                 — Reference docs (COMMANDS, SKILLS)
deploy/               — Systemd unit + deployment config. Service table: [DEPLOYMENT.md](DEPLOYMENT.md)
scripts/              — the seven operational shell entrypoints (migrate, dbt-gate, pipeline-nightly, notify-failure, verify-units, assert-main-checkout, pre-ship), `context_manifest_drift.py` behind the session hook, and `scripts/tests/` — the repo-level guards that pin these docs, the systemd units and the workspace registries. CLIs by name: [COMMANDS.md](COMMANDS.md)
.github/              — one GitHub Actions workflow, the weekly context cadence. It is the only scheduled job not on this VM: [DEPLOYMENT.md](DEPLOYMENT.md) § Scheduled work outside systemd
.claude/              — Claude Code settings, hooks and the skill symlinks
.skills/              — the skill knobs (context budgets, the ship gate's path list and advice) and `doctor.sh`
skills/               — skills discovered by agentskills.io, symlinks into the vendor trees
skills-vendor/        — the skill submodules themselves
```

The three `.skills`/`skills` trees and `.claude/` are described in
[SKILLS.md](SKILLS.md) — the symlink layout, the refresh procedure and what each
knob does.

The repo-root half of this tree and the `src/usa_wa_api/` half are both pinned
by `scripts/tests/test_docs_module_tree_drift.py` (#373). Five checks, all
against `git ls-files`: every tracked root directory is an entry, every
subpackage is placed by its full path (`src/usa_wa_api/serving/`, not a bare
`serving/`), every module in the package root or a subpackage is an entry,
every root-level `conftest*.py` is an entry, and this file carries **exactly
one** fenced block — a second would leave the guard unable to tell which one it
should be pinning. An entry is the first token on its line, so the separator
after it is free-form.

The prose beside each entry is not pinned — only the inventory. Before #373 the
tree drew `serving/` as a child of `api/`, omitted `datasets.py`, `serving.py`,
`cli/`, `conftest_coverage.py` and six of the ten root directories, and nothing
reported it.
