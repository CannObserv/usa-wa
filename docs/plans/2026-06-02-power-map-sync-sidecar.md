---
title: Power Map sync sidecar — implementation
date: 2026-06-02
status: draft
---

# Power Map sync sidecar — implementation

## Problem

usa-wa holds jurisdiction cache rows (PM-authoritative) and produces identity + entity-event records (usa-wa-authoritative, PM-archival), but nothing moves data to/from Power Map. The design ([`docs/specs/2026-06-02-power-map-sync-sidecar-design.md`](../specs/2026-06-02-power-map-sync-sidecar-design.md), issue [#4](https://github.com/CannObserv/usa-wa/issues/4)) settles the architecture; this plan sequences the build. The first increment must ship read-flow for all PM-served types plus a live jurisdiction write-flow, while leaving roles/assignments/entity-events write paths dormant pending PM endpoints — without coupling adapter ingestion to the sidecar.

## Approach

Build in dependency order, TDD throughout: (1) schema migration first (anchor standardization, `entity_events`, `sync` schema, outbox + sync_state) so models exist to test against; (2) the portable `clearinghouse-sync-powermap` engine package with zero usa-wa imports, unit-tested against fake descriptors + mocked PM; (3) the `usa-wa-sync-powermap` binding package with the 6 concrete descriptors, daemon entrypoint, and systemd unit; (4) wire the live jurisdiction round-trip and verify end-to-end against a `respx`-mocked PM plus an integration round-trip on real Postgres; (5) file the PM coordination issues. Each layer is independently verifiable before the next depends on it.

## Tradeoffs / alternatives

- **Engine inside `clearinghouse-core` instead of a new package** — rejected: the design's portability seam (sibling services reuse the engine) requires zero usa-wa/domain imports; folding into core blurs that boundary. Already decided in brainstorm.
- **Build all 6 write paths now behind a flag** — rejected: roles/assignments/entity-events PM endpoints don't exist yet; building untestable-against-real-PM code now adds risk for no shippable value. Descriptors register inert (`write_enabled=false`).
- **PM client hand-rolled vs generated** — rejected hand-rolling: `project_sidecar_sync_pattern` mandates `openapi-python-client` generation for forward-compat with PM's eventual SDK.
- **Cron one-shot vs daemon** — rejected one-shot: user chose process model B (long-running daemon) for write responsiveness.

## Steps

1. **Migration + model changes.** Add `clearinghouse_core.db`-style models: rename `powermap_person_id→pm_person_id` / `powermap_organization_id→pm_organization_id` in `identity.py`; add `pm_role_id`/`pm_assignment_id`; add `canonical.entity_events` table (+ `pm_entity_event_id`); add `sync` schema with `powermap_outbox` + `powermap_sync_state` models (in the new engine package, side-effect-imported into `Base.metadata`). Autogenerate one Alembic revision. Verify: `alembic upgrade head` clean on test DB; existing tests green after rename. (TDD: failing model/round-trip tests first.)
2. **Scaffold `clearinghouse-sync-powermap` package.** uv workspace member; deps = `clearinghouse-core` + generated PM client placeholder. Define `EntityDescriptor` (Protocol/dataclass), `OutboxEntry`/`SyncState` models, exceptions. Verify: package imports, `EntityDescriptor` contract unit-tested with a fake descriptor.
3. **Engine core — outbox worker + disposition + backoff.** `SyncEngine` write path: poll outbox, re-read row, `to_observation()`, POST, handle `AUTO_ATTACHED`/`NEW`/`REJECTED`/transient. Verify: unit tests for the state machine, LWW tie-break, exponential backoff — fake descriptor, mocked PM, zero usa-wa deps.
4. **Engine core — read path.** Changes-feed loop (cursor in `sync_state`) + periodic full-reconcile backstop + LWW reconcile + un-anchored sweep enqueue. Verify: unit tests with `respx`-mocked feed/read responses; cursor persistence; LWW enqueues `UPDATE`.
5. **PM client generation.** Generate from PM `/openapi.json` via `openapi-python-client`; confirm auth header (`X-API-Key` vs `Bearer`) and changes-feed/observation shapes; wrap in `PowerMapClient`. Verify: client builds; `PowerMapClient` smoke test against `respx`.
6. **Scaffold `usa-wa-sync-powermap` binding.** 6 concrete `EntityDescriptor`s (jurisdictions + canonical.{persons,organizations,roles,assignments,entity_events}); `to_observation`/`upsert_from_pm`/`last_updated` per type; `write_enabled=true` only for jurisdictions (persons/orgs per appetite). Scope config (`slug_prefix=usa-wa`). Verify: descriptor unit tests per entity (payload mapping, anchor set, LWW field).
7. **Daemon entrypoint + systemd unit.** `__main__` async daemon: `configure_logging()` once, build descriptors, run `SyncEngine` loops concurrently. `deploy/usa-wa-sync-powermap.service` (own lifecycle, env loading). Verify: daemon starts/stops cleanly on dev; unit file lints.
8. **End-to-end jurisdiction round-trip.** Integration test (`-m integration`, real Postgres, savepointed): boot → feed read → reconcile → un-anchored sweep → observe → anchor write-back, against `respx`-mocked PM with all three dispositions. Verify: green integration test; jurisdiction row gets `pm_jurisdiction_id` populated.
9. **File PM coordination issues** (§7 of the spec): roles/assignments observation endpoints; entity_events changes-feed coverage + dedicated observation endpoint; confirm roles/assignments/entity_events on `/changes`; auth confirmation. Verify: issues opened on CannObserv/power-map, cross-linked from #4.

## Open questions / risks

- **PM OpenAPI specifics unknown until step 5** — exact field names for identity/event records, changes-feed envelope shape, and auth header are pinned when the client is generated. Steps 4/6 may need minor mapper adjustments once the real schema lands; descriptors localize that churn.
- **`entity_events` write path** depends on PM's choice (dedicated endpoint vs embed-in-person/org). Ships dormant either way; no blocker for the MVP increment.
- **Migration rename blast radius** — `powermap_*_id` readers are minimal today (identity sync not live), but confirm no API/serializer references the old names before renaming.
