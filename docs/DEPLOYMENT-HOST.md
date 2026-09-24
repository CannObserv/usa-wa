# Deployment — host hazards

Split out of [`DEPLOYMENT.md`](DEPLOYMENT.md), which keeps the unit table, alerting, DB
roles and the lifecycle table. These are the ways the shared host breaks units that are
themselves correct — a feature branch left checked out (#87), a worktree restamping the
shared venv (#279), memory exhaustion (#389), a full disk (#394) — and what guards each.
The disk-GC commands came here from [`COMMANDS.md`](COMMANDS.md).

## Main-only checkout (issue #87)

**Main-only checkout — enforced (issue #87).** The prod checkout at
`/home/exedev/usa-wa` must stay on `main`: every code-running prod `.service`
(serving + oneshots + migrate) carries `ExecStartPre=…/scripts/assert-main-checkout.sh`,
so a unit **refuses to start** off a non-main
(or detached) checkout — loud in the journal, and for the `OnFailure=`-wired
oneshots an operator email. This closes the #84 hole: the PDC timer ran unmerged
`feat/79` code purely because the repo was left checked out on that branch (the
timer runs `uv run --frozen --no-sync` from whatever is checked out — no human
sequencing error involved). Convention alone enforced nothing. Do **feature work
in a git worktree** (see the `using-git-worktrees` skill), leaving the prod
checkout on `main`. `USA_WA_DEPLOY_BRANCH` overrides the expected branch for a
non-standard host. The notify handler (`usa-wa-notify-failure@.service`) is
exempt (it's the alerting path); timers carry no guard (they run no code, only
activate their guarded `.service`). The serving unit (`usa-wa`) carries a
widened `StartLimitIntervalSec=300`/
`StartLimitBurst=10` so an off-main checkout — which fails the guard on every
`Restart=` attempt — settles into `failed` instead of looping forever (a
transient dependency blip under ~50s still self-heals). **Recovery after an
off-main wedge:** returning to `main` doesn't auto-restart a `failed` unit — the
normal deploy (`systemctl restart …`) clears it; a bare `reset-failed` + `start`
also works. `test_unit_ordering.py` asserts the guard is present on every
code-running service, cross-checks the on-disk set (so a new service can't
silently omit it), and asserts every `Restart=` unit's start-limit window is
wide enough to bound the loop (`StartLimitIntervalSec >= RestartSec * StartLimitBurst`).

## Shared-venv integrity (issue #279)

The venv `/home/exedev/usa-wa/.venv` is shared by every unit, and #279 is the way
it silently stops being the production one. The prod checkout is both
`usa-wa.service`'s `WorkingDirectory=` and the parent of every worktree; the
worktree skill's default symlinked that `.venv` into each new worktree, and `uv
run` reinstalls the workspace project — so one `uv run pytest` inside a worktree
restamped **all** the editable installs in the live venv at the worktree's paths:

```
- usa-wa-common==0.1.0 (from file:///home/exedev/usa-wa/.worktrees/docs-276-runbook-ordering/packages/usa-wa-common)
+ usa-wa-common==0.1.0 (from file:///home/exedev/usa-wa/packages/usa-wa-common)
```

Nothing looked wrong: the gate ran green, the PR merged, and the running process
kept serving from modules it had already imported. It detonated at the next
`systemctl restart usa-wa`, days later, after the worktree was gone — and it
detonated naming the logging config rather than the venv:

```
ModuleNotFoundError: No module named 'clearinghouse_core'
ValueError: Cannot resolve 'clearinghouse_core.logging.build_json_formatter'
ValueError: Unable to configure formatter 'json'
```

**The cause is closed.** `.skills/worktree_venv` holds `none`, so worktrees get no
linked venv at all (docs/SKILLS.md § Worktree venv isolation) — provision one per
worktree with `uv sync --locked`. Neither older guard covers this direction:
`--frozen --no-sync` (#30) stops a *unit start* from mutating the venv, and
`assert-main-checkout.sh` (#87) guards the checked-out *branch*.

**The guard.** [`scripts/assert-venv-integrity.sh`](../scripts/assert-venv-integrity.sh) is
wired as the second `ExecStartPre=` on all thirteen code-running `.service` units,
directly after the #87 branch guard — same exemption (`usa-wa-notify-failure@`,
the alerting path) and the same cross-check in `test_unit_ordering.py`, so a new
service either carries both or is an explicit exemption. Branch guard first
because it answers the prior question: off-main the venv legitimately points
elsewhere, and a venv finding reported first sends the operator after a symptom.

Unit start is the right place and a pre-commit gate is not. The corruption is
committed by a *worktree*, whose own venv a gate running there would check
instead; and it surfaces at a start that may be days later. That start is the
moment the damage becomes visible, so it is the moment worth naming it.

It reads every `*.dist-info/direct_url.json` in the venv — PEP 610's record of where
each install came from, and exactly what `uv sync --locked` rewrote to repair
#279 — and requires each editable one to resolve to `<root>/packages/<member>`.
That is an allowlist rather than a check that the path is under the root, because
a worktree *is* under the root: `/home/exedev/usa-wa/.worktrees/<slug>/packages/…`
passes the obvious test and is the very state being caught. It fails closed on a
missing `.venv` and on a venv carrying no editable installs at all (a unit
starting against that raises the same `ModuleNotFoundError`).

Run it by hand any time — it writes nothing and touches no network:

```bash
bash /home/exedev/usa-wa/scripts/assert-venv-integrity.sh && echo intact
```

`USA_WA_DEPLOY_ROOT` overrides the checkout root for a non-standard host.

**Recovery**, once a unit refuses to start on it (journal: `assert-venv: refusing
to start — …`). Returning the venv to health does not auto-restart a `failed`
unit, exactly as with the #87 guard:

```bash
cd /home/exedev/usa-wa
uv sync --locked
sudo systemctl reset-failed usa-wa
sudo systemctl restart usa-wa
curl -s http://127.0.0.1:8000/health    # {"status":"ok","build":"..."} — /health, not /api/v1/health
```

Every code-running unit is affected the same way, not just `usa-wa`: the
daily/weekly timers and `usa-wa-migrate` all start through `uv run`.

## Memory pressure (issue #389)

7.7 GiB, **no swap**, one production service and interactive agent sessions on the
same host. That combination fails in an unusual way: past the ceiling the kernel
does not reliably kill anything, it fails *atomic* allocations in unrelated
processes (`tailscaled`, `ksoftirqd`) while the production service starves. On
`CannObserv/broker` — same shape, 8 GB — the bus was effectively down 57m 48s with
nothing OOM-killed ([gregoryfoster/skills#295](https://github.com/gregoryfoster/skills/issues/295)).

**Measured here, 2026-09-19.** `preflight.sh --check` passed on memory (7.7 GiB,
above the 4 GiB warn) and flagged the absent swap. `usa-wa.service` read
`oom_score` **668**; three concurrent SocratiCode MCP servers, launched from
`npx` caches under two *different* hashes, read **676–679**. Cause and victim
within noise of each other. Broker's report that exe.dev session processes sit at
`oom_score_adj` -1000 and so can never be picked does **not** reproduce here:
`exe-init` reads 0, only `sshd` reads -1000, and session-launched servers inherit
0. `postgresql.service` already ships at -900 from the Debian packaging.

Four changes, in the order they take effect:

| Layer | Change | Where it lives |
|---|---|---|
| Don't create the spike | SocratiCode pinned to a pre-installed 1.14.0 instead of `npx … @latest` per launch | `~/.socraticode/pin` — [docs/SOCRATICODE.md § The server is pinned](SOCRATICODE.md#the-server-is-pinned-not-installed-per-launch-389) |
| Keep the kernel's reserve | `vm.min_free_kbytes` 11399 → **65536** (~64 MiB, ~0.8% of RAM) | `/etc/sysctl.d/60-usa-wa-memory.conf` |
| Kill the cause before the kernel stalls | **earlyoom** 1.7, `--prefer '^(node\|npm\|esbuild)$'`, `--avoid '^(uv\|uvicorn\|postgres\|sshd\|systemd\|dockerd\|tailscaled)$'` | `/etc/default/earlyoom` |
| Protect the victim | `MemoryLow=256M` + `OOMScoreAdjust=-500` on `usa-wa.service`, **plus `MemoryLow=1G` on `system.slice`** | `deploy/usa-wa.service` + `deploy/system.slice.d/`, pinned by `test_unit_ordering.py` and `test_memory_protection.py` |

Two details that are easy to get wrong:

- **The serving unit's process name is `uv`, not `uvicorn`.** `ExecStart` is `uv
  run … uvicorn`, so `comm` is `uv` — an earlyoom `--avoid` regex written for
  `uvicorn` protects nothing. Both are listed.
- **With zero swap, earlyoom's swap condition is always satisfied** (`0 <= 10%`),
  so the memory threshold alone governs. That is the intent here; on a host with
  swap, both must be below their minimum before it acts.

`MemoryLow` is a *reservation*, not a limit — systemd never refuses an allocation
because of it. The unit's measured peak is ~62 MB, so 256M is headroom.

**`MemoryLow=` on the unit alone reserves nothing.** A cgroup's effective
`memory.low` is capped by its ancestors', and `system.slice` defaults to `0` —
measured here on 2026-09-19, with cgroup2 mounted `rw,relatime` (no
`memory_recursiveprot`) and `DefaultMemoryLow` unset, so
`min(256M, 0)` was **0** while the unit reported the directive as set. The parent
protection is `deploy/system.slice.d/10-usa-wa-memory.conf`; without it the unit
half is decorative. `test_memory_protection.py` asserts the slice's reservation
covers the unit's, so raising one without the other fails the gate.

### Installing the host artifacts

Three of the four layers live under `deploy/`, mirroring their install paths, and
are copied into place the same way the units are — root-owned copies, never
symlinks:

```bash
sudo mkdir -p /etc/systemd/system/system.slice.d
sudo cp deploy/system.slice.d/10-usa-wa-memory.conf /etc/systemd/system/system.slice.d/
sudo cp deploy/sysctl.d/60-usa-wa-memory.conf /etc/sysctl.d/
sudo cp deploy/default/earlyoom /etc/default/earlyoom
sudo systemctl daemon-reload && sudo sysctl --system && sudo systemctl restart earlyoom
```

A fifth landed with #394, on the same copy-don't-symlink rule — the journal cap:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp deploy/journald.conf.d/10-usa-wa-journal-cap.conf /etc/systemd/journald.conf.d/
sudo systemctl restart systemd-journald
```

`/etc/systemd/journald.conf` sets no `SystemMaxUse=`, so the journal grows to
journald's default 10% of the filesystem (~2.5 G here) and had drifted to 695 M
before #394. The cap is why `usa-wa-disk-gc.service` does not vacuum the journal
and therefore needs no privilege.

The fourth, the SocratiCode pin, is a per-host `npm install` and is not a repo
artifact — [docs/SOCRATICODE.md § The server is pinned](SOCRATICODE.md#the-server-is-pinned-not-installed-per-launch-389).

### Verifying

```bash
systemctl show usa-wa.service -p MemoryCurrent -p MemoryPeak -p MemoryLow -p OOMScoreAdjust
cat /sys/fs/cgroup/system.slice/memory.low        # must be non-zero, else the unit's is inert
systemctl status earlyoom                          # hourly `mem avail:` report in the journal
tr '\0' '\n' < /proc/$(systemctl show earlyoom -p MainPID --value)/cmdline
sysctl vm.min_free_kbytes
```

## Host maintenance (#394)

Not job-harness CLIs — plain bash, no venv, no database. Both exist because the
25 GB root volume hit 98% with 565 MB free and killed a `pytest` run on ENOSPC,
and because nothing on the box reported it beforehand.

```bash
# Report: free space, what is reclaimable, per-tier repo sizes. Removes nothing.
scripts/disk-gc.sh
scripts/disk-gc.sh --json                  # machine-readable, one object

# Reclaim, then report. What prod runs daily at 05:45 UTC (usa-wa-disk-gc.timer).
scripts/disk-gc.sh --prune
```

Exit `0` healthy or warning, `1` free space under the fail floor (→ operator
email via `OnFailure=`), `2` tooling. Thresholds are `DISK_GC_WARN_BYTES`
(default 2 GiB) and `DISK_GC_FAIL_BYTES` (1 GiB).

**What it prunes** — superseded VS Code server builds and `code-*` CLI binaries,
Claude extension versions that are neither live nor named by `extensions.json`
(#399), Claude plugin-cache versions that are not the installed one, and
`~/.npm/_npx` trees. The reclaimable copy and the in-use one are siblings in the
same directory, so a size-or-mtime heuristic would delete a running editor's server;
liveness is the discriminator, read from `/proc/*/` `cmdline`, `cwd`, `exe`
**and** `maps` — a process started by a relative path names its tree in none of
its argv, and a loaded native addon (#399) in none of the other three.

The manifest tiers (`extensions.json`, `installed_plugins.json`) prune only when
the manifest names a version on disk; otherwise they remove nothing, and warn (#400).

Plugin-cache liveness adds Claude Code's `.in_use/<pid>` markers (#407): a
session using a version names it in no `/proc` entry. Only a marker proven dead
(pid gone, or `procStart` mismatch) releases one; an unjudgeable one keeps it,
with a warning. Claude Code's own sweep deletes a dropped version after 14 days;
this tier is the faster backstop.

A `DISK_GC_GRACE_MINUTES` window (default 60) covers the one case liveness
cannot: a tree still being installed is named by no process yet, because the
process that will run out of it does not exist. Set it to `0` to disable.

**What it never prunes** — repo data. `data/datasets/`, `raw/`, the dbt logs and
the duckdb are measured and reported; their retention contract is
[#396](https://github.com/CannObserv/usa-wa/issues/396), not this script's call.
Worktrees are named, never destroyed — that is gated by the `using-git-worktrees`
Iron Law, a merge check a GC cannot make.

```bash
# Rebuild ollama/ollama:latest without its accelerator runtimes
scripts/slim-ollama-image.sh
scripts/slim-ollama-image.sh --force       # rebuild even if already slim
```

The stock image costs **8.06 GB on disk / 3.27 GB of content** to serve one
274 MB embedding model: `cuda_v12` 1.2 G, `cuda_v13` 831 M, `mlx_cuda_v13`
2.0 G, `vulkan` 47 M — 4.0 GB of accelerator runtime on a VM with no
accelerator. Stripping them took the volume from 98% to 57% with a
byte-identical embedding response.

It refuses on a host that has a GPU, refuses without headroom for the rebuild,
and keeps the fat image until the replacement is verified serving the model —
that image is the only rollback. The rebuilt image keeps the
`ollama/ollama:latest` tag (SocratiCode hardcodes it, and its
`isOllamaImagePresent()` gates the pull on it being present locally) and carries
a `dev.usa-wa.slim` label. A plain `docker pull` silently restores the fat
image; `scripts/disk-gc.sh` reads that label and reports it.
