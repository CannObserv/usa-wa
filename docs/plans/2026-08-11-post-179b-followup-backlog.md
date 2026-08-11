# Post-#179b followup backlog — prioritized batch clearance

**Date:** 2026-08-11
**Issues:** #160 (rescoped), #195, #196, #198, #201, #202, #205
**Skill:** `orchestrating-issue-backlog`

## Goal

Clear the seven followup issues carved out of the #189 / #183 / #185 / #179b shipping cycle
(batches A–F, merged through `aa06590`) as a prioritized, merge-safe sequence. The backlog is
across-the-stack rather than clustered — one defect per layer — so the file footprints are
almost fully disjoint. The binding constraint on parallelism is therefore **not** file
overlap but the single shared Postgres test database, and specifically one integration test
that drops every schema `CASCADE` outside the savepointed fixture.

## Approved approach

- **Rubric:** correctness-leading. `Score = (Foundation × 2) + (Correctness × 3) + Scope`, max **18**.
- **Deployment context:** early production — live systemd timers producing to Power Map, low
  volume, no external consumers. Stability matters; there is runway.
- **Parallelism:** hybrid — maximum width where files are genuinely disjoint, gates between.
- **Worktree ceiling: 3.** Plain `git worktree` (`skills/using-git-worktrees/scripts/`)
  provisions nothing project-specific — no port pool, no vhost, no DB clone. The
  ceiling-limiting resource is the single shared `TEST_DATABASE_URL` Postgres. Recorded at 3
  in the 2026-06-16 session and re-confirmed here.
- **Deferrals:** none. All seven in scope.
- **Merge strategy:** batch → `main` via `gh pr create` + **merge commit**, matching repo
  precedent (`Merge pull request #206 from CannObserv/batch/f`). Intra-batch
  worker → `batch/g` is fixed at `--no-ff` regular merge so `worktree-destroy.sh --base`
  keeps its ancestor check.

## Pre-scoring disposition: #160

Every issue was grepped against the live tree before scoring. Six were confirmed genuinely
open. One was not:

**#160's headline work had already shipped** at `db05912`. `ConditionalGetState` +
its migration, `fetch_record_conditional` / `get_entity_conditional` / `EntityFetch`, the
`If-None-Match` load/store around the anchored-cohort reconcile at `engine/read.py:494-526`,
the `conditional_get_enabled` kill switch, and tests at `test_engine_read.py:1750-1830` are
all in the tree. The issue had stayed open because its third comment named further work.

Disposition: **rescoped, not closed.** The replay-backstop fetch path
(`_apply_feed_page` → `descriptor.fetch_record`, `engine/read.py:686`) is still
unconditional, which is a real residual. The issue body was rewritten to that residual and
re-scored as a small item (7/18). Closing it outright would have discarded a genuine task;
leaving it whole would have allocated a batch slot to work already merged.

## Prioritization rubric

| Dimension | 1 | 2 | 3 | Weight |
|---|---|---|---|:-:|
| **Foundation Leverage** | Standalone improvement | 1–2 other issues benefit | Multiple issues depend on or are simplified by this | ×2 |
| **Correctness Risk** | Cosmetic / organizational | Edge-case incorrect behavior, runtime failure risk | Data loss, race conditions, silent failures | ×3 |
| **Scope Clarity** | Requires design discovery | Clear direction, minor decisions needed | Mechanical — obvious from the issue | ×1 |

Blast radius drives **sequencing**, not score.

## Scored backlog

| # | Issue | Fnd | Corr | Scope | **Score** | Blast | Footprint (grep-verified) |
|---|---|:-:|:-:|:-:|:-:|---|---|
| **201** | Split archive-refresh from fact-rebuild in the two facts-seats drivers | 3 | 2 | 1 | **13** | High | `pyproject.toml` (importlinter), `facts_seats/{house,pdc}/refresh.py` (176 ln ea.), adapter-sos, adapter-pdc, `scripts/tests/test_unit_exec_targets.py`, 2 systemd units, 4 docs |
| **195** | `test_refresh_e2e` asserts 1 FetchEvent but the refresh writes 37 | 2 | 2 | 3 | **13** | Low | `usa-wa-adapter-legislature/tests/test_refresh_e2e.py:127-128` |
| **198** | Unit tier fails its own coverage gate without `--no-cov` | 2 | 2 | 2 | **12** | Med | `pyproject.toml` (pytest + coverage), `AGENTS.md`, `README.md`, `docs/COMMANDS.md` |
| **205** | Flaky `test_ulid_time_ordering_preserved` | 1 | 2 | 3 | **11** | Low | `clearinghouse-core/tests/test_ulid.py:72-74` |
| **202** | Context budget: AGENTS.md (+431), MODULES-SYNC.md (+122) | 2 | 1 | 2 | **9** | Med | `AGENTS.md`, `docs/MODULES-SYNC.md`, new split file, index referrers |
| **196** | ASYNC230/240: four blocking file-IO calls in coroutines | 1 | 1 | 3 | **8** | Low | `adapter-legislature/src/`: `committees/ingest_seed.py:88`, `committees/succession_cli.py:243`, `meetings/harvest.py:150`, `operators/cli.py:249` |
| **160** | *(rescoped)* Conditional GET on the replay-backstop fetch path | 1 | 1 | 2 | **7** | Low-Med | `sync-powermap/engine/read.py` (`_apply_feed_page`), `engine/context.py`, tests |

### Non-obvious scores

- **#201 Correctness = 2, not 1.** Nothing behaves incorrectly today. The 2 is for the
  mechanism the issue names: two `ignore_imports` exceptions at `pyproject.toml:217-220`
  carrying a **false** provenance claim — the comment says "tracked as the follow-on named in
  `docs/MODULES-FACTS-SEATS.md`" and that file contains no such note. A layering fitness
  function with undocumented permanent holes is how an architecture contract rots silently.
  This is the score most sensitive to the ×3 weighting: at ×2 it drops to 11 and trades
  places with #198.
- **#195 and #201 tie at 13.** #201 leads on the Foundation tiebreak (3 vs 2 — it closes the
  last two holes in #189's contract). #195 wins decisively on Scope (3 vs 1).
- **#205 Correctness = 2, not 1.** ~1 run in 2,400 is a real gate failure, not cosmetic — it
  already burned a CR verification run. Foundation 1 keeps it out of the top tier.

## Backlog provenance

**Followup-derived, across-the-stack.** All seven are CR carve-outs from the same shipping
cycle. Per the skill's heuristic this predicts CR-like disjointness rather than a single-file
critical path, and the grep confirmed it — seven issues, seven layers: framework tests
(#205), adapter tests (#195), adapter src (#196), facts + deploy (#201), test config (#198),
docs (#202), sync engine (#160).

## Conflict zones

| File | Issues | Severity | Detail |
|---|---|---|---|
| `pyproject.toml` | #198, #201 | **Soft** | #198 → `[tool.pytest.ini_options] addopts` (:47) + `[tool.coverage.report]` (:74-76). #201 → `[[tool.importlinter.contracts]] ignore_imports` (:217-220). Distinct tables ~150 lines apart; git auto-merges. |
| `AGENTS.md` | #198, #202 | **Hard** | #198 → the `--no-cov` invocations at :149, :152. #202 → whole-file curation (6,431/6,000). #202 will move or rewrite the block #198 edits. |
| `docs/COMMANDS.md` | #198, #201, #202 | **Medium** | #198 → :125, :130-131 (a net *shrink* — dropping `--no-cov` from documented commands). #201 → the `pdc.refresh`/`house.refresh` entries at :32, :218, :226. #202 → 21 tokens of headroom, so the budget pressure comes from #201 alone. |
| `packages/usa-wa-adapter-legislature/` | #195, #196 | **None** | Verified disjoint: `refresh.py` imports `meetings.windows`, `membership.build`, `membership.cohort` — **not** `meetings.harvest` or `committees.ingest_seed`. None of #196's four files is in the e2e chain #195 asserts. |

Single-owner, uncontested: `test_ulid.py` (#205) · `engine/read.py` + `engine/context.py`
(#160) · `facts_seats/{house,pdc}/refresh.py` + adapter-sos + adapter-pdc + the two systemd
units (#201).

## The binding constraint is the shared test database, not file overlap

`clearinghouse_core.testing.reset_migration_schemas` (`testing.py:247-265`) does:

```
DROP TABLE IF EXISTS public.alembic_version
DROP SCHEMA IF EXISTS "<each declared schema>" CASCADE
```

It **opens its own engine**, deliberately bypassing the savepointed `db_session` fixture, so
it is not isolated by transaction rollback. Any worker running it destroys every other
worktree's in-flight db-marked test. Three tests call it, and one is #195's own file
(`test_refresh_e2e.py:96`; the other live caller is
`clearinghouse-core/tests/test_jurisdictional_seed_integration.py:85`).

This upgrades the 2026-06-16 "shared test DB caps the ceiling at 3" note from *contention* to
*destruction*, and it is what forces #195 into a solo batch — a sequencing edge invisible to
file-overlap analysis.

## Dependency graph

```
Batch A  (3 parallel = ceiling)         no shared config, no shared docs, no schema drops
  ├── #205  clearinghouse-core/tests/test_ulid.py        [db-marked, savepointed]
  ├── #196  adapter-legislature/src ×4                   [unit only]
  └── #160  sync-powermap engine/read.py, context.py     [db-marked, savepointed]
                          │
                          ▼  gate: A merged to main, full suite green
Batch B  (solo)   #195   adapter-legislature/tests/test_refresh_e2e.py
                          │   ── SOLE occupant: DROPs every schema CASCADE
                          ▼  gate: integration tier green
Batch C  (solo)   #201   facts-seats ×2 + adapter-sos + adapter-pdc
                          │          + pyproject [importlinter] + 2 systemd units + 4 docs
                          ▼  gate: lint-imports clean unaided, verify-units.sh green
Batch D  (solo)   #198   pyproject [pytest]+[coverage], AGENTS.md, README.md, COMMANDS.md
                          │   ── mutates the addopts every prior worker's TDD ran under
                          ▼  gate: unit tier exits 0 on its own
Batch E  (solo)   #202   AGENTS.md, docs/MODULES-SYNC.md (+ split file + referrers)
                              ── measures and cuts the FINAL doc state
```

Three edges, three distinct causes:

1. **`#195 → everything else` (DB destruction).** Not a file conflict. Early, so the
   integration tier is green before #198 reasons about what the tiers gate.
2. **`{#198, #201} → #202` (doc surface).** #202 is a measure-then-cut pass; curating a
   moving target is wasted work and its whole value is the final measurement.
3. **`{everything} → #198` (shared test infrastructure).** #198 changes `addopts` and the
   coverage profile — the ground every other worker's TDD stands on. Zero source overlap,
   still must go late.

## Batch execution plan

| Batch | Issues | Agents | Branch | Gate |
|---|---|:-:|---|---|
| **A** | #205, #196, #160 | 3 (parallel) | `batch/g` | Start immediately |
| **B** | #195 | 1 | `fix/195-refresh-e2e-assertions` | After A merged to main |
| **C** | #201 | 1 | `refactor/201-split-archive-refresh` | After B merged |
| **D** | #198 | 1 | `fix/198-unit-tier-coverage-gate` | After C merged |
| **E** | #202 | 1 | `docs/202-context-budget-curation` | After D merged |

### Batch A — 3 parallel agents

| Agent | Issue | Files | Verification |
|---|---|---|---|
| A1 | #205 | `clearinghouse-core/tests/test_ulid.py` | `uv run pytest --no-cov packages/clearinghouse-core/tests/test_ulid.py` |
| A2 | #196 | four `adapter-legislature/src` files (see scored table) | `uv run pytest --no-cov -m 'not db and not integration'` + `uv run ruff check .` |
| A3 | #160 | `sync-powermap/engine/read.py`, `engine/context.py`, tests | `uv run pytest --no-cov packages/clearinghouse-sync-powermap/tests` |

No intra-batch merge ordering — the three are file-disjoint, any merge order works.

**Hard rule for every Batch A worker: do not run `-m integration`, and do not call
`reset_migration_schemas`.** A1 and A3 run db-marked tests concurrently against the one
Postgres; that is safe only while every test stays inside the savepointed `db_session`
fixture. A2 is unit-only.

**Per-agent notes:**

- **A1 (#205)** — separate the two ULIDs by ≥1 ms and assert the property at the resolution
  it actually holds (`earlier.timestamp < later.timestamp`). Keep the DB round-trip half of
  the test; it is unaffected.
- **A2 (#196)** — wrap in `asyncio.to_thread(...)` or hoist the IO out of the coroutine
  (all four are argument-parsing-adjacent), then delete the four `noqa` comments. All four
  are now `run_job()` handlers post-#179b. Read
  `test_the_dry_run_flag_advertises_its_narrow_meaning` before touching
  `meetings/harvest.py:150` — that write is `--dry-run`-guarded and its semantics are pinned.
- **A3 (#160)** — carries the one design decision in this batch. `_apply_feed_page` is shared
  by the live feed path and the replay backstop (`read.py:636` says so). On the live feed a
  conditional GET is nearly always a 200, so the saving is replay-only. Choose between
  conditional-on-both and gated-on-the-replay-caller, and record the choice in the commit
  message. Reuse the existing `ConditionalGetState` store and `conditional_get_enabled` kill
  switch — do not add a second store or a second flag.

### Batch B — #195 (solo)

Keep `len(sources) == 1`. Replace the two cardinality assertions with the invariants the
test is actually for: every `FetchEvent` references that one source, and all four expected
`resource_id` prefixes are present (committees, sponsors, committee-members-hist, meetings);
each `RawPayload`'s `content_hash` matches its body.

**Mind the #82 caveat:** a forced daily re-pull re-records a payload-less `FetchEvent`, so
`len(payloads) == len(events)` is **not** the invariant. Assert one payload per
payload-bearing event.

Gate: `uv run pytest -m integration` green (3 tests, currently 1 failing).

### Batch C — #201 (solo)

Each adapter owns "refresh my archive"; the fact owns "rebuild from the archive".
`usa_wa_adapter_sos.results` and `usa_wa_adapter_pdc` grow the Phase-A drivers;
`usa_wa_facts_seats.house.refresh` / `.pdc.refresh` keep only the rebuild, taking a cohort
provider.

- Delete **both** `ignore_imports` at `pyproject.toml:217-220`, and delete the false
  provenance comment rather than relocating it.
- Decide which half `--force` and `--biennium` govern; update `docs/COMMANDS-BACKFILL.md`,
  which documents the current combined semantics.
- Two systemd units invoke these: `deploy/usa-wa-sos-refresh.service` (→
  `usa_wa_facts_seats.house.refresh`) and `deploy/usa-wa-pdc-refresh.service` (→
  `usa_wa_facts_seats.pdc.refresh`). `scripts/tests/test_unit_exec_targets.py` catches a unit
  left pointing at an old path.
- Remember the deployment rule: unit files are installed as root-owned **copies** —
  `sudo cp deploy/<unit> /etc/systemd/system/` before `daemon-reload`.

Gate: `uv run lint-imports` passing **unaided**, `scripts/verify-units.sh` green, full suite
green.

### Batch D — #198 (solo)

Prefer the issue's **option 2** — a second coverage profile scoped to what the unit tier
actually exercises, with its own `fail_under` — over option 1 (an `addopts` alias carrying
`--no-cov`). Option 2 gives the fast tier a real ratchet rather than an exemption, which is
the stated point of the issue. Option 1 is an acceptable fallback if 2 proves impractical;
say which was chosen and why in the commit message.

Update every documented invocation that currently carries `--no-cov`: `AGENTS.md:149,152`,
`README.md:50,56`, `docs/COMMANDS.md:125,130-131`.

Gate: `uv run pytest -m 'not db and not integration'` exits 0 with no flag.

### Batch E — #202 (solo, last)

Run the `curating-context` skill. Targets, measured against the repo's own
`.skills/context-token-ratio` (2.32 bytes/token) **after** C and D land their doc edits:

- `AGENTS.md` — 6,431 / 6,000 budget
- `docs/MODULES-SYNC.md` — 10,122 / 10,000 budget
- `docs/COMMANDS.md` — 9,979 / 10,000, i.e. 21 tokens of headroom; watch, do not necessarily cut

**Structural finding:** `docs/MODULES-SYNC.md` has exactly **one** heading
(`# Modules — Layer 4 deployment`); the body is a single ~23 KB indented code-block tree. The
usual "split on top-level headings" advice has nothing to grab. The seam is the tree's own
top-level entries — `usa-wa-api/` (the API deployment, which already has `docs/API.md`) vs
`usa-wa-sync-powermap/` (sidecar + producer CLIs) vs the repo-root directories. This is a
structural re-cut, not a prose compression: relocate rather than shorten.

Update the `AGENTS.md` § Project Layout module-doc list and § Detail Docs index for any new
file, and verify all relative links.

## Key decisions

1. **#160 rescoped rather than closed.** Its headline shipped; its residual is real. Closing
   would have discarded genuine work, keeping it whole would have wasted a slot on merged code.
2. **#195 is solo for a database reason, not a file reason.** `reset_migration_schemas` drops
   every schema `CASCADE` outside the savepointed fixture. This edge does not appear in any
   file-overlap analysis and is the single most important sequencing fact in this plan.
3. **#198 goes late despite scoring third.** Blast ≠ priority. It mutates `addopts` and the
   coverage profile — shared test infrastructure that every other worker's TDD stands on.
   Same class as the 2026-07-08 `conftest.py` finding: zero source overlap, still sequenced late.
4. **#202 goes last because it is a measurement.** A curation pass that runs before #198 and
   #201 land their doc edits measures a state that will not exist by the time it merges.
5. **D + E were not bundled.** Tempting as Shape A — #198's doc edits are part of what #202
   must curate, a genuine define→use sequence. Rejected on the *differ-in-kind* refinement: a
   pytest/coverage config change and a docs restructure that spawns a new file are two clean
   review surfaces, not one. Gates between single-agent batches are cheap.
6. **#201 was not chunked into Batch A** even though its files are disjoint from all three A
   agents. Under early production its systemd `ExecStart` moves and its `lint-imports`
   contract change deserve verification against a clean tree, not alongside three unrelated
   diffs.
7. **The context-budget guard is advisory, not a gate.** `.claude/hooks/context-budget-guard.sh`
   is a PostToolUse hook that always exits 0. It nags; it never blocks a commit. The
   pre-commit gate is ruff + `lint-imports` + `verify-units.sh` only. This downgrades the
   `docs/COMMANDS.md` headroom problem from a hard dependency to a coordination note.

## Deferred items

None. All seven issues are in scope for this plan.

## Out of scope

- **#194** (declared-not-implemented tier), **#140**, **#135**, **#117** (parked), **#99**,
  **#67**, **#66**, **#28** — open, but outside the seven named for this orchestration.
- **power-map#392** — PM's coverage gaps on read endpoints the reconcile does not poll. Named
  in #160's history; not needed for the residual.
- **The bulk version manifest** — the only lever that would cut the backstop's *request
  count*, as opposed to its bandwidth. Not filed on either side; noted so #160's scope caveat
  is not mistaken for an oversight.

## Orchestrator runtime checklist

1. **Before every batch:** `git checkout main && git pull --ff-only`. Workers worktree from
   local `main`; a stale local `main` bases their work on the wrong commit (Rule 1).
2. **Never** `git push origin HEAD:main` from a feature branch (Rule 2).
3. **Batch A only:** `git checkout -b batch/g` before spawning, so it is the merge target.
   Single-agent batches B–E use the worker's own feature branch.
4. **Verify slot availability** before launching:
   `bash skills/using-git-worktrees/scripts/worktree-list.sh --porcelain | grep -c '^worktree '`
   minus 1 for the main checkout, against the ceiling of 3.
5. **Do not assume `isolation: "worktree"` auto-merges** (Rule 3). On each completion signal:
   - `git -C /home/exedev/usa-wa status --porcelain` — any output means a worker fell through
     into the main checkout; halt the batch and salvage per `references/recovery.md` (Rule 6).
   - `git branch --no-merged batch/g` to locate the worker's actual branch; also check
     `git branch --show-current` and the worktree directory for uncommitted work.
   - `git merge --no-ff <agent-branch>` into `batch/g`.
   - `bash skills/using-git-worktrees/scripts/worktree-destroy.sh <agent-branch> --base batch/g`
   - `git branch -d <agent-branch>` — lowercase `-d`; if it refuses, the commits are not on
     `batch/g`. Escalate rather than force.
6. **After a rebase conflict**, `git commit --amend` the message immediately — before
   continuing (Rule 4).
7. **Full suite** on the batch branch before signalling the user. The orchestrator's run is
   the authoritative one; workers run targeted package tests only, because of the shared
   Postgres.
8. **After each merge to main:** `sudo systemctl restart usa-wa` — and after Batch C, also
   `sudo cp deploy/<unit> /etc/systemd/system/ && sudo systemctl daemon-reload`.
