# Code exploration — tool selection

The behavioural rule (SocratiCode first for semantic questions, `grep` only for exact
strings) is in [`AGENTS.md`](../AGENTS.md#code-exploration-policy) § Code Exploration
Policy. This file carries the detail: the goal-to-tool table, the measurements behind
the broken file-dependency graph, and the session-start prefetch query.

| Goal | Tool |
|------|------|
| Where is X defined / how does Y work / what files touch Z | `codebase_search` |
| Exact string/regex match (errors, log lines, known symbols) | `grep` / `rg` |
| Blast radius of changing/deleting a file or function | `codebase_impact` |
| What does an entry point actually do? | `codebase_flow` |
| Callers and callees of a function | `codebase_symbol` |
| Imports/dependents of a file | `grep` — **not** `codebase_graph_query` (see below) |
| DB schemas, deployment topology, runbook context | `codebase_context` / `codebase_context_search` |

**The file-dependency graph does not work on this repo.** `codebase_graph_build` resolves **11 edges
across 439 files** (82.2% of symbols unresolved — 3/374 at 81.8% when first measured; the ratio has
not moved), and `codebase_graph_query` on a module with 25 imports returns "No dependency
information found". A rebuild does not fix it — the resolver does not
map `usa_wa_adapter_legislature.tenure_spans` onto
`packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/tenure_spans.py`, i.e. it cannot
follow a `uv` workspace `src` layout where the directory name is dashed and the module name is
underscored. So `codebase_graph_query`, `codebase_graph_circular`, `codebase_graph_stats`, and the
file-mode of `codebase_impact` return empty or misleading results here — treat empty output as
"tool broken", never as "no dependents". Derive import edges with `grep` instead, e.g.:

```bash
grep -rnE '^[[:space:]]*(from|import)[[:space:]]+usa_wa_adapter_' packages/*/src --include='*.py'
```

`codebase_search`, `codebase_symbol`, and the context tools are unaffected and remain preferred.
Filed upstream as gregoryfoster/skills#107; revisit this note when it is fixed.

The daily `socraticode-health.sh` `SessionStart` hook re-measures this yield and reports it as a
finding every day (#263) — expected here, not news. The finding it exists for is a **context artifact
declared in `.socraticodecontextartifacts.json` but never indexed**, which produces no error and no
warning otherwise. See [`docs/SKILLS.md` § SocratiCode health](SKILLS.md#socraticode-health).

## Manifest coverage — the drift that grows silently (#300)

That check inspects only what the manifest already **names**, so a doc absent from it is invisible to
the check and stays invisible. In #298 that hid eleven `docs/MODULES-*.md` references plus
`ARCHITECTURE.md`, `ONTOLOGY.md`, `API.md` and `DEPLOYMENT.md` — all indexable, none reachable
through `codebase_context_search`, while the manifest still described a "four-layer" repo the
layering had outgrown at #189. `MODULES-LEGISLATURE-SPANS.md` was the top hit for a representative
semantic query and could not be found.

So the comparison runs the other way too. **Every tracked Markdown file at the repo root or under
`docs/` must be covered by a declared path**, or listed in `.skills/context-artifacts-exempt` (one
path per line, `#` comments ignored — the `.skills/` knob grammar). A declared *directory* covers the
files beneath it, which is how `docs/specs/` and `docs/plans/` are handled. Package `README.md` files
are module documentation, not context artifacts, and are out of scope.

`scripts/context_manifest_drift.py` is the comparison. Two things run it:

| Entry point | When | On drift |
|---|---|---|
| `scripts/tests/test_context_manifest_drift.py` | every test run | fails the suite at the commit that introduced it |
| `.claude/hooks/context-manifest-drift.sh` | once per UTC day, at session start | prints the findings as session context; never blocks |

The hook measures the **primary checkout** (the parent of `--git-common-dir`) and locks in the common
git dir, so N worktrees of one repo produce one report a day, not N. Exemptions are checked, not
trusted: an entry naming a file that is gone, or one that has since been declared, is reported as
stale — an opt-out nobody revisits is a blindfold.

```bash
uv run python scripts/context_manifest_drift.py            # ad-hoc; 0 clean, 1 findings
CONTEXT_MANIFEST_DRIFT_FORCE=1 bash .claude/hooks/context-manifest-drift.sh   # ignore the daily lock
```

Prefetch query — **emitted verbatim into every session** by the
`socraticode-reminder.sh` `SessionStart` hook, which is a symlink into the vendored
source of truth. Read it from there rather than from a copy here:

```bash
bash .claude/hooks/socraticode-reminder.sh
```

This file used to transcribe the query, and the transcription had already gone stale —
it omitted `codebase_graph_circular`, `_stats` and `_visualize`. That is the failure
upstream removed the copies for (gregoryfoster/skills#234): a transcription of a
symlinked hook's output drifts silently, because nothing compares the two.
