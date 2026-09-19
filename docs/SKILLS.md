# Agent Skills

Skills are reusable agent instructions. `usa-wa` consumes two upstream catalogs (vendored as git submodules under `skills-vendor/`) and exposes them through two discovery directories:

- **`skills/`** — agentskills.io convention (one symlink per skill, plus any local overrides).
- **`.claude/skills/`** — Claude Code discovery directory (mirrors every entry in `skills/`).

The vendor → symlink → discovery layout means the project carries no skill source code of its own (except local overrides) and stays in sync with upstream via submodule updates. The `SessionStart` hook in [`.claude/settings.json`](../.claude/settings.json) runs the vendored [`skills-submodule-update.sh`](../.claude/hooks/skills-submodule-update.sh) to keep both vendors current — once per UTC day, on `main` only, auto-committing the pointer bump. It also (re)installs `.skills/doctor.sh` on **every** session, outside the daily lock. Three further `SessionStart` hooks are registered there: the SocratiCode prefetch reminder, the daily health check ([§ SocratiCode health](#socraticode-health)), and the daily context-manifest drift report (project-local, not vendored — see [`docs/CODE-EXPLORATION.md` § Manifest coverage](CODE-EXPLORATION.md#manifest-coverage--the-drift-that-grows-silently-300)).

## `.skills/doctor.sh` — the preflight

Phase 1 of every `reviewing-*` / `shipping-*` skill runs `{ [ ! -x .skills/doctor.sh ] || bash .skills/doctor.sh; }`. The doctor walks the `skills/*` symlinks, auto-runs `git submodule update --init --recursive` when any dangle, and prints an actionable error otherwise — so a fresh `git worktree add` or a shallow CI clone doesn't hit "No such file or directory" on a skill invocation.

It is a **file copy**, not a symlink, deliberately: a symlink into `skills-vendor/` would itself dangle in exactly the uninitialized-submodule state the doctor exists to repair. Since gregoryfoster/skills#84 the doctor re-syncs itself from the vendored source on every mutating run, so upstream fixes arrive without a manual reinstall. It **must stay committed** — the hook installs it into the working tree, but only ever commits `skills-vendor/` and `.skills/doctor.sh`; anything untracked leaves CI and fresh worktrees with no doctor and the preflight silently short-circuits.

```bash
bash .skills/doctor.sh --version   # installed copy's stamp
bash skills-vendor/gregoryfoster-skills/skills/managing-skills/scripts/install-doctor.sh   # manual (re)install
```

## Hook timeouts

Each vendored hook ships a `<hook>.install` manifest carrying a `--timeout`, and an entry registered *without* one silently inherits the harness default instead (#379). All three vendored registrations now name theirs:

| Hook | Timeout | Why that budget |
|---|---|---|
| `skills-submodule-update.sh` | 120s | a `git submodule update --remote` per vendored repo, and since [gregoryfoster/skills#293](https://github.com/gregoryfoster/skills/issues/293) a `git push` sharing it. A kill between the commit and the push leaves `main` ahead of `origin/main` — an unpushed pointer bump is functionally untracked for every consumer but this machine |
| `socraticode-health.sh` | 120s | shells out to `mcp-driver.mjs`, which launches the server; it stamps its once-per-UTC-day lock *before* the work, so a kill consumes the day's attempt and reports nothing |
| `socraticode-reminder.sh` | 5s | one `echo` — no network, no Docker, no submodule |

`context-manifest-drift.sh` is project-local, ships no manifest, and carries no prescribed value.

**The order of the four entries is incidental.** Re-running an installer strips its own entry and re-appends it, so the array order reflects the order the installers last ran, not a decision. Nothing depends on it today; if something ever does, say so here rather than inferring intent from the file.

Re-running an installer **adds** a missing timeout and **preserves** a differing one ([gregoryfoster/skills#259](https://github.com/gregoryfoster/skills/issues/259)), so the repair is idempotent:

```bash
bash skills-vendor/gregoryfoster-skills/skills/managing-skills/scripts/install-refresh.sh
bash skills-vendor/gregoryfoster-skills/skills/managing-skills/scripts/install-refresh.sh --check
```

Pinned by [`scripts/tests/test_hook_registration_gate.py`](../scripts/tests/test_hook_registration_gate.py), which asserts *presence* rather than the manifest's literal — asserting equality would fight #259's preserve-beats-prescribe.

## Vendor sources

| Submodule | Upstream | Purpose |
|---|---|---|
| `skills-vendor/gregoryfoster-skills` | [gregoryfoster/skills](https://github.com/gregoryfoster/skills) | CannObserv-specific workflows (init, shipping, reviewing) |
| `skills-vendor/obra-superpowers` | [obra/superpowers](https://github.com/obra/superpowers) | General-purpose agent skills |

## Vendor skills (from gregoryfoster-skills)

| Skill | What it does |
|---|---|
| `curating-context` | Curate the agent-context surface (`AGENTS.md` + the docs it links) against a token budget, verifying facts before removing anything. Triggers: `curate context`, `context budget`, `trim AGENTS.md`. Also installs the write guard — see [§ Context budget](#context-budget). |
| `enforcing-architecture` | Graduate an accepted architecture finding into an executable fitness function (import-linter / module-size gate / OpenAPI drift guard). Triggers: `add a fitness function`, `enforce this contract`, `lock this rule`. |
| `init-project-fastapi` | Bootstrap a new FastAPI service (this project's origin). |
| `init-socraticode` | Set up / repair SocratiCode indexing, the context-artifact manifest, and the code-exploration policy. Vendors the two `SessionStart` hooks — the prefetch reminder and the daily health check (see [§ SocratiCode health](#socraticode-health)). |
| `managing-skills` | Add/update/audit skills across vendors and overrides. |
| `orchestrating-issue-backlog` | Triage and sequence open GitHub issues into actionable work. |
| `reviewing-architecture` | Architectural review of a design doc or large change. Delegates to `enforcing-architecture` on a `fix + fitness` / `fitness` directive — both must be symlinked or the delegation fails to resolve. |
| `reviewing-code-python-fastapi` | Python/FastAPI-stack code review (the review workflow for this repo). |
| `shipping-work-python-fastapi` | Python/FastAPI ship workflow with `pre-ship.sh` (the ship workflow for this repo). |
| `using-git-worktrees` | Worktree-based branch workflow for parallel work. |
| `writing-plans` | Drafting an implementation plan in `docs/plans/` before coding. |

Only the Python/FastAPI variants of the review and ship workflows are symlinked here. The vendor also ships stack-neutral, PHP, and Python/Click variants (`reviewing-code{,-php,-python-click}`, `shipping-work{,-php,-python-click}`); those are intentionally **not** symlinked into this FastAPI repo. They remain available under `skills-vendor/gregoryfoster-skills/skills/` if ever needed.

## Vendor preferences on name collisions

Two skill names exist in both vendors. For each, we pick the CannObserv (gregoryfoster) version explicitly:

| Skill | Resolves to |
|---|---|
| `using-git-worktrees` | `skills-vendor/gregoryfoster-skills/skills/using-git-worktrees` |
| `writing-plans` | `skills-vendor/gregoryfoster-skills/skills/writing-plans` |

The upstream `init-project-fastapi` skill's Phase 10 loop now enforces this itself: it uses `ln -sfn` with vendor ordering that lets gregoryfoster override obra on a collision (and pattern-filters the review/ship stack variants). This table records the preference; no local workaround is needed on a re-run.

## Local overrides

**There are none.** Every entry in `skills/` is a symlink into a vendor.

A local override is a full copy (not a symlink) at `skills/<name>/`, declaring `overrides: <vendor>/<upstream-skill-name>` and `override-reason:`. Since gregoryfoster/skills#238 it must also record **what it was last synced from**, and which key depends on the vendor:

| Vendor frontmatter | Override records | Doctor compares |
|---|---|---|
| ships a `version:` | `version:` — *the vendor version last synced from*, bumped on every re-sync even when the local deltas are unchanged | the two version strings |
| ships none | `synced-from: "<repo> <tag> (<commit>)"` | `git diff <commit> HEAD` scoped to the skill's path |

Neither key present is reported as **un-assessable**, which is the same failure as not detecting drift at all.

`skills/brainstorming/` was the one override and is now a symlink (#263). It claimed "project-specific narrative content", but it was byte-identical to obra-superpowers **v5.1.0** apart from its three frontmatter lines — the project content it was forked to hold was never added. Meanwhile upstream changed 950 lines across 8 files (+810/−140, including v6.3.0's three-path router), none of which the repo was getting. That is the drift mode the `synced-from:` check exists to surface, caught here in its terminal form: a fork with nothing in it, silently pinning a skill at the version vendored on day one (2026-05-25) through seven upstream releases (v6.0.0 → v6.3.0).

**The lesson, not just the fix:** an override costs a permanent manual re-sync obligation, so fork only when the project genuinely diverges *today* — never speculatively, to hold content someone might add later. See the `init-project-fastapi` SKILL.md "Phase 10 — `skills/` directory" section for the conditions that warrant one.

## Local script overrides

Every `reviewing-*` / `shipping-*` skill resolves its scripts by probing `scripts/` **before** the skill's own directory, so a project-local `scripts/<name>.sh` wins with no skill edit. One exists:

| Script | Why |
|---|---|
| [`scripts/pre-ship.sh`](../scripts/pre-ship.sh) | Loads the two env files, then `exec`s the vendored gate. The gate runs the full suite, whose `db`-marked majority needs `TEST_DATABASE_URL`, so on a clean shell its test phase died wholesale (#172; pre-#185 it died at conftest import). This is the override point the vendored script documents. |

**It is a wrapper, not a fork — keep it that way.** The ~200 lines upstream owns (per-SHA stamp cache, `pytest-cov` detection, the JS block, the zombie preflight) stay in one place and keep improving without a merge; a copy would drift silently on every submodule bump. Pinned by [`scripts/tests/test_pre_ship_wrapper.py`](../scripts/tests/test_pre_ship_wrapper.py), whose last test fails if the vendored delegate path moves. Upstream ask to make this recipe the sanctioned idiom (it currently says "keep a thin local fork", and only 1 of the 4 `shipping-work*` variants says even that): [gregoryfoster/skills#105](https://github.com/gregoryfoster/skills/issues/105).

## SocratiCode health

Adding an artifact to `.socraticodecontextartifacts.json` **does not index it**. Nothing reacts to a manifest edit: `codebase_context_search` answers from indexed artifacts only, and silently answers without the missing one — no error, no warning, and `codebase_status` stays green at the top while reporting the shortfall in a line nobody reads (#263, upstream [gregoryfoster/skills#214](https://github.com/gregoryfoster/skills/issues/214)).

`.claude/hooks/socraticode-health.sh` is the detector — a `SessionStart` hook symlinked into the vendored `init-socraticode/scripts/`, wired in [`.claude/settings.json`](../.claude/settings.json). It runs at most once per UTC day **per project** (the lock lives in the common `.git`, so N worktrees produce one report a day, not N), is silent when there is nothing to report, and exits 0 on every path so it can never block a session. It **reports; it never repairs** — no re-index, no `docker start`, no file edit.

What it surfaces: a declared-but-unindexed (or stale) context artifact **by name**, a `codebase_health` problem, a FAILED or INCOMPLETE last operation, and the graph edge-yield gate. That last one fires here every day and is expected — it is the broken file-dependency graph documented in [`docs/CODE-EXPLORATION.md`](CODE-EXPLORATION.md), not a new finding.

```bash
SOCRATICODE_HEALTH_FORCE=1 bash .claude/hooks/socraticode-health.sh   # ignore the daily lock
bash .claude/hooks/socraticode-health.sh --help                      # env vars, driver resolution
# findings log: <common .git>/socraticode-health.log
```

Act on an artifact finding with `codebase_context_index`; on an index finding with `codebase_index`.

## Context budget

`curating-context` (#161) keeps `AGENTS.md` and the docs it links under a token budget — every token in that surface is paid on every agent invocation. It leaves five tracked files in `.skills/`:

| File | What it is |
|---|---|
| `context-budget` | policy-file budget in force — **6,000** tokens |
| `context-doc-budget` | per-reference-doc budget — **10,000** tokens |
| `context-token-ratio` | this repo's measured bytes-per-token, written by each `--exact` run; the offline estimators read it so `bytes/4` (which under-reports this content by ~60%) is never used |
| `context-metrics.jsonl` | append-only ledger, one row per run. Committed rather than centralized so the history travels with the repo and is reviewable in the same PR as the edits it describes |
| `doctor.sh` | unrelated — see [§ the preflight](#skillsdoctorsh--the-preflight) |

The weekly run recovers ground; the **write guard** stops regrowth between runs. It is a `PostToolUse` hook on `Edit|Write|MultiEdit`, wired in [`.claude/settings.json`](../.claude/settings.json), and it is advisory only — always exits 0, and stays silent unless an edit *both* pushes a context-surface file past its budget *and* increases it since `HEAD`, so a curation run is never nagged. `docs/plans/`, `docs/specs/`, and `docs/research/` are excluded as archival at any depth (so are `audits/` and `archive/`, which this repo does not currently have).

```bash
bash skills/curating-context/scripts/measure-context.sh --exact   # exact counts (needs ANTHROPIC_API_KEY; read from the repo-root .env)
bash skills/curating-context/scripts/prove-no-loss.sh --base main # assert a curation relocated rather than dropped
```

`.claude/hooks/context-budget-guard.sh` is a symlink through `skills/curating-context/` into the vendor submodule, so on an uninitialized checkout it dangles and every edit fails the hook until the submodule is initialized — `.skills/doctor.sh` heals it, since the chain routes through `skills/*`, which the doctor's scan covers (upstream: [gregoryfoster/skills#99](https://github.com/gregoryfoster/skills/issues/99)).

## Ship-gate sensitive paths and advice

`.skills/doc-sensitive-paths` is the list Step 1.5 of the shipping skill (`doc-check.sh`) checks a branch's changed files against — files whose names or structure some doc enumerates, so the matching section gets a look before the branch ships. It **replaces** the vendored defaults rather than extending them.

Grammar: one path per line, blank lines and `#` comments ignored (the same shape as `.skills/import-targets` upstream). Entries match whole path **segments** at any depth, so `pyproject.toml` covers the root file *and* every `packages/*/pyproject.toml`.

**Why this repo needs its own (#297).** Before [gregoryfoster/skills#252](https://github.com/gregoryfoster/skills/issues/252) entries were anchored at the start of the path, so on this uv workspace — eleven members, every source file under `packages/*/src/` — the defaults matched nothing below the root and a branch that renamed a member printed `No sensitive paths changed` and exited 0. Six of the twelve defaults (`CHANGELOG.md`, `schema.sql`, `src/api/`, `src/models/`, `src/core/`, `.env.example`) name nothing that exists here at all.

Two upstream behaviours make a stale list visible rather than silent: an entry matching no tracked file is named in a note on every clean run, and a list where *no* entry matches anything exits 2 — a gate that could not have found anything is not a pass. `scripts/tests/test_doc_sensitive_paths.py` fails on either state before it reaches a ship.

The list is deliberately narrower than a bare `src/`: that would match every source file in the workspace, so every branch would exit 1 and the exit code would stop meaning anything. Each entry names a surface a doc *enumerates*.

**The advice half (#371).** `doc-check.sh` resolves two files independently: the list above says *what the gate watches*, `.skills/doc-sections` says *what to do about a hit*. Same grammar, and it likewise **replaces** the vendored defaults. Tailoring one and not the other is the failure [gregoryfoster/skills#284](https://github.com/gregoryfoster/skills/issues/284) made audible — from #297 until #371 every hit here printed the python-fastapi defaults ("project structure, conventions, skill inventory, route table") and never named `docs/COMMANDS.md` or `docs/DEPLOYMENT.md`, the docs that actually enumerate what the list watches. A hit now ends with `(advice: .skills/doc-sections)` and no note.

House shape, one section per line:

```
<doc>[, <doc>…]: <what to spot-check> (<sensitive-path>, …)
```

The trailing group names the `doc-sensitive-paths` entries the line answers for. Upstream runs no dead-entry check on advice — advice is prose, and a checker for it would be satisfied by pasting paths into the text — so the fixed position buys three checks locally, all in `scripts/tests/test_doc_sections.py`: every doc named is a tracked file, every watched path is routed by some line, and no line routes a path the list does not watch. The routing tokens are **verbatim** `doc-sensitive-paths` entries, trailing slash included — they are keys, not prose. #314 is why they exist; it deleted `descriptors/` along with the two docs that routed it, and the routing #371 was filed with still named all three.

## Worktree submodule population

`git worktree add` never populates submodules, so a fresh worktree has an empty `skills-vendor/`.
Run `git submodule update --init --recursive` beside the `uv sync --locked` below — it is the other
half of the same bootstrap, and nothing in `worktree-create.sh` does it for you.

**Five tests fail until you do**, none of them naming the submodule as the cause — each reports only
that a vendored file is missing:

| Test | Pins |
|---|---|
| `test_doc_sections.py::test_the_snapshot_still_matches_the_vendor` | the local snapshot still matches the vendor's `DOC_SECTIONS` defaults |
| `test_doc_sections.py::test_the_vendored_gate_still_reads_this_file` | `doc-check.sh` still reads `.skills/doc-sections` (gregoryfoster/skills#284) |
| `test_doc_sensitive_paths.py::test_the_vendored_matcher_is_still_segment_based` | `doc-check.sh` still has `path_matches()` (gregoryfoster/skills#252) |
| `test_pre_ship_wrapper.py::test_real_delegate_path_resolves` | the hardcoded `shipping-work-python-fastapi/scripts/pre-ship.sh` path |
| `test_hook_registration_gate.py::test_manifest_scan_finds_the_vendored_hooks` | the `<hook>.install` manifests the timeout guard reads ([§ Hook timeouts](#hook-timeouts)) |

One further test depends on state outside the checkout for a different reason: `test_assert_venv_integrity.py::test_the_production_venv_is_intact` runs the #279 guard against `/home/exedev/usa-wa/.venv` whichever checkout invokes it, so a **worktree** suite can go red for a condition in the primary checkout. That is deliberate — a guard about to become an `ExecStartPre=` on thirteen units should be known to pass against the venv those units start from — and a red there is a live finding, repaired with `uv sync --locked` in the primary checkout ([`docs/DEPLOYMENT.md` § Shared-venv integrity](DEPLOYMENT.md#shared-venv-integrity-issue-279)). It skips where that checkout does not exist.

All five read the vendored file **on purpose** — that is how a local snapshot is kept honest against
vendor drift — so none of them can be made to pass with the submodule absent, and none should be.

`.skills/doctor.sh` repairs this on its own, but only when something invokes it: Phase 1 of the
`reviewing-*` / `shipping-*` skills (see the top of this file). A bare `uv run pytest` in a new
worktree never calls it, which is why the init belongs in the bootstrap rather than being left to
the doctor. Same shape as #296, which `test_pre_ship_wrapper.py` already carries a note about: the
checkout [`AGENTS.md`](../AGENTS.md) § Server Lifecycle *mandates* for feature work is the one that
arrives missing things, because every mechanism that would have supplied them is keyed to the
primary checkout.

## Worktree venv isolation

`.skills/worktree_venv` holds **`none`**, so `worktree-create.sh` links no `.venv` into a new worktree and says so on stderr. Provision one there with `uv sync --locked` — about 2 s against a warm cache.

**Why, concretely.** The main checkout is `usa-wa.service`'s `WorkingDirectory=`, and the default (`link`) symlinks its `.venv` into every worktree — handing them one shared *mutable* environment while isolating them in every other respect. `uv run` reinstalls the workspace project, so a single `uv run pytest` in a worktree restamps all eleven editable `.pth` files in the live service's venv to point at `.worktrees/…`. That happened twice while wiring #263 before this knob was set. The service survives it (every unit runs `uv run --frozen --no-sync`), but it is then importing from a worktree, and destroying that worktree breaks it.

The reverse direction bites too: a worktree's own `uv sync` prunes dependency groups it was not asked for, out from under the running workers.

**It is committed**, a deliberate departure from the skill's "commit only if it holds for every clone". Here it does: [`AGENTS.md`](../AGENTS.md) § Infrastructure fixes this repo as a **single-VM setup where code committed to main is the deployed code**, so the main checkout is a service working directory in every deployment this repo contemplates. An untracked knob would also be lost on a repo reset — silently restoring the failure it exists to prevent. The cost elsewhere is one `uv sync` per worktree.

## Updating skills

Daily updates are automatic via the `SessionStart` hook. To force an update mid-session:

```bash
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
git add skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
git commit -m "chore: update skills submodules"
```

After updating, re-run the symlink loops in `init-project-fastapi` SKILL.md Phase 10/11 if new skills appeared upstream.
