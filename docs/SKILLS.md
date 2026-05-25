# Agent Skills

Skills are reusable agent instructions. `usa-wa` consumes two upstream catalogs (vendored as git submodules under `skills-vendor/`) and exposes them through two discovery directories:

- **`skills/`** — agentskills.io convention (one symlink per skill, plus any local overrides).
- **`.claude/skills/`** — Claude Code discovery directory (mirrors every entry in `skills/`).

The vendor → symlink → discovery layout means the project carries no skill source code of its own (except local overrides) and stays in sync with upstream via submodule updates. The `UserPromptSubmit` hook in [`.claude/settings.json`](../.claude/settings.json) runs `git submodule update --remote --merge` once per day to keep both vendors current.

## Vendor sources

| Submodule | Upstream | Purpose |
|---|---|---|
| `skills-vendor/gregoryfoster-skills` | [gregoryfoster/skills](https://github.com/gregoryfoster/skills) | CannObserv-specific workflows (init, shipping, reviewing) |
| `skills-vendor/obra-superpowers` | [obra/superpowers](https://github.com/obra/superpowers) | General-purpose agent skills |

## Vendor skills (from gregoryfoster-skills)

| Skill | What it does |
|---|---|
| `init-project-fastapi` | Bootstrap a new FastAPI service (this project's origin). |
| `managing-skills` | Add/update/audit skills across vendors and overrides. |
| `orchestrating-issue-backlog` | Triage and sequence open GitHub issues into actionable work. |
| `reviewing-architecture` | Architectural review of a design doc or large change. |
| `reviewing-code` | Generic per-PR code review. |
| `reviewing-code-python-fastapi` | Python/FastAPI-stack code review (use this in this repo). |
| `reviewing-code-php` | PHP-stack code review. |
| `reviewing-code-python-click` | Python/Click-stack code review. |
| `shipping-work` | Generic ship workflow (test, commit, push, open PR). |
| `shipping-work-python-fastapi` | Python/FastAPI ship workflow with `pre-ship.sh` (use this in this repo). |
| `shipping-work-php` | PHP ship workflow. |
| `shipping-work-python-click` | Python/Click ship workflow. |
| `using-git-worktrees` | Worktree-based branch workflow for parallel work. |
| `writing-plans` | Drafting an implementation plan in `docs/plans/` before coding. |

The Python/FastAPI variants (`reviewing-code-python-fastapi`, `shipping-work-python-fastapi`) are the canonical workflows for this repo; their stack-neutral counterparts are kept for cross-cohort reference.

## Local overrides

Local overrides live in `skills/<name>/` directly (not symlinks). Each override must declare `overrides: <vendor>/<upstream-skill-name>` and `override-reason:` in its frontmatter `metadata` block.

| Override | Reason |
|---|---|
| `skills/brainstorming/` | Project-specific narrative content (the upstream skill is generic). |

Add a thin override only when the project genuinely diverges from the upstream behavior — see the `init-project-fastapi` SKILL.md "Phase 10 — `skills/` directory" section for the conditions that warrant a fork.

## Updating skills

Daily updates are automatic via the `UserPromptSubmit` hook. To force an update mid-session:

```bash
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
git add skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
git commit -m "chore: update skills submodules"
```

After updating, re-run the symlink loops in `init-project-fastapi` SKILL.md Phase 10/11 if new skills appeared upstream.
