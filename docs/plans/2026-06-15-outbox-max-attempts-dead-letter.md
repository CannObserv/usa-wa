---
title: Outbox max-attempts dead-letter + stuck-retry visibility (#5)
date: 2026-06-15
status: draft
---

# Outbox max-attempts dead-letter + stuck-retry visibility

## Problem

`SyncEngine.drain_outbox` retries transient delivery failures with exponential
backoff but has no max-attempts cap ([engine.py:211-224](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/engine.py#L211-L224)).
A row failing for a long time (PM down for days, or a payload that only ever
raises a transport error) retries unbounded and stays `PENDING` at e.g.
`attempts=900`. The only terminal state today is `REJECTED` (PM said no
explicitly), and the operator backlog is documented as keying on `REJECTED`, so
a perpetually-retrying row is invisible. A second, related blind spot: the
deps-not-ready deferral ([engine.py:194-203](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/engine.py#L194-L203))
keeps a row `PENDING` without incrementing `attempts`, so a row whose parent is
permanently un-anchorable defers forever and is equally invisible. Worse, the
"operator backlog" surface the issue refers to does not actually exist yet —
nothing reads the outbox by status.

## Approach

Introduce a new terminal status `UNAVAILABLE` (distinct from `REJECTED`) for
entries that exhaust a configurable transport-failure cap, and surface the
backlog so stuck entries become visible. `UNAVAILABLE` is kept separate from
`REJECTED` because the cause and operator remedy differ: `REJECTED` means PM
rejected the payload (fix the data; do not blindly retry), whereas
`UNAVAILABLE` means PM was unreachable (the same payload will likely succeed
once PM recovers, so it is re-drivable). Centralize the "record a failed
attempt" logic in one helper so both the transient-exception branch and the
unexpected-disposition branch share the cap. Add a read-only backlog query on
the engine and expose it through a `/health/sync` endpoint plus a distinct
error log on the `PENDING → UNAVAILABLE` transition for immediate log-based
alerting. Provide an operator re-drive path to reset `UNAVAILABLE → PENDING`
once PM is healthy. The deps-not-ready forever-defer blind spot is scoped out
of this plan (tracked as a follow-up) to keep this change focused.

## Tradeoffs / alternatives

- **Reuse `REJECTED` with a distinct `last_error`** — rejected because it
  conflates "data bug, do not retry" with "PM was down, safe to re-drive,"
  collapsing the two operator remedies and making the backlog view ambiguous.
- **Hardcode the cap instead of making it configurable** — rejected because the
  right ceiling is a deployment/SLA decision; `SyncEngine` already takes
  `batch_limit` as a constructor knob, so `max_attempts` follows the same shape.
- **Skip the API surface, rely only on logs** — rejected because the issue
  explicitly asks for an operator/alerting surface; a pollable counts endpoint
  is the daemon's only HTTP face and the cheapest durable backlog view. (Logs
  are still added as the zero-infra alerting hook.)
- **Fix the deps-not-ready blind spot in the same change** — deferred to a
  follow-up issue; it is a separate code path (attempts never increments, so the
  cap can't catch it) and folding it in widens scope and risk.

## Steps

1. **Model + migration.** Add `STATUS_UNAVAILABLE = "UNAVAILABLE"` to
   `_STATUSES` in [models.py](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/models.py)
   and write an Alembic migration widening the `ck_powermap_outbox_status` check
   constraint. Verify: `alembic upgrade head` succeeds; constraint accepts the
   new value, rejects garbage. (Partial-unique-open index keys on `PENDING`
   only, so no index change needed.)
2. **Cap, test-first.** Add a failing test: an entry hitting `max_attempts`
   transitions to `UNAVAILABLE`; below the cap it stays `PENDING`; a
   deps-not-ready deferral never reaches the cap (attempts unchanged). Then add
   `max_attempts` to `SyncEngine.__init__` and a private `_fail_attempt(entry,
   now, error)` helper that increments `attempts`, sets `last_error`, and either
   reschedules (`PENDING`) or flips to `UNAVAILABLE` with an error-level
   `powermap_observation_unavailable` log. Route both the `TRANSIENT_EXCEPTIONS`
   branch and the unexpected-disposition branch through it. Default
   `max_attempts = 60` (≈ 2.3 days: the first ~7 attempts burn ~2h of short
   backoffs, then each is hourly at the 1h ceiling, so total ≈ 2h + 53h).
   Document the attempts↔wall-clock relationship in the docstring. Verify: new
   tests pass, existing write tests still pass.
3. **Backlog query.** Test-first: a method returning counts by status (overdue
   `PENDING`, `REJECTED`, `UNAVAILABLE`) plus oldest-pending age. Implement
   `async def backlog(session)` on `SyncEngine`, backed by the existing
   `ix_powermap_outbox_due` `(status, next_attempt_at)` index. Verify: query
   returns correct counts against seeded rows.
4. **API surface.** Add `/health/sync` to the health router
   ([main.py:28-32](../../packages/usa-wa-api/src/usa_wa_api/api/main.py#L28-L32))
   returning `backlog()` output. Verify: endpoint test asserts the JSON shape
   and status counts.
5. **Re-drive path.** Test-first: resetting `UNAVAILABLE → PENDING` with
   `next_attempt_at = now` and `attempts = 0`. Implement `async def
   redrive_unavailable(session, ...)` on `SyncEngine`. Verify: re-driven entries
   become due and are picked up by `drain_outbox` on the next cycle.
6. **Docs.** Update the `OutboxEntry` docstring (which currently claims
   "`REJECTED` rows persist as the operator backlog") to describe the
   `REJECTED` vs `UNAVAILABLE` split and the re-drive path. Verify: docstring
   matches behavior; `ruff check` clean.

## Open questions / risks (resolved)

- **Default `max_attempts` value.** Resolved: `60` (~2.3 days of PM outage
  tolerance). See step 2.
- **Re-drive surface.** Resolved: engine method only for now; DB/REPL invocation
  is acceptable. User-friendly trigger (HTTP/CLI) deferred to #16.
- **Deps-not-ready forever-defer.** Resolved: out of scope; split into #15.
- **`/health/sync` exposure.** Resolved: sits alongside the unauthenticated
  `/health` — no auth.
