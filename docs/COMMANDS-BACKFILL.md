# Commands — historical backfill and probes

Split out of [COMMANDS.md](COMMANDS.md), which is where the index lives.

## Discovery probes (write-free)

Talk to WSL directly (NOT the runner) — no FetchEvent/RawPayload written. Answer
scoping questions ("how much history exists", "is the Id stable") before ingest.

```bash
# Committee historical extent probe (#64) — walks bienniums backward from current, tallying
# committee/meeting counts + meeting wire bytes, stopping after N consecutive empty bienniums.
python -m usa_wa_adapter_legislature.probe_committee_extent
python -m usa_wa_adapter_legislature.probe_committee_extent --start-biennium 2025-26 --max-empty 2

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

## Historical backfill (epic #76 / sub-project 3 / #100)

Sweep a source to its floor. Data-source-respecting: each closed window — a
biennium for the WSL sweeps, an election year for the SOS one — is archived once
(#54) and cache-hits on re-run; `--pause-seconds` drips against that source's own
upstream via its central rate limiter. `--dry-run` rolls back. Run-once (not
timers).

Ordered by source, then by phase within it — three sources, not two:

1. the WSL/legislature sweeps (members, committees, their span builders and
   migrations), which are most of the section;
2. `usa_wa_sync_powermap.reconcile_committee_name_chain` — a PM-sync emitter,
   documented here beside the `harvest_committees` Phase A whose archived
   rosters it reads, not in [COMMANDS-SYNC.md](COMMANDS-SYNC.md) with the other
   reconcilers;
3. the SOS filings archive last — a different upstream, epic, and archive key,
   so it deliberately does not interleave with either.

```bash
# Joint/Other committee backfill (#39) — sweep CommitteeMeetingService.GetCommitteeMeetings
# over a biennium range (the only source of Joint/Other committees), archiving the pristine
# SOAP wire and upserting org_type='other' rows, then FREEZE the deduped durable cohort to
# data/joint_other_committees_seed.json (+ .sha256/.meta.json sidecars). Hits live WSL (one
# POST per window) AND mutates the DB — not read-only; --dry-run still upserts but skips the
# seed write. Closed windows are cache hits on re-run. Commit the produced seed.
python -m usa_wa_adapter_legislature.meetings.harvest --from-biennium 2023-24 --to-biennium 2025-26

# Joint/Other seed ingest (#39) — the no-WSL counterpart: materialize the frozen cohort on a
# fresh deploy. verified_digest gates the seed bytes (fails closed on a sidecar mismatch),
# writes a synthetic hashed FetchEvent + archived RawPayload, and fill-only upserts (existing
# rows untouched — the seed is a floor, not an authority). Needs the committed seed file.
python -m usa_wa_adapter_legislature.ingest_committee_seed

# Historical member (sponsor) harvest — Phase A of the #76 backfill epic (#77). Sweep
# GetSponsors(biennium) from the 1991-92 floor to current through AdapterRunner(fill_only=True),
# archiving each sponsors:<biennium> wire (#54) and materializing PERSONS + wa_legislature_member_id
# identifiers ONLY (the sponsor normalize is persons-only, #78-2c — party/seat/committee tenure
# are merged spans built in Phase B #78, not per-biennium here). Persons dedup by stable Id (#81). Same
# op/resource key as the daily path. Pacing is central: --pause-seconds sets the WSL limiter — it
# defaults to None (#169), so an unflagged run leaves USA_WA_WSL_MIN_REQUEST_INTERVAL in force.
# Closed biennia cache-hit on re-run; --dry-run rolls back; --force re-materializes.
python -m usa_wa_adapter_legislature.sponsors.harvest --dry-run   # 1991-92→current, roll back
python -m usa_wa_adapter_legislature.sponsors.harvest --from-biennium 1991-92 --pause-seconds 1

# Historical member SPANS — Phase B of the #76 backfill (#78). Archive-derived, no WSL pull:
# reads every archived sponsors:<biennium> offline (SponsorRosterCohortProvider re-parses via
# parse_sponsors), projects rows to party + Senate-seat tenure Observations
# (sponsor_observations), collapses contiguous biennia into merged valid_from..valid_to spans
# (tenure_spans.build_tenure_spans — a dormancy gap splits; the run reaching the current
# biennium stays open/is_active), and emits ONE Assignment per tenure keyed on the tenure
# start (sponsor_span_emit) with a Citation per biennium in range (cite-every-biennium, #78).
# Idempotent re-assert. Depends on the #77 harvest archiving the rosters first. --dry-run rolls
# back. The daily refresh also re-drives this builder for the current biennium (#78-2c).
# Ends with the #83 stale-span sweep (party + chamber-senate): open spans the rebuild no longer
# asserts are closed (departed members) — closed_stale in the completion log; closed_stale > 0 on
# an unrestricted run = previously-stranded rows repaired. Guarded against empty/mass closes
# (sweep_aborted=true in the completion log); --max-close-fraction 1.0 (validated to (0, 1])
# permits a deliberate one. STALE-ROW EXCLUSION (#105 (b)): each biennium's named rows are
# screened against that biennium's committee-roster archive (roster_hygiene) — a departed-but-
# still-named ghost (Kilduff/Senn/Nguyen) emits no party/Senate observations for its stale
# bienniums, so the merged span ends at the real departure boundary (sponsor_stale_row_excluded
# per exclusion; --stale-min-coverage floor, default 0.9, skips a thin committee cohort —
# stale_exclusion_skipped_low_coverage; >1 disables).
python -m usa_wa_adapter_legislature.sponsors.build --dry-run
python -m usa_wa_adapter_legislature.sponsors.build

# Span MIGRATION — #78-3 + #97, OWNER ROLE (deletes citations, #54). Collapse STRANDED
# party/chamber-senate Assignments (each carrying a pm_assignment_id) onto the merged span that
# shares their (person_id, role_id) — PM's own structural assignment key. Transfers the PM anchor
# to the span + hard-deletes the stranded row + its citations, so the local cache holds ONE row per
# PM assignment (else the assignment descriptor's local_match scalar_one_or_none / the #86 unique
# index breaks). Builds the spans first (idempotent), then collapses. Two stranded shapes:
#   (1) pre-#78 per-biennium 3-part rows ({member}:{dim}:{YYYY-YY}), #78-3; and
#   (2) superseded 4-part shallow spans (#97) — the 2c daily path keys a span on the CURRENT
#       biennium start; when the full-natural-depth backfill (harvest_sponsor_spans, no restrict)
#       merges the same tenure into an EARLIER-start span, the current-start row is stranded (the
#       same _superseded_pairs case #91 fixed for PDC House / #95 for committees). The #78-3 pass
#       only handled shape 1, so on the 2c deploy the 202 4-part current rows were left uncollapsed
#       as orphans_no_span — #97 closes that. Anchor transfer is index-safe (delete+flush before
#       assign → runs under the live uq_assignments_pm_assignment_id #86 index).
# Leaves chamber-house (PDC/#79) + committee (#82) rows untouched; a stranded row with no covering
# span is left + counted (orphans_no_span); a keeper already carrying a different anchor drops the
# stranded one (anchors_dropped + warned, the #80 orphaned-upstream case). Idempotent; --dry-run
# rolls back. #97 run (full-depth Senate/party backfill): spans_built=920 superseded_retired=164
# anchors_transferred=164 orphans=0 → Senate 241 spans (1991->2025) + party 679, all produced.
# DEPLOY SEQUENCING: run in the SAME window as the backfill, sidecar PAUSED
# (sudo systemctl stop usa-wa-sync-powermap). PM keys assignments on (person, role, start_date), so
# a deepened span the sidecar anchors BEFORE this runs gets its own PM assignment, after which the
# stranded anchor can only be dropped (anchors_dropped). Restart after:
# sudo systemctl start usa-wa-sync-powermap.
python -m usa_wa_adapter_legislature.sponsors.migrate_spans --dry-run
python -m usa_wa_adapter_legislature.sponsors.migrate_spans

# Committee MEMBERSHIP harvest — Phase A (#82). Enumerate each biennium's House/Senate standing
# committees from the local committees-roster archive (no extra GetCommittees call; an un-archived
# biennium falls back to a live, UNARCHIVED GetCommittees pull — run harvest_committees first if
# you want the enumeration itself provenanced) and fan
# GetCommitteeMembers(biennium, agency, Name) over them, archiving each wire (#54). Persons only
# (fill_only) — membership is a Phase B span. Joint/Other skipped (no membership op, #39). Floor
# 1999-00 (below it WSL's truncated old names fault → swallowed to an empty roster). ~40
# committees x ~14 biennia; --pause-seconds sets the central WSL limiter. Closed rosters cache-hit.
python -m usa_wa_adapter_legislature.harvest_committee_members --dry-run
python -m usa_wa_adapter_legislature.harvest_committee_members --from-biennium 1999-00 --pause-seconds 1

# Committee membership SPANS — Phase B (#82). Archive-derived, no WSL pull: re-parses each
# archived committee-members-hist roster offline, projects (member, committee, biennium)
# observations, merges contiguous biennia into one membership span bound to the committee's
# shared `member` Role, citing each (biennium, committee) roster. A dormancy gap opens a second
# span. Idempotent. The daily refresh re-drives this for the current cohort.
# Ends with the #83 stale-span sweep (committee): open memberships the rebuild no longer asserts
# are closed — a member who left the committee OR the legislature, and superseded-wire orphans.
# closed_stale in the completion log; guarded against empty/mass closes (sweep_aborted=true when
# tripped). A wholesale WSL committee-Id re-key makes EVERY old-Id span stale at once — that
# legitimate mass close is the --max-close-fraction 1.0 case (flag validated to (0, 1]).
python -m usa_wa_adapter_legislature.harvest_committee_member_spans --dry-run
python -m usa_wa_adapter_legislature.harvest_committee_member_spans

# Committee span MIGRATION — #82, OWNER ROLE, run AFTER the Phase A harvest deepens spans.
# A span starting at a legacy row's biennium upserts it in place (same 4-part key), so a shallow
# archive needs no migration. Once the harvest pushes a span's start earlier, the shipped
# per-biennium row is stranded: legacy = a committee Assignment the emitted span-key set doesn't
# claim. Each is mapped to the covering span by (person_id, role_id) + validity window, its
# pm_assignment_id transferred, then hard-deleted with its citations (owner-only under #54).
#
# SEQUENCING: run this in the SAME maintenance window as the Phase A harvest, with the sidecar
# paused (sudo systemctl stop usa-wa-sync-powermap). PM keys assignments on
# (person, role, start_date), so a deepened span the sidecar drains first is minted as its OWN PM
# assignment — after which the legacy row's anchor can only be dropped, orphaning that PM row
# (a live PM assignment with the wrong start_date and no local mirror). Those are counted
# `anchors_dropped` and warned per row; expect 0. Restart the sidecar after.
# Idempotent; --dry-run rolls back.
python -m usa_wa_adapter_legislature.migrate_committee_spans --dry-run
python -m usa_wa_adapter_legislature.migrate_committee_spans

# Reclassify generic `member` Roles → PM catalog slugs (#110). Two emitters historically
# stamped every membership Role with role_type='member', but PM's role_types catalog refines
# that into `committee_member` / `party_member` (power-map#268). The classifier sat permanently
# diverged from PM's role_type_slug, so the #109 no-op gate read a genuine diff and re-enqueued
# ~305 roles every reconcile forever. The emitters now stamp the catalog slug on NEW rows; this
# reclassifies EXISTING ones by source_id prefix (committee-member-role: → committee_member,
# party-role: → party_member) — get_or_create_role never rewrites an existing classifier and
# the daily refresh only re-drives the current cohort, so historical rows need this one-shot.
# Reclassifying makes to_observation send the matching slug → the next reconcile's gate reads a
# true no-op and adopts PM's clock, stopping the churn. APP role (role_type is a plain canonical
# column). Idempotent; --dry-run rolls back. No sidecar pause needed (local reclassify only).
python -m usa_wa_adapter_legislature.migrate_member_role_types --dry-run
python -m usa_wa_adapter_legislature.migrate_member_role_types

# Committee historical backfill (sub-project 3, Phase A) — sweep GetCommittees(biennium)
# over a range through AdapterRunner(fill_only=True): archive the full-roster wire under
# committees-roster:<biennium> + materialize standing committees by stable Id WITHOUT
# clobbering PM-curated rows (#65). Hits live WSL (one POST/biennium, --pause-seconds
# between); auto-probes the floor if --from-biennium omitted; closed rosters cache-hit on
# re-run. --dry-run rolls back. Distinct from the daily GetActiveCommittees archive.
# --force re-fetches + re-normalizes past the freshness cache (a plain re-run inside the
# 1-day TTL is a cache hit that upserts NOTHING) — the post-incident re-materialization of
# rolled-back rows, and the retrospective-change revalidation of closed rosters; byte-identical
# wire dedups to the existing RawPayload, fill-only leaves unaffected committees untouched.
# FOLLOW-UP after a --force run that CREATES committees: the freshly-created rows are
# LWW-locked (local updated_at ≥ PM's org clock), so the sidecar mirror won't adopt their
# PM name/acronym windows until PM's clock advances — run `heal_committee_curation` to
# force-adopt them (else validate_committees shows them divergent with empty child tables).
python -m usa_wa_adapter_legislature.harvest_committees --from-biennium 2011-12 --pause-seconds 2
python -m usa_wa_adapter_legislature.harvest_committees --dry-run   # auto-probe floor, roll back
python -m usa_wa_adapter_legislature.harvest_committees --from-biennium 1991-92 --force  # re-materialize
# then: python -m usa_wa_sync_powermap.heal_committee_curation   # mirror the created cohort's windows

# Full committee rename-chain emission (sub-project 3, Phase B) — the deep-history sibling
# of #46. Reads every archived committees-roster:<biennium> offline (archive-first, no WSL
# re-pull), builds each stable Id's full normalize_name(LongName) timeline, and emits every
# former->legal transition to PM (windowed dated-name evidence). Dormancy-aware + per-boundary
# storm floor. Emit-only; PM curates is_canonical, the #45 mirror brings windows back (now
# sticking via #65). Backfill-once (not a timer). --dry-run previews; exit 0/1/2/3.
python -m usa_wa_sync_powermap.reconcile_committee_name_chain --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_name_chain

# SOS FILINGS harvest — Phase A of #100, archive-only (#166). Sweep the votewa
# /Candidates/ExportToExcel CSV export (statewide, countyCode=xx) for each EVEN general-election
# year and archive the pristine wire under sos-whofiled:<YYYYMM> (YYYY11 — the WA general is
# November) via AdapterRunner.archive_only: FetchEvent + deduped RawPayload with the #54 content
# hash, NO normalize (SOSAdapter.normalize raises NotImplementedError by design). Even years only —
# an odd --from-year bumps up to the next even year, unlike the results harvest which sweeps odd
# years too (#106). Floor 2008 (the PDC winner floor this was built to join).
# DISTINCT FROM usa_wa_adapter_sos.results.harvest (COMMANDS.md § WSL+SOS House Position backfill):
# different source (usa_wa_sos vs usa_wa_sos_results), different archive key, its OWN CLI — do not
# infer flags or resilience from the sibling.
#
# 2020+ COVERAGE CLIFF. SOS retired this export to Power BI after the 2018 general; a 2020+
# election returns HTTP 500 (live audit 2026-07-18 — the finding that moved the House Position seat
# onto the results source at #101 — re-verified 2026-08-06: 201811 → 200, 202011 → 500). Since #169
# the CLI knows this: DEFAULT_ELECTION_CEILING = 2018 caps the wall-clock default, so a bare
# invocation sweeps 2008–2018 and stops. Passing --to-year 2018 is now belt-and-braces rather than
# mandatory. An EXPLICIT --to-year past the ceiling is still honoured as given (an operator probe of
# whether votewa ever restores the export) — and is survivable, because the sweep is per-year
# resilient: each year runs in its own SAVEPOINT, an httpx failure (status OR transport) is
# skipped-and-logged as sos_cohort_year_skipped, and the years the sweep reached still commit. A
# DB/SQLAlchemy error is NOT an httpx error and still aborts the whole run. If every year fails, one
# distinct sos_harvest_total_outage warning fires so cohorts_archived=0 does not read as "nothing to
# do". USA_WA_BIENNIUM is NOT read here (the bound comes from the wall clock, then the ceiling).
# The source is kept for its candidacy metadata (Email / MailingAddress / Phone / FilingDate /
# IsWithdrawn, #99), not the seat — see ARCHITECTURE.md for the two sources' coverage table, and
# clearinghouse_core.source_coverage (#180) for the machine-readable form: this feed carries a
# verified 2008-2018 claim AND an absent 2020- claim, which is what the ceiling is derived from.
#
# PACING: --pause-seconds sets the CENTRAL votewa min-interval — one shared limiter every votewa GET
# passes through (the #77 central-governor pattern), the same gate USA_WA_SOS_MIN_REQUEST_INTERVAL
# seeds at import (default 1.0, 0 disables). Since #169 the flag defaults to None and the CLI only
# calls configure_sos_rate_limit() when you pass it, so the env var now genuinely governs the
# unflagged run — it previously did not, this harvest being the ONLY production caller of
# SOSFilingsClient (Phase B reads the archive offline). Deliberately gentle: votewa is a low-QPS
# government ASP.NET site with no published API contract, and 2008–2018 is 6 calls.
# CACHE: the freshness TTL is provisioned at 1 day (the usa_wa_sos Source's cache_ttl_days, set on
# row creation only — an existing row's value is never reconciled). Inside it a re-run is a pure
# cache hit — no HTTP at all, cohorts_archived=0 with years unchanged. Past it every year
# re-fetches (cohorts_archived counts FETCHES, not new bytes) but a byte-identical CSV dedups to
# the existing RawPayload, so only a new FetchEvent is written. --force skips the TTL check.
# --dry-run harvests for real (it hits votewa) and rolls back — no provenance retained.
# EXIT CODES: 0 success, printing e.g. "SOS harvest: years=6 cohorts_archived=6 cohorts_skipped=0
# (committed)" — the trailing token is "(dry-run, rolled back)" under --dry-run. SOME years
# skipped is still exit 0 (the reached years committed), so read cohorts_skipped for a partial
# outage; 1 a NON-httpx exception mid-sweep (a DB error), logged as sos_harvest_failed, nothing
# committed; 2 DATABASE_URL unset, or the year range selects nothing (e.g. --from-year above the
# 2018 ceiling); 4 EVERY year skipped — a whole-source outage, also logged as
# sos_harvest_total_outage. 4 is non-zero deliberately: per-year resilience means no year crashes
# the sweep, so without it the outage would be a warning nobody consumes (#178 / CR #196 f22).
# APP role (archive tables only, no owner DML). No sidecar pause needed — archive-only, so nothing
# reaches PM.
python -m usa_wa_adapter_sos.filings.harvest --dry-run                # stops at the 2018 ceiling
python -m usa_wa_adapter_sos.filings.harvest --from-year 2008 --to-year 2018 --pause-seconds 1.0
python -m usa_wa_adapter_sos.filings.harvest --force   # re-pull past the 1-day TTL
```
