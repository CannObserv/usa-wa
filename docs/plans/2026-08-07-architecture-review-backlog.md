# Architecture-review backlog clearance — orchestration plan

**Date:** 2026-08-07
**Scope:** issues #178–#189 (the first architectural review of this codebase) plus #169, folded in
**Batches:** 6 · **Agent slots:** 11 · **Issues:** 13

## Goal

Clear the twelve findings from the 2026-08-07 architectural review as a prioritized, merge-safe
sequence of agent batches. The review diagnosed three compounding problems: the pipeline has no
memory of its own runs, source coverage was never made into data, and cross-adapter composition has
no home — so `usa-wa-adapter-legislature` became a shared kernel by accident. The batch order below
lands telemetry and cheap structural locks first, then the composition layer, then the file moves
that depend on it, so that the largest refactors run against a tree that is already instrumented and
constrained.

## Approved approach

| Decision | Agreed |
|---|---|
| Quality axis | **Foundation leverage ×3** — structural enabling work ranks highest |
| Deployment context | **Early production** — Power Map is a live consumer, 11 daily timers; breakage recoverable |
| Deferrals | **None** — all twelve scoped issues in this round |
| Parallelism | **Hybrid** — parallel within batches, human review gate between |
| Worktree isolation | Yes, `isolation: "worktree"` for every worker |
| **Parallelism ceiling** | **4** — one provisioned test database per concurrent agent (see below) |
| Intra-batch merge | Regular merge (`--no-ff`), fixed — preserves the ancestor check `worktree-destroy --base` relies on |
| Batch → main merge | **Regular merge commit** — matches repo history, preserves per-agent commits |
| #169 | **Folded into the #180 agent as its first commit** (correctness fix leads the refactor) |

### The ceiling is a test database, not a port pool

This project has no custom worktree-create script and no port pool. Its parallelism ceiling comes
from somewhere else, discovered during Step 5:

- `conftest.py:64-90` — the session-scoped `test_engine` fixture runs `DROP SCHEMA … CASCADE` then
  `CREATE SCHEMA` for **every declared schema** at session start, against a single `usa_wa_test`
  database.
- `usa_wa_test_owner` has neither `CREATEDB` nor superuser (`rolcreatedb=f, rolsuper=f`), so a worker
  cannot provision its own.

Two agents running `pytest` concurrently therefore drop each other's schemas mid-run. Since the
worker protocol requires a full suite pass **before** signalling, the effective ceiling without
intervention is **one concurrent test run**, regardless of worktree isolation.

**Resolution — provision four test databases before Batch A launches** (superuser access via `sudo -u
postgres` confirmed):

```bash
for n in 1 2 3 4; do
  sudo -u postgres createdb "usa_wa_${n}_test"
  sudo -u postgres psql -q -c "ALTER DATABASE usa_wa_${n}_test OWNER TO usa_wa_test_owner;"
done
```

**Do not run `scripts/grants.sql` against a test database.** Its header says so explicitly: the test
DB's schemas do not exist until the suite creates them per session, so the schema-grant steps error.
A test slot needs only the (already-existing, cluster-global) `usa_wa_test_owner` role plus
`ALTER DATABASE … OWNER TO`. Applied and verified 2026-08-07: all four slots authenticate as
`usa_wa_test_owner`, and `pytest packages/clearinghouse-core/tests/test_adapter_runner.py` against
slot 1 passes 19/19.

Each worker's worktree gets `TEST_DATABASE_URL` pointing at its own slot.

**The slot names are `usa_wa_<n>_test`, not `usa_wa_test_<n>` — this is load-bearing.**
`clearinghouse_core.testing.assert_test_url_safety()` (called at `conftest.py` import) rejects any
DSN whose database name does **not** end in `_test`; `usa_wa_test_1` would fail that check and abort
the whole suite before collection. The other two belts still pass: the DSN differs from
`DATABASE_URL`, and `usa_wa_test_owner` ≠ the production role `usa_wa_app`.

#185 (Batch B) makes most later verification DB-free via `pytest -m 'not db'`, which relieves the
constraint further but does not remove it — DB-touching work still contends for slots.

## Prioritization rubrics

**Score = (Foundation × 3) + (Correctness × 2) + Scope**, max 18.

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| **Foundation leverage** (×3) | Standalone improvement | 1–2 other issues benefit | Multiple issues depend on or are simplified by this |
| **Correctness risk** (×2) | Cosmetic / organizational | Edge-case incorrect behavior, runtime failure risk | Data loss, race conditions, silent failures |
| **Scope clarity** (×1) | Requires design discovery | Clear direction, minor decisions | Mechanical — obvious from the issue |

Blast radius drives *sequencing*, not score.

## Scored backlog

| # | Item | F | C | S | **Score** | Blast |
|---|---|---|---|---|---|---|
| 1 | **#179 + #178** — job harness + run ledger | 3 | 3 | 2 | **17** | High |
| 2 | **#180** — source coverage as data | 3 | 2 | 2 | **15** | Med |
| 3 | **#189** — composition layer | 3 | 2 | 1 | **14** | High |
| 4 | **#185** — unit test tier | 3 | 1 | 2 | **13** | High |
| 4 | **#188** — `docs/ONTOLOGY.md` | 3 | 1 | 2 | **13** | Low |
| 4 | **#187** — workspace registries | 2 | 2 | 3 | **13** | Low |
| 7 | **#181 + #186** — SyncEngine split + test seam | 2 | 1 | 3 | **11** | Med |
| 7 | **#182** — declared-not-implemented tier | 2 | 1 | 3 | **11** | Low-Med |
| 9 | **#184** — read surface | 2 | 1 | 2 | **10** | Low |
| 10 | **#183** — adapter shape + naming | 2 | 1 | 1 | **9** | High |

**Closed-in-fact check: none.** All ten items re-verified against `9073f37` — no run/job table (0
matches), 29 direct `DATABASE_URL` reads, 29 `create_async_engine` sites, 40 `ArgumentParser` sites,
all 7 coverage constants present, `engine.py` still 2318 LOC, `usa-wa-adapter-sos` still absent from
`pyproject.toml` (0 matches).

**Prerequisite pairs resolved at Q0** — bundle #179+#178 (Shape A: one define→use sequence, and
splitting means touching all 47 CLIs twice); bundle #181+#186 (Shape A: the extraction and the test
migration into the seam it creates review as one thing); split #189/#183 (Shape B: the dependent
dwarfs the prerequisite and each needs its own review surface).

## Conflict zones

### Zone 1 — the 47 CLI entry modules (dominant)

| Package | CLI modules | Also claimed by |
|---|---|---|
| `usa-wa-adapter-legislature` | 20 | #183 (restructure), #189 (span engine leaves) |
| `usa-wa-adapter-pdc` | 4 | #183 |
| `usa-wa-adapter-sos` | 7 | #189 (apps extract to `usa-wa-facts-*`) |
| `usa-wa-sync-powermap` | 14 | #189 (WSLClient removal, 5 reconcilers) |
| `clearinghouse-core` / `usa-wa-api` | 2 | — |

**24 of #179's 47 targets sit inside #183's packages; 21 inside #189's blast.** #179, #183 and #189
are mutually exclusive.

**Resolution — decompose #179**, as its own body invites (*"Migrate opportunistically, one module per
PR"*):

- **#179a** — `clearinghouse_core/job.py` + `runs.py` + migration + pilot adoption in
  `clearinghouse_core/integrity.py`. Isolated; Batch A.
- **#179b** — adoption sweep across the remaining ~46 CLIs, run once against the **final** file
  layout. Batch F.

### Zone 2 — root `pyproject.toml` (four claimants, stanza-disjoint)

| Stanza | Lines | Issue |
|---|---|---|
| `[dependency-groups]`, `[tool.uv.sources]`, `[tool.ruff.lint.isort]` | 4-37, 67+ | **#187**, later **#189** |
| `[tool.coverage.run/report]` | 51-58 | **#182** |
| `[tool.pytest.ini_options]` | 38-50 | **#185** |

**#187 must precede #189** — its fitness test (the four registry sets must agree) is exactly what
stops #189's new packages repeating the omission that #187 exists to fix.

### Zone 3 — alembic revision chain

#178 (`runs`) and #180 (`source_coverage`) each create a migration; concurrent agents would both
claim `down_revision = <head>`. **Rule: at most one migration-creating agent per batch** — A4 and C1
respectively. No other agent runs `alembic revision`.

### Zone 4 — shared documentation files

`docs/ARCHITECTURE.md` is edited by #180 (checklist step), #189 (layer table) and #183 (package
shape); `AGENTS.md` by #188 and #183. Resolved purely by batch sequencing — no two land in the same
batch.

### Zone 5 — a feared conflict that does not exist

#186 migrates engine-contract assertions out of `usa-wa-sync-powermap/tests/test_sidecar.py`; #189
rewrites the five committee reconcilers. These were expected to collide. They do not:
`test_sidecar.py` contains **zero** references to `reconcile_committee*` or `WSLClient` (the
reconcilers own seven separate test files). **#181+#186 is fully disjoint from #189** and was
scheduled freely as a result.

### Zone 6 — out-of-scope collision, resolved

Open issue **#169** targets `filings/harvest.py`, also touched by #180 (a floor constant), #179b (the
CLI block) and #183 (the move). Per the *correctness fixes lead refactors* rule it is folded into the
C1 agent as its **first commit**, removing the conflict and shipping the bug fix early.

## Dependency graph

```
#187 registries ─────────────┐
#188 ONTOLOGY.md ────────────┤  (independent — Batch A, 4 parallel)
#182 declared tier ──────────┤
#179a core harness+ledger ───┘
        │
        ▼
    #185 unit test tier ............ touches every test package → solo (Batch B)
        │
        ├──────────────┬─────────────────────────┐
        ▼              ▼                         │
   #169 → #180     #181 → #186               (disjoint pair — Batch C)
   coverage        engine split
   (+migration)    + test seam
        │              │
        └──────┬───────┘
               ▼
        #189 composition layer ...... needs #187's registry lock
               │                       + #180's centralized floors  (Batch D, solo)
               ├──────────────┬────────────────┐
               ▼              ▼                │
         #183 adapter    #184 read surface  (disjoint pair — Batch E)
          shape+naming    (reads #178+#180)
               │              │
               └──────┬───────┘
                      ▼
              #179b adoption sweep ... final layout, one mechanical pass (Batch F)
```

Two edges carry the most weight: **#187 → #189** (lock the registries before adding packages) and
**#180 → #189** (centralize the seven coverage floors *before* moving the files that hold them, so
the move carries one constant rather than seven).

## Batch execution plan

| Batch | Agent | Issue(s) | Files | Gate |
|---|---|---|---|---|
| **A** | A1 | #187 | `pyproject.toml` (dev / uv.sources / isort) · new `scripts/tests/test_workspace_registries.py` | Start immediately |
| | A2 | #188 | new `docs/ONTOLOGY.md` · `AGENTS.md` Detail Docs link | |
| | A3 | #182 | `clearinghouse_domain_legislative/{bills,votes,statutes,pdc}.py` · `[tool.coverage.*]` · new fitness test | |
| | A4 | **#179a + #178** | new `clearinghouse_core/{job,runs}.py` · migration · `clearinghouse_core/integrity.py` (pilot) | |
| **B** | B1 | #185 | root `conftest.py` · 5 package conftests · `@pytest.mark.db` sweep (148 files) · `[tool.pytest.ini_options]` | After A merged |
| **C** | C1 | **#169 → #180** | `filings/harvest.py` (fix first) · new `clearinghouse_core/source_coverage.py` · migration · 7 floor constants · `docs/ARCHITECTURE.md` | After B merged |
| | C2 | **#181 → #186** | `clearinghouse_sync_powermap/engine.py` → `engine/{write,read,anchors}.py` + façade · `test_sidecar.py` assertion migration | |
| **D** | D1 | #189 | `CohortProvider` Protocol · span engine → Layer 2 · new `usa-wa-common`, `usa-wa-facts-*` · WSLClient removed from 5 reconcilers · import-linter contract · `pyproject.toml` | After C merged |
| **E** | E1 | #183 | `usa-wa-adapter-legislature` (49 modules) + `usa-wa-adapter-pdc` (14) restructure + naming | After D merged |
| | E2 | #184 | `usa-wa-api` — `/api/v1`, `/health/jobs`, `/sources/*/coverage` | |
| **F** | F1 | #179b | remaining ~46 CLIs onto `run_job()`, final layout | After E merged |

**Intra-batch merge ordering**

- **Batch A:** A1 merges **first** (claims three `pyproject.toml` stanzas); A3 **rebases** before
  merge (coverage stanza). A2 and A4 in any order.
- **Batch C, Batch E:** independent; either order.

**Branch strategy**

- Multi-agent batches (A, C, E) use a shared integration branch: `batch/a`, `batch/c`, `batch/e`.
- Single-agent batches (B, D, F) use the agent's feature branch directly — no separate batch branch.
- Human review happens against the batch branch; merge to `main` with a regular merge commit.

## Key decisions

1. **#179 is decomposed, not deferred.** It scores highest (17) but its adoption sweep collides with
   both #183 and #189. Splitting core (Batch A) from adoption (Batch F) lets the highest-value item
   start immediately while the sweep runs once against a stable layout instead of three times against
   a moving one.

2. **#185 sits at Batch B, alone, before all structural work.** It touches every test package, so it
   can never share a batch. Placing it before the moves means #189 and #183 carry the `db` markers
   along via git rename detection, and every batch from C onward verifies faster.

3. **#169 leads C1 rather than getting its own slot.** The skill's *correctness fixes lead refactors*
   rule: a targeted bug fix on a file a later refactor edits belongs at the head of that refactor's
   commit sequence, not in an earlier parallel slot where it would conflict with three downstream
   batches.

4. **#187 is cheap but gates #189.** A three-line edit scoring 13 sits in Batch A specifically
   because its fitness test must exist before #189 adds two new packages — otherwise the same
   omission recurs on the new packages and nothing catches it.

5. **#180 precedes #189 for a non-obvious reason.** Not priority — mechanics. #180 collapses seven
   duplicated floor constants into one table. Doing it after #189's file moves would mean chasing
   seven constants through relocated files; doing it before means #189 moves a single reference.

6. **Foundation shared files are read-only outside their owning batch** (prevents the
   three-concurrent-edits failure mode):
   - `clearinghouse_core/job.py`, `runs.py` — read-only for B–E; **F1 is the designated adopter**.
   - `scripts/tests/test_workspace_registries.py` — **D1 must satisfy it by editing `pyproject.toml`,
     never by editing the test.**
   - `docs/ONTOLOGY.md` — read-only for B–F; additions route as small post-merge doc PRs.

7. **AR-derived backlogs are maximally contested, the inverse of CR-derived ones.** A code-review
   backlog yields one bug per surface and parallelises freely (process-log 2026-05-09: 6 agents, zero
   contested files). An *architecture*-review backlog is the opposite: every finding is about
   structure, and structure is shared — which is why this plan is six mostly-serial batches rather
   than two wide ones. Recorded so a future orchestrator does not assume review-derived means
   disjoint.

## Deferred items

Nothing from #178–#189 is deferred; all twelve are in this round.

## Out of scope

Open issues that intersect this work but are **not** in the plan:

| Issue | Relationship |
|---|---|
| #160 — conditional GET on reconcile fetch path | Lands in `engine/read.py` **after** C2's split. Work it post-Batch-C. |
| #117 — non-convergence backstop Phase B (`parked`) | Lands in `engine/anchors.py` after C2. Stays parked. |
| #140 — historical House Position coverage edges | A **consumer** of #180's `source_coverage` table. Revisit after Batch C. |
| #135 — biennium-rollover source availability | Same — consumer of #180. Revisit after Batch C. |
| #28, #67, #99 — WSL bill cluster, committee activity, votewa enrichment | The tracking issues #182's declared tier must cite. Unblocked by, not part of, this plan. |
| #66 — periodic historical re-validation | No file overlap; independent. |

Also out of scope: retiring the read-only probe CLIs (`probe_committee_extent`,
`probe_member_identity`, `validate_committees`, `committee_lineage_suggest`) in favour of #184's read
surface. Noted in #184 as a follow-on; not planned here.
