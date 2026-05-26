# Watcher integration contract — research note

- **Date:** 2026-05-26
- **Status:** P2 evaluation input (no decision yet)
- **Audience:** usa-wa engineers deciding whether to keep APScheduler in-process or delegate refresh orchestration to `CannObserv/watcher`
- **Watcher repo state:** active, public, MIT, ~2.8 MB, 17 open issues, last push 2026-05-25. Phase 5 (#156) cutover to v2 done; Phase 6 (#160) InfoItem-first reshape done; current work is dashboard/UX polish.

## TL;DR

Watcher is **not** a generic "schedule fetcher" service. It is a tightly-coupled link in a three-service pipeline (`watcher` → `archiver` → `notifier`) whose data model assumes every monitored URL is already registered as an Archiver `InfoItem` with a primary `InfoSpec` document. Delegating usa-wa scheduling to Watcher requires usa-wa to also adopt Archiver as the system of record for source bytes and Notifier for outbound dispatch — i.e., the whole CannObserv pipeline, not just the scheduler. **Recommendation: keep APScheduler indefinitely unless usa-wa is independently migrating to Archiver as primary-source store. If/when that migration happens, Watcher delegation comes nearly for free.**

## Scheduling capability

### What watcher supports

- **Interval-based, human-readable strings:** `30s`, `15m`, `6h`, `1d`. Implemented in `src/core/scheduler.py::parse_interval` (regex `^(\d+)([smhd])$`). Default is daily.
- **No cron expressions for source schedules.** Cron is only used internally by Procrastinate's `@bp.periodic` to drive the 1-minute `schedule_tick` and `drain_pending_source_revisions` jobs.
- **Per-source configurability:** schedule lives on a `WatchedItem.default_schedule_config` JSONB column; individual child `Watch`es inherit and can override (live-inheritance: Watch override → WatchedItem default → system default; see `src/core/watches/resolution.py`).
- **Temporal profiles** (`src/api/schemas/temporal_profile.py`) add dynamic cadence on top of the base interval:
  - `event` / `deadline` profiles — anchored on a reference date; rules of the form `{days_before: int, interval: str}` let you crank cadence as a deadline approaches. Tightest matching rule wins.
  - `seasonal` profiles — active only between `date_range_start` and `date_range_end`.
  - When any profile is active, the **shortest** interval across all active profiles overrides the base.
  - `post_action` (`deactivate` / `archive` / `reduce_frequency`) fires after the reference date or end-of-range, mutating the Watch or its parent WatchedItem.
- **No webhook-triggered schedules.** Watcher does not expose a "refresh now" hook that an external system can call to provoke a fetch outside the schedule. There is no on-demand pull API for triggering an immediate check from outside.

### Scheduling tick model

- `schedule_tick` is a Procrastinate `@bp.periodic(cron="* * * * *")` task. **Minimum cadence is 1 minute** — cron's floor. Sub-minute is explicitly out of scope (acknowledged in `docs/plans/2026-05-04-watcher-phase2c-cutover-plan.md`).
- On each tick, watcher loads every active+non-archived WatchedItem, computes whether any child Watch is due via `compute_next_check(schedule_config, last_checked_at, now, profiles)`, and enqueues a `check_watched_item` job per due item.
- Domain-level rate limiting + 429 backoff (see `src/core/rate_limiter.py` and `_persist_backoff` in `src/workers/pipeline.py`) sits between the schedule decision and the actual HTTP fetch.

### Implications for usa-wa

- usa-wa's spec calls for "daily refresh + on-demand pulls." Watcher covers the daily piece natively (`interval: "1d"`). On-demand pulls are **not** supported as a first-class API. The closest workaround is to PATCH a Watch's `last_checked_at` to NULL, which would make it due on the next tick — but that's not an intended contract.
- Cron-style scheduling (e.g. "every Monday at 09:00 PT") is **not** expressible in watcher today. usa-wa would lose this capability if it migrated.
- The 1-minute schedule_tick floor is fine for daily refreshes but limits how fine-grained reactive cadence can get during legislative session crunches.

## Push / pull semantics

Watcher does **not** push fetched bytes to subscribers. Its design is "fetch, fingerprint, hand off to Archiver, then optionally notify humans via Notifier."

### What watcher emits

1. **POST to Archiver via `archiver-client` SDK** (the primary outbound). Every detected change becomes a `SourceRevision` POSTed to Archiver. If Archiver is down, watcher persists to a local `pending_source_revisions` outbox and drains via `drain_pending_source_revisions` (periodic, 1-min cron, exponential backoff capped at 1 hour, max 10 attempts). See `src/workers/source_revisions_drain.py` and `src/core/sources/outbox.py`.
2. **Redis Stream `info.changes`** (envelope `schema_version: 2`, partitioned by `info_item_id`). This is mentioned in the README as the intended downstream-consumer path, but **no actual external consumer is wired up in the current repo** — Archive consumption is described as "Phase 3+" and not yet implemented at the watcher boundary. The stream is the only programmatic push channel exposed today.
3. **Notifications dispatched via the Notifier service** (`NotifierClient` SDK, HTTP). Templated per WatchedItem; geared toward human channels (Apprise URLs, email). Not a clean programmatic webhook for another service.

### What watcher does NOT emit

- No webhook callback to the entity that registered the Watch. There is no `webhook_url` field on Watch or WatchedItem.
- No published `clients/` SDK. Compare archiver (`/clients/python/`) and notifier (which watcher consumes as `notifier-client` from `ssh://git@github-notifier/CannObserv/notifier.git#clients/python`). To integrate, usa-wa would have to hand-roll an OpenAPI client (or use `openapi-python-client`, which watcher already has in dev deps) against watcher's REST surface.
- No message-queue topic beyond the Redis Stream. No AMQP, no Kafka, no SNS/SQS.

### What "subscribing" to watcher would look like for usa-wa

Given current code, usa-wa would need to:

1. Register WashingtonState `InfoItem`s in **Archiver** (not watcher) with primary `InfoSpec` documents describing URL + extraction config.
2. POST a `WatchedItem` to watcher referencing those `info_item_id`s, with `default_schedule_config = {"interval": "1d"}`.
3. To learn about changes, **either**:
   - Poll Archiver for new `SourceRevision`s per `info_source_id` (the durable path), **or**
   - Stand up a Redis Stream consumer on `info.changes`, dedupe by `change_id`, react to envelopes (low-latency but coupled to watcher's Redis).
4. Have Archiver hold the bytes; usa-wa fetches the canonical revision from Archiver when it wants to (re-)ingest.

This means **watcher delegation implies Archiver adoption**. There is no path where usa-wa uses watcher for scheduling but keeps its own primary-source byte cache — watcher's emit-side machinery is built around Archiver as the system of record.

## Architectural fit

| Concern | usa-wa today (APScheduler) | usa-wa via watcher |
|---|---|---|
| Where schedules live | usa-wa Postgres (Source table + APScheduler in-process) | watcher Postgres (`WatchedItem.default_schedule_config`) |
| Where bytes live | usa-wa time-bounded cache | Archiver (long-term) + watcher scratch (`/var/cache/watcher/scratch`, TTL 600 s) |
| Where change notifications surface | usa-wa internal | Redis Stream `info.changes` + per-WatchedItem notifier templates |
| Cron-style schedules | Native (APScheduler `CronTrigger`) | Not supported (interval strings only) |
| On-demand refresh | Native (call `runner.refresh(source_id)`) | No first-class API |
| WA-Legislature SOAP fetcher | usa-wa adapter code | usa-wa would still own the fetcher; watcher only fetches via `fetcher.fetch(url)` with HTTP semantics. SOAP integration would either be a custom watcher fetcher (cross-repo PR) or stay in usa-wa with watcher just doing the schedule. |
| Operational blast radius | usa-wa-only outage | Outage in any of {watcher, archiver, redis, notifier} affects refresh |

The mismatch on **on-demand pulls** and **SOAP fetching** are the load-bearing technical gaps; the rest are mostly preference / surface area.

## Recommendation

**Keep APScheduler in usa-wa indefinitely as the default.** Reasons:

1. usa-wa is the read layer of the cohort; its primary integrations are *upstream* (it consumes WA Legislature SOAP, RCW, PDC) rather than downstream of watcher. Watcher is built for HTTP-fetch monitoring of "InfoItems" registered in Archiver — usa-wa's primary sources don't naturally fit that shape (SOAP envelopes, paginated legislative APIs, dated cutoffs).
2. usa-wa's on-demand-pull requirement has no clean watcher analog. APScheduler trivially supports `scheduler.modify_job(next_run_time=now())`; watcher would require either a Watch-PATCH hack or a new endpoint contribution.
3. The "delegate to watcher" win is mostly **operational** (one scheduler to monitor across the cohort), not architectural. usa-wa's APScheduler in-process is ~50 lines of code and zero new infrastructure.
4. Migration cost is steep: usa-wa would need to adopt Archiver as the canonical byte store *and* either Redis-Stream-consume `info.changes` or build an Archiver poller. That's a multi-month rework with no immediate user-facing benefit.

**Migrate only if all of the following gates trip.** Treat this as the P2 evaluation checklist.

### P2 evaluation criteria — when to migrate from APScheduler to watcher delegation

Migrate to watcher delegation when **all** of these are true:

- [ ] **Archiver adoption is already decided** for usa-wa — i.e., we've committed to making Archiver the system of record for primary-source bytes (not just usa-wa's time-bounded cache). Without this, watcher delegation is more expensive than the status quo.
- [ ] **Source set has stabilized on HTTP-fetchable shapes.** WA Legislature SOAP and any custom-protocol fetchers have been wrapped (or shimmed) as plain HTTP endpoints that watcher's `Fetcher` protocol can consume — or we've contributed the fetcher upstream to watcher.
- [ ] **Cron-style cadence is no longer required.** Either usa-wa's scheduling needs collapse to "every N hours/days," or we contribute cron-expression support to watcher's `schedule_config` schema upstream.
- [ ] **On-demand refresh has a contract.** Either watcher exposes a `POST /watched-items/{id}/refresh-now` endpoint (we contribute it), or usa-wa's product no longer needs synchronous re-fetch.
- [ ] **Operational scale tips the cost balance.** Concretely: usa-wa is running ≥ ~50 distinct Source schedules, AND/OR has had two or more APScheduler-related incidents (missed jobs after restart, in-process scheduler lock-up, etc.), AND/OR adding usa-wa-specific scheduler-ops tooling looks more expensive than learning watcher's.
- [ ] **Cohort-wide observability is desired.** "One pane of glass" for refresh latency / health across usa-wa + sibling services is a stated requirement.
- [ ] **Watcher exposes a stable Python client.** A `clients/python/` published under `CannObserv/watcher` exists with the same maturity as `archiver-client` (>= 1.0, semver, pinned to a release tag). Hand-rolling an OpenAPI client against an undocumented contract is a footgun.

If only some of these are true, the migration is net-negative. The single most load-bearing gate is the **Archiver adoption** gate: without it, the rest is moot.

## Blocking unknowns

1. **No external SDK exists.** Watcher does not ship a `clients/python/`; integration would be raw HTTP against the OpenAPI surface, with no breaking-change discipline. Need to confirm with the watcher team whether a client is planned, and whether they'd accept a breaking-change policy for downstream consumers.
2. **`info.changes` Redis Stream is described but has no documented external consumer contract.** Envelope `schema_version: 2` is mentioned in the README and in the Phase 2c plan, but there is no schema file in the repo (looked for under `docs/`, `src/core/sources/`, schemas). Need a stable schema commitment before any consumer can be built against it.
3. **Archiver is a hard dependency.** usa-wa would need its own Archiver research note (separate task) before this migration could even be scoped. Without understanding Archiver's onboarding cost, the migration estimate is incomplete.
4. **SOAP / custom-protocol fetcher path is undefined.** Watcher's `Fetcher` protocol (in `src/core/fetchers/`) is HTTP-only based on the `pyproject.toml` dependency surface (`httpx`, `playwright` optional). Whether watcher would accept a SOAP fetcher upstream, or expects each consumer to shim, is unclear. WA Legislature uses SOAP; this is non-optional.
5. **No multi-tenancy model documented.** Watcher's API has `api_key` (issue #100 — "API key management and public API access") but it is unclear whether watcher is operated as multi-tenant or whether each consumer service runs its own watcher deployment. If the former, usa-wa coexists with `notifier`-style tenants; if the latter, usa-wa runs its own watcher VM and most of the "one scheduler for the cohort" argument evaporates.
6. **On-demand pull is unsupported and unscoped.** No issue or plan in the watcher backlog addresses a "refresh now" trigger. Filing one and waiting for it to land is part of the migration cost — non-trivial because of how `schedule_tick` is structured.

## Sources consulted

- `https://github.com/CannObserv/watcher` (default branch `main`, commit at 2026-05-25)
- `README.md`, `AGENTS.md`, `CHANGELOG.md`, `pyproject.toml`
- `src/core/scheduler.py` — interval parsing + temporal-profile evaluation
- `src/workers/__init__.py`, `src/workers/tasks.py` (`check_watched_item`, `schedule_tick`)
- `src/workers/pipeline.py`, `src/workers/source_revisions_drain.py`
- `src/core/sources/outbox.py`, `src/core/sources/revision_cache.py`
- `src/api/routes/watches.py`, `src/api/routes/watched_items.py`
- `src/api/schemas/{watch,watched_item,temporal_profile}.py`
- `docs/DEPLOYMENT.md`
- `docs/plans/2026-05-04-watcher-phase2c-cutover-plan.md` (change-bus contract)
- Issue backlog #1–#176 (titles only)
- `/home/exedev/usa-wa/docs/specs/2026-05-25-usa-wa-mvp-design.md` (the question)
