# Code exploration — tool selection

The behavioural rule (SocratiCode first for semantic questions, `grep` only for exact
strings) is in [`AGENTS.md`](../AGENTS.md#code-exploration-policy) § Code Exploration
Policy. This file carries the detail: the goal-to-tool table, the measurements behind
the file-dependency graph's repair, and the session-start prefetch query.

| Goal | Tool |
|------|------|
| Where is X defined / how does Y work / what files touch Z | `codebase_search` |
| Exact string/regex match (errors, log lines, known symbols) | `grep` / `rg` |
| Blast radius of changing/deleting a file or function | `codebase_impact` |
| What does an entry point actually do? | `codebase_flow` |
| Callers and callees of a function | `codebase_symbol` |
| Imports/dependents of a file | `codebase_graph_query` (see the version floor below) |
| DB schemas, deployment topology, runbook context | `codebase_context` / `codebase_context_search` |

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

## The server is pinned, not installed per launch (#389)

The plugin's `mcp.json` is `npx -y --prefer-online socraticode@latest`, and
`--prefer-online` revalidates against the registry on **every** launch — a warm
cache is not a warm path on any day the package moved. The daily
`socraticode-health.sh` hook shells out to the same command from `SessionStart`,
concurrently with the plugin's own launch. So the expensive part of a session
start was an *install*, not an index: measured upstream on an 8 GB host, a cold
install plus server plus full index reached **1.2 G** at the cgroup and all 126
`MemoryHigh` throttle events landed in the install, none in indexing
([gregoryfoster/skills#295](https://github.com/gregoryfoster/skills/issues/295)).

This host qualifies — 7.7 GiB, **no swap**, and a production service sharing it —
so the server is installed once, deliberately, under a cap:

```bash
npm view socraticode version        # pick a literal; never @latest
systemd-run --user --scope -p MemoryHigh=1200M -p MemoryMax=1536M \
  -- npm install --prefix ~/.socraticode/pin socraticode@<version>
```

Pinned here at **1.14.0** on 2026-09-19 (457 MB under `~/.socraticode/pin`), which
is what `@latest` resolved to that day — so the pin introduced no version drift.
Confirm which path wins without launching a server:

```bash
node skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/mcp-driver.mjs resolve
# → "source": "pinned install v1.14.0 (/home/exedev/.socraticode/pin)"
```

Nothing else is configured. Absent a pin the chain is exactly what it was, so this
is opt-in and reversible by deleting the directory.

**It does not pin the session.** Claude Code cannot override a plugin's MCP
command, so the plugin keeps launching `@latest` while the driver is
deterministic. The health hook measures that gap and reports a *defect* only at a
minor or major difference — a patch apart is the intended steady state, since a
pin is meant to lag. Re-pin as a decision, not on a schedule: the reason to pin
was to stop an unattended launch from installing.

Host-side memory protection — `MemoryLow=`/`OOMScoreAdjust=` on the serving unit,
`vm.min_free_kbytes`, earlyoom — is in
[`docs/DEPLOYMENT.md` § Memory pressure](DEPLOYMENT.md#memory-pressure-issue-389).
A cap on a session process cannot substitute for it.
