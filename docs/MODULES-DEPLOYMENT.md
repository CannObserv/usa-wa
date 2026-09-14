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
    src/usa_wa_api/api/
      main.py         — App factory, lifespan, router registration
      deps.py         — FastAPI dependencies (DB session). The `X-Operator-Token` gate retired at #313 with the one route it guarded
      serving/        — the #313 serving tier: `schema.py` (the disposable `serving` projection, its own MetaData so alembic never sees it) · `load.py` (catalog-driven loader — digest + row-count + header + contract verified before a single write, one transaction, replacement not merge)
      v1/             — **read-only product surface (#184)**: 13 GET routes — `schemas.py` (the published models; `ULIDStr` rejects the UUID-hex form PM 404s on) · `pagination.py` (keyset `Page[T]`, max 200, no total count) · `ops.py` (#178 ledger, #180 coverage, provenance — the consumer those tables shipped without; an empty answer is a 200, since it *is* the finding) · `products.py` (persons/orgs/roles/assignments off the `serving` schema since #313 — assignments being the span route, ONTOLOGY.md § 2, addressed by their 4-part span key rather than a ULID). Contracts + inventory: **[API.md](API.md)**, pinned live by `tests/test_v1_contract.py`
    tests/            — API tests; conftest adds the AsyncClient over the root db_session
alembic/              — single alembic root; env.py imports clearinghouse_core.models.Base
conftest.py           — DB-free test base: prod-DSN guard (CR #191), `db` auto-marker (#185)
conftest_db.py        — the `db` tier's fixtures (test_engine/db_session); fails lazily
docs/specs/           — Architecture specs (source of truth for design decisions)
docs/plans/           — Per-phase implementation plans
docs/research/        — Discovery outputs (Archiver/Watcher contracts, multi-state IA delta)
docs/                 — Reference docs (COMMANDS, SKILLS)
deploy/               — Systemd unit + deployment config
```
