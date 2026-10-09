# Dead-code detection — the `pruning-dead-code` skill

- **Date:** 2026-10-09
- **Status:** approved in conversation; written spec under review
- **Issues:** usa-wa#475 (driver), follows #471 / PR #474
- **Cross-repo:** most of the build lands upstream in `gregoryfoster/skills`; usa-wa is the
  first adopter

## Goal

Catch production code that has lost its last production caller, in the PR that orphans it,
across every cohort project — not only in usa-wa, and not only by hand after a large
retirement. #471 was found by a reviewer reading #470; the hand scan behind it was wrong in
both directions (§ Background). Dead code inflates the whole-tree coverage figure (#413) and
keeps the `docs/MODULES-*.md` references describing it as live.

Delivered as an upstream skill, `pruning-dead-code`, that installs the check into a new or
existing project and runs triage sweeps, plus an auto-detected advisory block in both `uv`
pre-ship gates so an installed project is covered with no further wiring.

## Background — what vulture is and is not

[vulture](https://github.com/jendrikseipp/vulture) parses each file to an AST, collects
every defined name and every used name, and reports definitions whose name is never used.

- **Name-based, not reference-resolving.** A use of the name anywhere counts for every
  definition of that name, so a collision masks dead code (the two
  `election_year_from_resource_id` definitions shield each other once either gains a
  caller). It cannot see a cascade: a function reachable only from a dead function is not
  reported until the dead one is deleted. Hence the triage fixpoint (§ Triage).
- **Confidence is fixed per finding kind**, not a measured certainty: 60% for unused
  definitions, 90% for imports, 100% for unreachable code. On usa-wa every finding is 60%
  and nothing is above it, so `min_confidence` is not a useful filter here.
- **Blind to** decorator registration, framework callbacks, `getattr`, and names written as
  strings in config.
- **Whitelist** = any Python file passed as a path; names used in it count as used.
  Settings live in `[tool.vulture]` in `pyproject.toml`.
- **Exit codes** (2.16, measured): 0 clean, 1 invalid input, 2 bad arguments, 3 dead code.
  A file with a syntax error prints a line into the findings stream and still exits 3 when
  another file has findings, so the exit code alone cannot prove the scan was whole.
- **Cost:** 0.6 s over usa-wa's whole production scope.

## Measurement on `main` (c3d7e14)

`vulture packages/*/src scripts/*.py packages/usa-wa-pipeline/dbt --min-confidence 60`:
155 findings.

| Bucket | Count | Treatment |
|---|---|---|
| unused variable / attribute — Pydantic, SQLAlchemy `Mapped`, settings fields | 99 | out of scope: filtered by kind |
| FastAPI routes (`@router.get`, `@health_router.get`) | 18 | `ignore_decorators` |
| `clearinghouse_core.testing` helpers | 5 (+4 attributes counted above) | `exclude` — consumed by tests by design |
| remaining functions, classes, methods, properties | 33 | the seed backlog plus framework hooks |

## Corrections and additions to #475 as filed

1. **False positives the issue did not list:** names referenced as strings in config —
   `ColorMessageFilter` is named in `packages/usa-wa-api/src/usa_wa_api/log_config.json`
   (`"()"`, #155) and is live; SQLAlchemy `TypeDecorator` hooks (`process_bind_param`,
   `process_result_value`); properties that may be serialized.
2. **Test-only callers are dead.** Scanning production only (as the issue scopes it) makes a
   symbol called only from tests a candidate. Deleting it deletes those tests. 15 of the 20
   seed entries are test-only.
3. **Cadence:** none of the three options as written. A warn-only block in the pre-ship gate
   fires in the PR that orphans a symbol, which is #470's exact failure mode, and attributes
   it for free (it is this branch). The systemd timer is host-specific and needs a GH token in
   a unit; `curating-context` is the wrong concern. The tracking-issue output moves to an
   on-demand sweep.
4. **Variables and attributes are out of scope** (decided). Field deadness is a schema
   question, not a call-graph one, and is 99 of 155 findings.

## Decisions

| # | Decision |
|---|---|
| D1 | Skill name `pruning-dead-code` (gerund, no suffix; Python-only until a sibling stack needs it) |
| D2 | The vendored pre-ship gate change is in scope — both `shipping-work-python-fastapi` and `shipping-work-python-click` |
| D3 | Unused variables, attributes and imports are filtered out; imports are ruff F401's |
| D4 | Gate integration is **approach A**: the gate invokes vulture inline, enabled by the presence of `[tool.vulture]` in the root `pyproject.toml`. Rejected: B (gate calls the skill's `scan.sh` — a cross-skill dependency that vanishes silently in a per-file-symlink consumer); C (a `.skills/` opt-in knob — redundant with the signal `pyproject.toml` already carries) |
| D5 | Advisory forever: no state of the dead-code block changes the gate's exit code |
| D6 | Allowlist entries are import-form with a mandatory reason; `_.name` form is rejected |

## Components

### Upstream — `skills/pruning-dead-code/`

- **`SKILL.md`** — two modes.
  - *install* (new project or retrofit): add `vulture` as a dev dependency; write
    `[tool.vulture]` (`paths` = production source roots plus the allowlist, `exclude`,
    `ignore_decorators` per detected framework, `min_confidence = 60`); generate a draft
    with `--make-whitelist`, convert it to import form, and give every entry a reason; copy
    the allowlist test; add a ruff B018 per-file ignore for the allowlist only when the
    project selects `B`; document the commands in the project's command reference. All
    wiring is shown as a reviewable diff, never applied silently.
  - *sweep*: run `scan.sh --json --attribute`, triage each candidate (§ Triage), delete to
    fixpoint, update the project's module docs, maintain the tracking issue.
  - Iron law: no deletion without the triage checklist; no allowlist entry without a reason.
- **`scripts/scan.sh`** — runs `uv run vulture` (project config), applies the kind filter.
  Exit 0 clean, 1 findings, 2 tooling failure (vulture exit 1/2, vulture not installed, or
  any output line that is not a finding). `--json` emits
  `[{kind, name, file, line, confidence, likely_orphaned_by?}]`. `--attribute` fills
  `likely_orphaned_by` from `git log -S<name> -1 --format=%h -- . ':!<defining file>'` —
  "likely" because pickaxe also matches docs and comments. `--help` per repo convention.
- **`references/false-positives.md`** — per framework: FastAPI route and handler
  decorators, Click/Typer commands, Pydantic validators and `computed_field`, SQLAlchemy
  `TypeDecorator` hooks, string-dotted-path config (logging `dictConfig` `"()"`),
  `[project.scripts]` entry points, test-support modules shipped under `src`.
- **`references/triage.md`** — the checklist, the fixpoint loop, the tracking-issue format.
- **`assets/test_vulture_whitelist.py`** — copied into the consumer (§ Allowlist).

### Upstream — cross-skill edits

- Both `uv` pre-ship gates gain the dead-code block (§ Gate block).
- `init-project-fastapi` gains a phase that runs `pruning-dead-code` install mode.
- README skill inventory; the self-budget ratchet covers the new `SKILL.md` and references.

### usa-wa

- Submodule bump; install mode run; `docs/COMMANDS.md` entry; module-doc updates as
  symbols are deleted. The project-local `scripts/pre-ship.sh` wrapper delegates to the
  vendored gate, so it inherits the block with no edit.

## Allowlist

`vulture_whitelist.py` at the repo root, listed in `[tool.vulture].paths`:

```python
from clearinghouse_core.db.ulid import ULIDType
from clearinghouse_core.logging import ColorMessageFilter

ULIDType.process_bind_param  # SQLAlchemy TypeDecorator hook, called by the ORM
ColorMessageFilter  # named as a string in log_config.json "()" (#155)
```

The copied test enforces, over every expression line:

1. a trailing `# <reason>` that is not vulture's generated `unused <kind> (<file>:<line>)`;
2. no `_.name` entries;
3. the file executes under `runpy.run_path` — an `ImportError` or `AttributeError` is a
   stale entry and fails.

Import-form entries are ruff-clean under usa-wa's rule set (E, F, I, W, UP, ASYNC, S);
`Name.attr` lines trip B018 only where `B` is selected (measured).

## Kind filter (one definition, two copies)

- A **finding** is a line matching `^<path>:<line>: <message> (<NN>% confidence)$`.
- Dropped: messages beginning `unused variable`, `unused attribute`, `unused import`.
- Kept: every other finding (function, class, method, property, unreachable code,
  unsatisfiable condition).
- **Any non-blank line that is not a finding is a scan failure.**

The gate and `scan.sh` each carry the filter; a behavioural twins test feeds both one
fixture stream and requires identical classification.

## Gate block

Both variants, placed after the test phase and before `Pre-ship checks passed.`, invoked
through the existing `uv_run` (so `.skills/pre-ship-uv-args` applies). Runs on every
invocation — not behind the per-SHA test stamp.

| State | Output | Gate exit |
|---|---|---|
| no `[tool.vulture]` table in root `pyproject.toml` | nothing | unchanged |
| configured, vulture not importable | `WARN: [tool.vulture] configured but vulture is not installed — dead-code check did not run` | unchanged |
| vulture exit 1/2, or a non-finding line | `WARN: dead-code scan failed — check did not run` + output | unchanged |
| findings after filter | `WARN: N new dead-code candidate(s)` + lines + triage pointer | unchanged |
| clean | `No new dead-code candidates.` | unchanged |

The allowlist absorbs the known set, so every reported finding is this branch's doing; no
baseline diff is needed. A failing test phase exits before the block runs.

## Triage

1. Search every non-Python surface for the name as a string: JSON/YAML/TOML config,
   `[project.scripts]`, systemd units, shell scripts, dbt models (dbt Python models are real
   callers and are in `paths`).
2. A docstring or Markdown mention is not a caller.
3. A test-only caller is not a caller: delete the symbol and the tests that exist only for it.
4. A deliberate keep (an operator REPL tool, a documented hook) is allowlisted with its
   reason.
5. Delete, re-run `scan.sh`, repeat until no new candidate appears (cascades).
6. Update the module docs that described the deleted symbols.

**Tracking issue** (sweep only): one open issue located by the body marker
`<!-- pruning-dead-code:tracking -->`, its body rewritten each sweep as a table — symbol,
file:line, kind, likely orphaning commit, triage status. Updated through the REST API
(`gh api -X PATCH`), since `gh issue edit` fails on the deprecated-projects GraphQL field.

## Seed backlog — pre-triage evidence

Reference counts on `main` (not yet checked for `getattr` or other dynamic callers):

| Class | Symbols |
|---|---|
| live — allowlist | `logging.ColorMessageFilter` (string ref in `log_config.json`) |
| no caller anywhere (src, tests, config) | `jurisdictions.wa_jurisdiction_facts`, `seats.ld_slug`, `serving.load.datapackage_fields`, `api.v1.schemas.parse_ulid_path` |
| test-only callers | `roster_pdf.audit.audit_roster`, `roster_pdf.normalize.parse_district_pages`, `roster_pdf.resources.revision_from_resource_id`, `sos.{filings,results}.resources.election_year_from_resource_id`, `sponsors.roster_hygiene.stale_member_ids`, `committees.succession_store.superseded_events`, `committees.probe_extent.probe_floor`, `meetings.windows.parse_meetings_resource_id`, `committee_succession.find_succession_cycles`, `source_coverage.known_gaps`, `database.reset_engine`, `parties.tally_party_tokens`, `backup.raw_mirror.{local_inventory,unrecognized_files}`, `serving.load.ensure_serving_schema` |

Candidates outside the seed list (also to triage): `job.require_session_factory`,
`transport.parse_historical_committee_members`, the `ULIDType` hooks, and the properties
`floor_year`, `ceiling_year`, `party_tokens`, `dimensions`, `known_gaps`. The three
`configure_*_rate_limit` functions are #471's recorded deliberate keeps.

## Testing

### Upstream (TDD)

1. **Gate behaviour** — new `tests/structural/test_pre_ship_dead_code.py`, parametrized over
   both variants with the existing stub-`uv` pattern
   (`tests/structural/test_pre_ship_unresolved_head.py`): one case per gate-block row; exit 0
   in every row on a green suite; a failing test phase exits 1 and never reaches the block;
   dropped kinds never surface; a syntax-error line with exit 3 yields "scan failed", not a
   candidate list; `.skills/pre-ship-uv-args` reaches the vulture call.
2. **Filter twins** — behavioural: one fixture stream through both copies, identical
   classification.
3. **`scan.sh`** — real vulture on fixture packages: exits 0/1/2, `--json` shape,
   `--attribute` naming the commit that removed a caller in a fixture git repo.
4. **Allowlist asset** — passes a valid file; fails a missing reason, a generated reason, a
   `_.name` entry, a stale import.
5. **Registration** — existing structural tests (naming, schema, references, self-budget,
   script `--help`, checked temp writes) pass with the new skill.

### usa-wa retrofit

- Seed entries enter the allowlist with the reason `seed backlog #475 — untriaged` (accepted
  by the asset test) so the first gate run is clean; sub-project 3 removes them.
- `scripts/pre-ship.sh` on a clean tree prints `No new dead-code candidates.`
- Detection proof: on a throwaway branch, delete one live caller; the gate warns; discard.
- The allowlist test runs in the unit tier (no database) and counts toward the coverage gate.

## Delivery

| Sub-project | Repo | Closes |
|---|---|---|
| 1. Skill, gate block in both variants, `init-project-fastapi` phase | `gregoryfoster/skills` (own issue) | — |
| 2. Retrofit: submodule bump, install, `docs/COMMANDS.md` | usa-wa | #475 done-when 1–2 |
| 3. Seed-backlog triage and deletions | usa-wa | #475 done-when 3 |

Each sub-project gets its own plan and PR; 2 depends on 1 merging upstream.

## Out of scope

A systemd timer; a failing ratchet; variables, attributes and imports; non-Python stacks
(knip for JS/TS and shipmonk dead-code-detector for PHP are the likely tools when a cohort
project needs one); reachability analysis beyond vulture's name matching.
