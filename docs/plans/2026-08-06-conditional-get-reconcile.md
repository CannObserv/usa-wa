---
title: Honor conditional GET / 304 on the reconcile fetch path (usa-wa#160)
date: 2026-08-06
status: implemented
---

> **Implementation note (2026-08-06).** All 6 steps shipped on `feat/160-conditional-get`.
> **`events_etag` was dropped during implementation:** PM's detail ETag covers child tables
> **including events** (the touch-cascade, power-map#385), so a detail `304` means the whole
> row — events included — is unchanged. `ConditionalGetState` stores only `detail_etag`, and
> the events-pagination open question is moot (no separate events validator). Requires a
> migration (`890c046c…`, new sync-schema table) → `usa-wa-migrate` on deploy.
---

# Honor conditional GET / 304 on the reconcile fetch path (usa-wa#160)

## Problem

The anchored-cohort reconcile re-fetches every produced row's **full** PM body by id
each pass (`_reconcile_anchored_cohort` → `descriptor.fetch_record` → `get_entity`,
plus each person's `/events`), even though ~99.9% are unchanged. Post-#159 this scan
is the *residual* backstop and Phase B widens it to weekly — but each pass is still
O(cohort) full-body reads. PM shipped conditional GET (power-map#385/#292): a strong
`ETag` on every entity + `/events` GET, honouring `If-None-Match` with `304 Not
Modified`. We committed (on #385) to send `If-None-Match` and honour `304`
unconditionally. Honouring it turns the reconcile's steady state into N cheap `304`s
+ full bodies only for genuinely-changed rows — cutting bandwidth, PM serialization,
and our apply/re-derive work (**not** request count; that's the version-manifest's
job, out of scope).

## Approach

Persist PM's ETag per anchored row in a **sync-schema sidecar table** keyed
`(entity_type, anchor_id)` (a `detail_etag` + an `events_etag`, mirroring the
`EnrichFingerprint`/`NonConvergenceState` per-row pattern — no per-entity-table
migration, one table). Thread an optional `if_none_match` validator through
`PowerMapClient.get_entity` / `list_entity_events` (inject via the generated client's
`with_headers({"If-None-Match": etag})`; read `resp.status_code == 304` +
`resp.headers["ETag"]`) and through `descriptor.fetch_record`, returning a small
`EntityFetch(not_modified, record, etag)` result. In `_reconcile_anchored_cohort`:
load the stored validator, pass it, and on `304` **short-circuit** — no body, no
`apply_record`, no re-enrich — just count it; on `200` apply as today and store the
fresh ETag. The events sub-resource carries its own ETag (with `limit`/`offset` baked
in per the #385 contract), stored + sent separately. Correctness rests on the #385
guarantee that the detail ETag covers child tables (the touch-cascade), so a `304`
genuinely means "nothing to heal." Surface a `conditional_get` hit tally on
`sidecar_cycle_summary`. Kill-switchable (`CONDITIONAL_GET_ENABLED`, default true) so
a suspected PM ETag bug can be disabled without a redeploy.

## Tradeoffs / alternatives

- **Per-entity-table `pm_etag` column** — rejected: 4 migrations + 4 model changes vs.
  one sidecar table; the ETag is sync-plumbing, not canonical entity data, so it
  belongs in the sync schema next to `EnrichFingerprint`.
- **Store only the detail ETag, re-fetch events unconditionally** — rejected: people
  are the expensive cohort (`/events` per person); skipping conditional GET there
  leaves most of the win on the table. The #385 events ETag is explicitly supported.
- **Apply conditional GET on the replay path too** — deferred: replay is O(items in
  window), tiny; the reconcile scan is the volume driver. Wire the store so replay
  *could* adopt it later, but scope this change to the reconcile.
- **Trust the stored ETag blindly** — n/a risk: a stale/wrong stored validator only
  ever costs a `200` we apply (idempotent under LWW). The failure mode is "no
  savings," never "missed update," so no correctness exposure.

## Steps

1. **ETag store.** Sync-schema model `ConditionalGetState` (`(entity_type, anchor_id)`
   unique; `detail_etag`, `events_etag` nullable) + alembic migration + grants entry.
   Unit-test the upsert/read.
2. **Client contract.** Add an optional `if_none_match` to `get_entity` /
   `list_entity_events` (Protocol in `client.py`), returning the ETag + a
   not-modified signal (an `EntityFetch`/`EventsFetch` result type). Implement in
   `GeneratedPowerMapClient` via `with_headers` + `resp.status_code == 304`/`resp.headers`.
   `FakeClient` gains preset ETag/304 behaviour. Wrapper test with `respx` asserting
   the header is sent and a 304 is surfaced.
3. **Descriptor seam.** `fetch_record` accepts + forwards the validator and returns the
   `EntityFetch` (person/org override threads the separate events validator). Keeps the
   `dict | None` contract available for non-conditional callers.
4. **Reconcile wiring.** `_reconcile_anchored_cohort` loads the stored validators,
   passes them, short-circuits on `304` (count, no apply/enrich), applies + stores the
   fresh ETag on `200`. A `not_found`/404 still routes to the existing heal path.
   Tests: 304 → no upsert + no outbox; 200 → applies + stores ETag; first run (no
   stored ETag) → unconditional 200 + stores.
5. **Observability + kill switch.** `conditional_get` hit/miss tally on
   `sidecar_cycle_summary`; `CONDITIONAL_GET_ENABLED` (SidecarSettings, default true).
6. **Docs.** AGENTS.md (reconcile bullet + env table), COMMANDS unaffected. Note the
   power-map#392 gap (endpoints still lacking conditional GET) is out of scope.

## Open questions / risks

- **Migration (unlike #159, this one has schema).** New sync-schema table → alembic
  revision + `grants.sql` entry + `usa-wa-migrate` on deploy. Confirm the sync schema
  name and that `env.py` picks the model up (it imports `Base` side-effect modules).
- **Generated-client header injection.** `with_headers` is the intended path; verify
  the vendored `AuthenticatedClient` exposes it and that `asyncio_detailed` surfaces
  `resp.headers` on a 304 (openapi-python-client does). Fallback: a raw httpx GET on
  the known read path. No PM client regen expected.
- **Events ETag pagination coupling.** The #385 events ETag bakes in `limit`/`offset`,
  so the stored validator is only valid for the *same* pagination — `list_entity_events`
  pages internally from offset 0; store/send the ETag for the **first** page only, or
  treat any `has_more` walk as a full (non-conditional) read. Decide in step 3.
- **Scope creep vs the manifest.** This does not reduce request count (per #385); if
  the weekly scan's request volume is still a concern after Phase B, that's the
  separate version-manifest ask, not this issue.
