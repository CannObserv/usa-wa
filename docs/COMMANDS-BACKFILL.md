# Commands — discovery probes

Split out of [COMMANDS.md](COMMANDS.md), which is where the index lives.

The historical backfills this file documented — the WSL member/committee/membership sweeps,
their span builders and migrations, the Joint/Other meeting harvest and seed ingest, the role
reclassify and the SOS filings sweep — wrote the Postgres canonical tier and were deleted with it
in #412 PR F. History now lands through the daily raw harvests ([COMMANDS.md](COMMANDS.md)) and
is rebuilt nightly by the #302 pipeline ([PIPELINE.md](PIPELINE.md)). The roster PDF has its own
reference: [COMMANDS-ROSTER.md](COMMANDS-ROSTER.md).

## Discovery probes (write-free)

Talk to WSL directly — nothing is archived, to the raw store or anywhere else. Answer
scoping questions ("how much history exists", "is the Id stable") before ingest.

Both run on the shared job harness with `needs_db=False` (#179b): no DSN is resolved and
**no `job_runs` row is written** — there is no database to write one to. `--json` is the
harness's, so it emits the whole run envelope (`job`/`outcome`/`counters`/`duration_ms`)
with the probe's summary under `counters`, rather than the bare summary dict these two
printed before #179b. Exit code is unchanged: always `0`; a divergence is a finding to
read, not a job failure.

```bash
# Committee historical extent probe (#64) — walks bienniums backward from current, tallying
# committee/meeting counts + meeting wire bytes, stopping after N consecutive empty bienniums.
python -m usa_wa_adapter_legislature.committees.probe_extent
python -m usa_wa_adapter_legislature.committees.probe_extent --start-biennium 2025-26 --max-empty 2

# Member Id-stability probe (P1b #27 step 0) — answers "is the WSL member Id a stable
# Person.source_id?" before member ingest: matches members BY NAME (not Id) across GetSponsors
# vs GetActiveCommitteeMembers (cross-endpoint) and GetSponsors(current) vs GetSponsors(prior)
# (cross-biennium), tallying Id agreement. Finding 2026-07-06: Id stable both axes → canonical
# source_id = GetSponsors.Id. --json for compact output.
python -m usa_wa_adapter_legislature.sponsors.probe_identity
python -m usa_wa_adapter_legislature.sponsors.probe_identity --biennium 2025-26 --json
# Deep-history sweep (#81): every consecutive biennium pair 1991-92→current, classifying
# same-name/different-Id divergences into re-keys (same District — forks one person) vs name
# collisions (different District — two people the Id separates). Finding 2026-07-08: Id STABLE
# across all 17 boundaries, 0 re-keys (one benign collision: two "Brian Sullivan"s, LD29/LD21).
python -m usa_wa_adapter_legislature.sponsors.probe_identity --history
```

The third write-free probe, `probe_availability` (#135, WSL's rollover-day roster availability),
is indexed in [COMMANDS.md](COMMANDS.md) and run by
[RUNBOOK-ROLLOVER.md](RUNBOOK-ROLLOVER.md).
