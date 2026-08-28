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

Prefetch query — run via `ToolSearch` at session start:

`select:mcp__plugin_socraticode_socraticode__codebase_search,mcp__plugin_socraticode_socraticode__codebase_symbol,mcp__plugin_socraticode_socraticode__codebase_symbols,mcp__plugin_socraticode_socraticode__codebase_flow,mcp__plugin_socraticode_socraticode__codebase_impact,mcp__plugin_socraticode_socraticode__codebase_graph_query,mcp__plugin_socraticode_socraticode__codebase_status,mcp__plugin_socraticode_socraticode__codebase_context,mcp__plugin_socraticode_socraticode__codebase_context_search`
