# clearinghouse-sync-powermap

Portable Power Map (PM) sync engine for CannObserv sibling services.

This package owns the **mechanism** of syncing canonical entities to/from Power
Map; it knows nothing about Washington, the legislative domain, or which
concrete tables a deployment carries. A sibling service supplies a list of
`EntityDescriptor`s wiring its own tables to PM endpoints, and the `SyncEngine`
does the rest.

The seam test: `SyncEngine` is readable without knowing usa-wa exists. Keep it
that way — no `usa_wa_*` / `clearinghouse_domain_*` imports belong here.

See `docs/specs/2026-06-02-power-map-sync-sidecar-design.md` in the usa-wa repo
for the design.

## Surface

- `EntityDescriptor` — the per-entity contract a sibling implements.
- `OutboxEntry` / `SyncState` — durable delivery ledger + feed cursor (schema `sync`).
- `SyncEngine` — changes-feed loop, full-reconcile backstop, outbox worker, LWW reconciler.
- `PowerMapClient` — thin wrapper over the generated PM client (auth, base URL).
