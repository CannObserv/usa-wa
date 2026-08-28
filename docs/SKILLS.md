# Agent Skills

Skills are reusable agent instructions. `usa-wa` consumes two upstream catalogs (vendored as git submodules under `skills-vendor/`) and exposes them through two discovery directories:

- **`skills/`** — agentskills.io convention (one symlink per skill, plus any local overrides).
- **`.claude/skills/`** — Claude Code discovery directory (mirrors every entry in `skills/`).

The vendor → symlink → discovery layout means the project carries no skill source code of its own (except local overrides) and stays in sync with upstream via submodule updates. The `SessionStart` hook in [`.claude/settings.json`](../.claude/settings.json) runs the vendored [`skills-submodule-update.sh`](../.claude/hooks/skills-submodule-update.sh) to keep both vendors current — once per UTC day, on `main` only, auto-committing the pointer bump. It also (re)installs `.skills/doctor.sh` on **every** session, outside the daily lock. Two further `SessionStart` hooks are registered there: the SocratiCode prefetch reminder, and the daily health check ([§ SocratiCode health](#socraticode-health)).

## `.skills/doctor.sh` — the preflight

Phase 1 of every `reviewing-*` / `shipping-*` skill runs `{ [ ! -x .skills/doctor.sh ] || bash .skills/doctor.sh; }`. The doctor walks the `skills/*` symlinks, auto-runs `git submodule update --init --recursive` when any dangle, and prints an actionable error otherwise — so a fresh `git worktree add` or a shallow CI clone doesn't hit "No such file or directory" on a skill invocation.

It is a **file copy**, not a symlink, deliberately: a symlink into `skills-vendor/` would itself dangle in exactly the uninitialized-submodule state the doctor exists to repair. Since gregoryfoster/skills#84 the doctor re-syncs itself from the vendored source on every mutating run, so upstream fixes arrive without a manual reinstall. It **must stay committed** — the hook installs it into the working tree, but only ever commits `skills-vendor/` and `.skills/doctor.sh`; anything untracked leaves CI and fresh worktrees with no doctor and the preflight silently short-circuits.

```bash
bash .skills/doctor.sh --version   # installed copy's stamp
bash skills-vendor/gregoryfoster-skills/skills/managing-skills/scripts/install-doctor.sh   # manual (re)install
```

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

A local override is a full copy (not a symlink) at `skills/<name>/`, declaring `overrides: <vendor>/<upstream-skill-name>`, `override-reason:`, and — since gregoryfoster/skills#238 — a `synced-from: "<repo> <tag> (<commit>)"` recording the vendor commit last synced from. Without that last key the doctor reports the fork as un-assessable, which is the same failure as not detecting drift at all.

`skills/brainstorming/` was the one override and is now a symlink (#263). It claimed "project-specific narrative content", but it was byte-identical to obra-superpowers **v5.1.0** apart from its three frontmatter lines — the project content it was forked to hold was never added. Meanwhile upstream moved 810 lines across 8 files (v6.3.0's three-path router), none of which the repo was getting. That is the drift mode the `synced-from:` check exists to surface, caught here in its terminal form: a fork with nothing in it, silently pinning a skill at the version vendored on day one (2026-05-25) through seven upstream releases (v6.0.0 → v6.3.0).

**The lesson, not just the fix:** an override costs a permanent manual re-sync obligation, so fork only when the project genuinely diverges *today* — never speculatively, to hold content someone might add later. See the `init-project-fastapi` SKILL.md "Phase 10 — `skills/` directory" section for the conditions that warrant one.

## Local script overrides

Every `reviewing-*` / `shipping-*` skill resolves its scripts by probing `scripts/` **before** the skill's own directory, so a project-local `scripts/<name>.sh` wins with no skill edit. One exists:

| Script | Why |
|---|---|
| [`scripts/pre-ship.sh`](../scripts/pre-ship.sh) | Loads the two env files, then `exec`s the vendored gate. The gate runs the full suite, whose `db`-marked majority needs `TEST_DATABASE_URL`, so on a clean shell its test phase died wholesale (#172; pre-#185 it died at conftest import). This is the override point the vendored script documents. |

**It is a wrapper, not a fork — keep it that way.** The ~200 lines upstream owns (per-SHA stamp cache, `pytest-cov` detection, the JS block, the zombie preflight) stay in one place and keep improving without a merge; a copy would drift silently on every submodule bump. Pinned by [`scripts/tests/test_pre_ship_wrapper.py`](../scripts/tests/test_pre_ship_wrapper.py), whose last test fails if the vendored delegate path moves. Upstream ask to make this recipe the sanctioned idiom (it currently says "keep a thin local fork", and only 1 of the 4 `shipping-work*` variants says even that): [gregoryfoster/skills#105](https://github.com/gregoryfoster/skills/issues/105).

## SocratiCode health

Adding an artifact to `.socraticodecontextartifacts.json` **does not index it**. Nothing reacts to a manifest edit: `codebase_context_search` answers from indexed artifacts only, and silently answers without the missing one — no error, no warning, and `codebase_status` stays green at the top while reporting the shortfall in a line nobody reads (#263, upstream [gregoryfoster/skills#214](https://github.com/gregoryfoster/skills/issues/214)).

`.claude/hooks/socraticode-health.sh` is the detector — a `SessionStart` hook symlinked through `skills/init-socraticode/` into the vendor, wired in [`.claude/settings.json`](../.claude/settings.json). It runs at most once per UTC day **per project** (the lock lives in the common `.git`, so N worktrees produce one report a day, not N), is silent when there is nothing to report, and exits 0 on every path so it can never block a session. It **reports; it never repairs** — no re-index, no `docker start`, no file edit.

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
| `worktree_venv` | unrelated — see [§ Worktree venv isolation](#worktree-venv-isolation) |

The weekly run recovers ground; the **write guard** stops regrowth between runs. It is a `PostToolUse` hook on `Edit|Write|MultiEdit`, wired in [`.claude/settings.json`](../.claude/settings.json), and it is advisory only — always exits 0, and stays silent unless an edit *both* pushes a context-surface file past its budget *and* increases it since `HEAD`, so a curation run is never nagged. `docs/plans/`, `docs/specs/`, and `docs/research/` are excluded as archival at any depth (so are `audits/` and `archive/`, which this repo does not currently have).

```bash
bash skills/curating-context/scripts/measure-context.sh --exact   # exact counts (needs ANTHROPIC_API_KEY; read from the repo-root .env)
bash skills/curating-context/scripts/prove-no-loss.sh --base main # assert a curation relocated rather than dropped
```

`.claude/hooks/context-budget-guard.sh` is a symlink through `skills/curating-context/` into the vendor submodule, so on an uninitialized checkout it dangles and every edit fails the hook until the submodule is initialized — `.skills/doctor.sh` heals it, since the chain routes through `skills/*`, which the doctor's scan covers (upstream: [gregoryfoster/skills#99](https://github.com/gregoryfoster/skills/issues/99)).

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
