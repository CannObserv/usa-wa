# SocratiCode — the graph and background work on this repo (#299)

Repo-authored notes split out of [SOCRATICODE.md](SOCRATICODE.md)'s § Repo-specific notes:
when this repo's file-dependency graph can be trusted, and why a graph build or index
run must outlive the tool call that started it. The tool reference itself, and
§ Graph health's yield check, stay in SOCRATICODE.md.

## The file-dependency graph works here (#299)

**The file-dependency graph works on this repo as of SocratiCode 1.14.0 (#299).** It did not
before 1.13.0: the resolver probed `src/` and `lib/` only at the project root, so a `uv` workspace
whose distribution directory is dashed and whose module is underscored —
`packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/tenure_spans.py` — lost nearly
every import, and no rebuild fixed it. Upstream `f836e99` (gregoryfoster/skills#107,
socraticode#112) takes the import roots from the tree's `pyproject.toml` manifests instead; it
shipped in `v1.13.0` on 2026-09-07.

Measured 2026-09-16, one `codebase_graph_build` apart:

| | 1.12.0 | 1.14.0 |
|---|---|---|
| dependency edges | 13 | **1,633** |
| average dependencies per file | 0.0 | 3.2 |
| orphan files | 460+ of 492 | 51 of 510 |
| unresolved symbol edges | 81.2% | 61.8% |

Spot-checked against `grep`, both exact:
`packages/usa-wa-facts-seats/src/usa_wa_facts_seats/house/build.py` reports 24 imports and `grep`
finds 24 distinct first-party import statements; `clearinghouse_core/provenance.py` reports 83
dependents and `grep` finds 83 importing files.

**The version floor is the caveat that survives.** An empty graph answer still reads as an ordinary
"No dependency information found" rather than an error, and nothing in this repo pins the engine —
the plugin launches `npx -y socraticode`, whose npx cache version moves independently of the
plugin's (#298). So before trusting an empty answer, run `codebase_graph_status`: it names the
builder version and the last build time, and asks for a rebuild when the stored graph predates the
running engine. Anything built by an engine older than 1.13.0 is the degraded graph described
above — there, and only there, derive import edges with `grep`:

```bash
grep -rnE '^[[:space:]]*(from|import)[[:space:]]+usa_wa_adapter_' packages/*/src --include='*.py'
```

`codebase_search`, `codebase_symbol`, and the context tools were never affected.

The daily `socraticode-health.sh` `SessionStart` hook re-measures this yield (#263) and now returns
`ok`, carrying the 61.8% unresolved share as a statistic rather than a defect. The finding it exists
for is a **context artifact declared in `.socraticodecontextartifacts.json` but never indexed**,
which produces no error and no warning otherwise. See
[`docs/SKILLS.md` § SocratiCode health](SKILLS.md#socraticode-health).

## Background work outlives the tool call (#299)

**`codebase_graph_build` and `codebase_index` return before they have done anything.** Both answer
immediately — "started in the background" — and then do the work *inside the MCP server process*,
as does the file watcher's incremental update. Kill the server mid-flight and the half-finished
state persists, with nothing raised. Both failure modes below were hit in one session:

| What was killed | What persisted | What it looked like |
|---|---|---|
| a graph build | the **previous** graph | a rebuild that "succeeded" and still read 13 edges |
| a watcher-driven incremental update | `indexingStatus: in-progress`, 655/0 files | `⚠ INDEX IS INCOMPLETE` on the next launch |

Neither reports an error, and the second surfaced twenty minutes *after* the same index had been
read as `completed` 661/661 — so a status checked once is not a status that stays true while a
watcher is running.

**Check the timestamp, not the headline count.** `codebase_graph_status` carries `Last built`; the
`socraticode_metadata` Qdrant collection carries `indexingStatus` and `lastIndexedAt` per collection
(`codebase_*`, `context_*`, `codegraph_*`). An unchanged `Last built` after a "successful" rebuild
means the build never landed. The edge count alone cannot tell you.

**Close stdin and wait for the exit.** A driver script should finish with `proc.stdin.close()` and
`proc.wait()`, confirming `rc = 0`, rather than `terminate()`. Recovery is cheap once recognized —
the server auto-resumes an interrupted index on its next launch (7.9s for six outstanding files),
and an explicit `codebase_index` during that window is *refused* as "already in progress", which is
the resume working, not a failure.

**`codebase_remove` is total, not index-only.** It drops every collection for the project — the
graph and symbol graphs with it. A from-scratch code index rebuilds them unprompted, but budget for
it: the full rebuild measured here was ~110 minutes for 5,562 chunks plus ~43 for 1,013 context
chunks, embedding locally against `nomic-embed-text`.
