# 2026-08-13 — Sidecar + test-infra backlog orchestration (#208, #211–#213, #216)

## Goal

Clear the five issues filed out of the #207 cycle: the shared-test-DB concurrency hazard
(#208), the replay-backstop runaway and its two read-path defects (#211, #212, #213), and
the integration tier's lying exit code (#216). End state: replay re-enabled with a
meaningful `healed` signal, concurrent pytest sessions safe-or-legible, and an
integration tier whose exit code a scheduler can trust.

## Approved approach

- **Q0**: #212+#213 bundled (one agent, sequential commits — same file, same path).
  #211 in scope as the full fix, gated behind the bundle.
- **Q1**: correctness-first — Correctness ×3 in the rubric.
- **Q2**: early production (live daily PM syncs, no external API consumers).
- **Q3**: cheap-first depth. #208 = advisory lock (option 3), namespacing/xdist (option 1)
  deferred. #216 = exit-code fix (option 2, `--no-cov`-equivalent for the tier),
  scheduled-run wiring deferred. Decisions recorded on the issues 2026-08-13.
- **Q4**: hybrid — parallel Batch A, gated Batch B, worktrees throughout.
- **Q5 / ceiling**: plain git-worktree tooling, no provisioning ceiling. The shared
  `TEST_DATABASE_URL` + destructive session fixture caps concurrent db-marked pytest at
  **1** — which is what #208 itself fixes, so the cap binds during this backlog's own
  execution. Resolution: **serialize the verification gate** — workers self-verify with
  the unit tier only (`uv run pytest -m 'not db and not integration'`); the orchestrator
  runs db-marked and integration tests alone at each batch gate.
- **Merge strategy**: intra-batch worker→batch = FF/regular merge (fixed);
  batch→main = **regular merge commit**.

## Prioritization rubric

Score = (Foundation × 2) + (Correctness × 3) + Scope, max 18. Correctness ×3 per Q1
(variable-weight escape hatch). Blast radius drives sequencing, not score.

## Scored backlog

| Item | F | C | S | Score | Blast | Disposition |
|---|---|---|---|---|---|---|
| #208 test-DB advisory lock | 3 | 3 | 3 | 18 | Med | Batch A (scope 2→3 after option-3 decision) |
| #212+#213 read.py bundle | 2 | 3 | 2 | 15 | Low | Batch A |
| #216 integration exit code | 2 | 2 | 3 | 13 | Low | Batch A |
| #211 replay backstop fix | 1 | 2 | 1 | 9 | Med | Batch B (design discovery; C mitigated by kill switch) |

All five verified live against the tree 2026-08-13 (fixture, `apply_record`, feed-path
skip, coverage config all as described; one line-drift: `test_refresh_e2e.py` caller now
at :120, not :96).

## Conflict zones

| File | Items | Resolution |
|---|---|---|
| `packages/clearinghouse-sync-powermap/…/engine/read.py`, `tests/test_engine_read.py`, `tests/test_engine_replay.py` | #212+213, #211 | Hard sequence — bundle first, #211 branches from merged state (grep-confirmed overlap) |
| `AGENTS.md` ~:118–129, `docs/COMMANDS.md` ~:134–146 | #208, #216 | Line-window ownership: #208 owns the new concurrency-contract lines; #216 owns the integration-tier lines. Additions within own window only; **no restructuring of the test-commands block** |

Everything else disjoint: #208 (`conftest_db.py`, `clearinghouse_core/testing.py`,
`test_conftest_db_guard.py`) vs #216 (`conftest_coverage.py`, `pyproject.toml`,
`scripts/tests/test_coverage_profiles.py`) vs the sync packages.

No chain-appending artifacts in scope (no migrations; `APPLY_NOOP` is a constant).

## Dependency graph

```
#208 ──────────────────────────┐
#216 ──────────────────────────┼─→ gate A (full suite + one -m integration run, alone)
#212+#213 ─→ #211 ─────────────┘─→ gate B ─→ ops tail (re-enable replay, observe)
```

## Batch execution plan

| Batch | Issues | Agents | Files | Gate |
|---|---|---|---|---|
| A | #208; #212+#213; #216 | 3 parallel, worktrees, unit-tier self-verify | see conflict table | start immediately |
| B | #211 | 1 | `usa-wa-sync-powermap/config.py`, `sidecar.py`, `clearinghouse-sync-powermap/engine/read.py` (+tests), `docs/ENVIRONMENT.md`, `docs/MODULES-SYNC*.md` | after A merged to main |
| ops | re-enable replay | orchestrator + user (sudo) | `/etc/usa-wa/.env` (delete `REPLAY_ENABLED=false`), `sudo systemctl restart usa-wa-sync-powermap` | after B merged; observe ≥2 replay passes: req/s under ceiling, `healed` → 0 on converged state, no repeat-404s |

## Key decisions

- **#208 option 3**: blocking `pg_advisory_lock` with timeout; clear failure message on
  expiry subsumes option 4. Both `reset_migration_schemas` callers covered by the same
  mechanism. Option 1 (namespacing/xdist) deferred.
- **#216 option 2**: no meaningful line-coverage number exists for an out-of-process
  subprocess tier; exempt it rather than ratchet it — the inverse of #198's verdict, for
  the asymmetry the issue states.
- **#212 mechanism** (attribute history vs `session.is_modified`) left to the worker —
  implementation detail, not a product call.
- **Verification-mode asymmetry**: #208 changes the db fixture and #216 changes coverage
  gating, so gate A's full-suite + integration run is the **first execution under the new
  modes**. A red there is a mode interaction until proven otherwise — not automatically
  the third worker's defect. The integration run also hits live WSL SOAP; an upstream
  outage is a possible red cause.
- **Read-only for Batch B**: `conftest_db.py`, `conftest_coverage.py`, `pyproject.toml`
  coverage sections — Batch A just shipped them; #211 has no business editing them.
- **`healed` observation window**: #212 makes `replay_healed` meaningful for the first
  time; #211 is not "done" at merge but after the ops tail shows convergence.

## Runtime note on issue-body decay

Issue bodies are a snapshot. #211's body predates the kill-switch lift (2026-08-13
comment is authoritative on current topology) and predates whatever the Batch A bundle
does to `read.py` — the Batch B worker must re-verify every line reference in #211
against post-A main. Line refs in #208/#213 already drifted once (`:96`→`:120`, `:782`).

## Deferred items

- #208 option 1 — per-session schema namespacing / xdist unlock (own issue when wanted).
- #216 scheduled integration run with alerting + pre-merge path gate (recommendation
  recorded in #216; needs #208 landed first anyway).
- #117 write-path non-convergence — explicitly distinct from #212 (read path).

## Out of scope

- Any PM-side change (power-map#387 rate limiting, bulk feed endpoints) — #211's fix must
  work against PM as it is; a bulk/if-modified-since feed read is only in scope if PM
  already serves it.
- Timer/unit file changes beyond what #211 strictly needs.
