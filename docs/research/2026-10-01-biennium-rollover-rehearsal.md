# Biennium-rollover rehearsal and source availability (#135)

The record behind [RUNBOOK-ROLLOVER.md](../RUNBOOK-ROLLOVER.md): what the nightly chain does
when `2027-28` becomes current (#135 item 2), and when each upstream source starts answering
for the new cycle (#135 item 4). Rehearsals are appended per run, and availability dates as
they are observed. The predictions came first, in the
[#135 review](https://github.com/CannObserv/usa-wa/issues/135#issuecomment-5921666182);
each result below says whether it held.

## Method

`scripts/rollover-rehearsal.sh <empty|partial>` copies production `raw/` and
`data/datasets/` into `~/rehearsal/<biennium>-<scenario>-<stamp>/` and runs the **real**
`scripts/pipeline-nightly.sh` with `USA_WA_BIENNIUM=2027-28` in its rehearsal mode:
- every root lives in the scratch dir, or the chain refuses to start;
- the registrar, the serving load and the coverage seed are skipped;
- `USA_WA_JOB_LEDGER=0`.

Production is only **read**: the registry crosswalk and operator events, which the build
joins. The two scenarios:
- **`empty`** harvests live, which shows how upstream answers for `2027-28` that day.
- **`partial`** skips the harvests and builds from a synthesized `sponsors:2027-28` wire:
  a deterministic 60% of the 2025-26 wire's members (`scripts/rehearsal_roster.py`).

## Rehearsal 1 — 2026-10-01 (before #412 PR F, before the election)

Caveats: the chain still had #412's pre-PR-F shape, and no 2026 ballot existed yet, so the
SOS lookahead and the House Position seat both behave as they would if early capture had
failed. Re-run in December (runbook).

### `empty` — exit 1

| Stage | Result | Prediction |
|---|---|---|
| WSL harvest | **degraded**, `errors=1`: `GetCommittees(2027-28)` raises `DataPortal.Fetch failed (Object reference not set to an instance of an object.)`, a server fault, not the empty list the past-floor case returns. `GetSponsors(2027-28)` answers an empty envelope (352 B). `GetCommitteeMeetings` for the 2027–28 window answers empty. | **missed**: not predicted |
| PDC harvest | ok, 5 empty cohorts | held |
| SOS harvest | degraded: `2026` and `2027` results both fail | an October artifact. By Jan 1 the certified 2026 export lands, and only `2027` fails, so the source is not degraded |
| dbt build | PASS=123, WARN=1 (`assignments_chamber_vacancy`, both chambers), ERROR=0 | held |
| `build_warnings` | exit 1 (the vacancy) | held |
| publish | ok: `assignments`, `organizations` and `stg_raw_fetches` minted, row counts flat, **no shrink refusal** | held |
| `registry_coverage` / `parity_citations` | ok, 0 unregistered, 0 uncited | held |

Data state: `assignments` keeps all 8,395 rows. **0 of 773** formerly active spans are
active, and every closed span has `valid_to = 2026-12-31`. `organizations`: 0 of 34
committees and 0 of 22 Joint/Other bodies active (#428). Side note: `committees:2027-28`
archives `GetActiveCommittees`, which takes no biennium, so it stores 2025-26's active set
under the 2027-28 label. No pipeline model reads that prefix (only the retiring Postgres
tier does).

### `partial` — exit 1

`sponsors:2027-28` = 101 of the 2025-26 wire's 158 members (61 House, 40 Senate).

| Stage | Result |
|---|---|
| harvests | skipped (`PIPELINE_NIGHTLY_SKIP`) |
| dbt build | PASS=123, WARN=1 (vacancy, both chambers, smaller deficits), ERROR=0 |
| publish | ok, 5 minted, no shrink refusal |
| probes | ok; the same 8,395 spans, so returning members' spans **reopen under their original keys** |

Data state:
- 35 Senate seats and 93 party spans active again. The 5 kept senators who stayed closed
  are departed members the synthesized wire carried over: 3 name-blanked stubs, Nguyen and
  Ramos. Hygiene and the operator overlay correctly hold them closed, and a real 2027-28
  wire won't list them.
- **0 House seats active**, though 61 House members are staged. A 2027-28 House seat's
  Position comes only from the 2026 ballot (`merge_positions`: the even November seats the
  next biennium), and the back-chain carries a Position backward, never forward. On
  Jan 1, then, the House can reopen only if `sos-legresults:20261103` is in `raw/`. **The
  early capture is load-bearing for the whole House chamber.**
- Every committee span is closed and every committee inactive (no 2027-28 committee rosters).

### Findings

1. **`GetCommittees(<next>)` faults until WSL publishes**, so the WSL raw harvest degrades
   and the nightly mails every night of the window, alongside the vacancy warning. The
   message is a generic server null-reference. The repo's rule (never key a parser on an
   exact upstream string) argues against treating it as benign the way `"valid biennium"`
   is, so the harvest is unchanged, and the runbook names the expected email exactly.
2. **The House chamber depends on the early-captured 2026 ballot.** Already handled by
   #449; the runbook's 2026-11-04 checklist confirms it landed.
3. Every other prediction held: spans close at the old biennium's end, committees go
   inactive, publish proceeds without a shrink refusal, and the probes stay clean.

## Source availability for 2027-28

| Source | Operation | First answered | How measured |
|---|---|---|---|
| WSL | `GetSponsors(2027-28)` has rows | — | `usa-wa-wsl-availability-probe` (daily; `data/research/wsl-availability.jsonl`) |
| WSL | `GetCommittees(2027-28)` stops faulting | — | same |
| WSL | `GetCommitteeMembers(2027-28, …)` has rows | — | same |
| PDC | `Won in general` marked for 2026 | — | the nightly's lookahead fetch: first non-empty `house-winners:2026` |
| SOS | 2026 results (unofficial) | — | first `ok` fetch of `sos-legresults:20261103` |
| SOS | 2026 results (certified) | — | that resource's sha256 change around certification |

Baseline, 2026-10-01: GetSponsors empty, GetCommittees faulting, no committee members.

For the PDC and SOS rows, the raw store's run manifests are the record. Each nightly run
adds one manifest, and a changed body shows up as a new sha256. **Date PDC by its first
non-empty cohort, not by hash changes:** its wire's hash churns with no change in what it
says (`house-winners:2024` moved between 196,103 and 196,088 bytes five times over
July–August 2026). The SOS export's hash has been stable, so its certification will show
as the first hash change after election night.

```bash
uv run python -c "
import json, pathlib
for rid, slug in (('sos-legresults:20261103', 'usa_wa_sos_results'), ('house-winners:2026', 'usa_wa_pdc')):
    last = None
    for m in sorted(pathlib.Path('raw', slug, 'runs').glob('*.json')):
        for e in json.loads(m.read_text())['entries']:
            if e['resource_id'] == rid and e['sha256'] != last:
                print(rid, e['fetched_at'], e['status'], e['bytes'], (e['sha256'] or '')[:12]); last = e['sha256']
"
```

(The SOS filings source is excluded from this table: dead upstream, #333.)
