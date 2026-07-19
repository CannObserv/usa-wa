# usa-wa — Agent Guidelines

Be terse. Prefer fragments over full sentences. Skip filler and preamble. Sacrifice grammar for density. Lead with the answer or action.

## Project Overview

Washington State law, regulation, and policy tracking service.

## Development Methodology

TDD required. Red → Green → Refactor. No production code without a failing test first.

## Environment & Tooling

Python ≥3.12, uv, pytest, ruff.

## Code Exploration Policy

SocratiCode is the preferred semantic-search tool for this repo (once indexed; the index lives in `.socraticodecontextartifacts.json` once `codebase_index` has run). Its MCP tools are **deferred** — schemas load only after a `ToolSearch` prefetch.

**Negative rule.** For broad semantic questions ("where is X", "how does Y work", "what depends on Z"), use SocratiCode MCP tools first. Reach for `grep`/`ripgrep` only on exact strings (error messages, log lines, known symbols). Reserve the Explore subagent for path-pattern walks (e.g. "all `*.py` under `packages/usa-wa-api/src/usa_wa_api/api/`"), not semantic search.

| Goal | Tool |
|------|------|
| Where is X defined / how does Y work / what files touch Z | `codebase_search` |
| Exact string/regex match (errors, log lines, known symbols) | `grep` / `rg` |
| Blast radius of changing/deleting a file or function | `codebase_impact` |
| What does an entry point actually do? | `codebase_flow` |
| Callers and callees of a function | `codebase_symbol` |
| Imports/dependents of a file | `codebase_graph_query` |
| DB schemas, deployment topology, runbook context | `codebase_context` / `codebase_context_search` |

Prefetch query — run via `ToolSearch` at session start:

`select:mcp__plugin_socraticode_socraticode__codebase_search,mcp__plugin_socraticode_socraticode__codebase_symbol,mcp__plugin_socraticode_socraticode__codebase_symbols,mcp__plugin_socraticode_socraticode__codebase_flow,mcp__plugin_socraticode_socraticode__codebase_impact,mcp__plugin_socraticode_socraticode__codebase_graph_query,mcp__plugin_socraticode_socraticode__codebase_status,mcp__plugin_socraticode_socraticode__codebase_context,mcp__plugin_socraticode_socraticode__codebase_context_search`

## Project Layout

`uv` workspace. Four-layer clearinghouse split — framework + domain shared across deployments; adapters + API per jurisdiction. See [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](docs/specs/2026-05-25-usa-wa-mvp-design.md).

**Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before adding an adapter, a data source, or a span/seat builder.** It is the reusable Layer-3 pattern: one adapter package per *jurisdiction+target* bundling every source that target publishes; each **source** a self-contained archive (own `Source`/`source_slug`/archive-key/transport/adapter/normalize/cohort/harvest); the **application** (spans/seats) source-agnostic, consuming a cohort interface — so a fact can draw on a new source without a rewrite (the `usa-wa-adapter-sos` filings + results sources are the worked example). Audit a source's coverage before building on it; never key a parser on an exact upstream string.

```
packages/
  clearinghouse-core/                 — Layer 1: framework primitives (jurisdiction-agnostic)
    src/clearinghouse_core/
      models.py       — Declarative Base, TimestampMixin (side-effect-imports jurisdictions + provenance for Base.metadata)
      jurisdictions.py — Jurisdiction cache mirror (4 tables: types/relationship_types lookups, jurisdictions, jurisdiction_relationships) — local copy of Power Map's Jurisdiction extension
      provenance.py   — Source, FetchEvent, RawPayload, Citation, Note, DocumentIdentifier (every canonical fact traces back to these)
      adapter.py      — BaseAdapter contract + FetchedPayload / NormalizedBatch / ResourceRef
      runner.py       — AdapterRunner: cache-or-fetch decision, idempotent upsert, provenance writing (derives FetchEvent.content_hash = sha256(RawPayload.body) — the #54 integrity baseline, single chokepoint). `archive_only(resource_id)` (#79) is the public promotion of the `_archive_payload` seam (#62): fetch + archive a wire **without** normalizing (honours the freshness cache), for a Phase-A harvest whose canonical derivation is archive-first + needs context the harvest doesn't hold (PDC's era roster)
      integrity.py    — provenance integrity sweep (#54): `python -m clearinghouse_core.integrity` re-hashes RawPayload bodies vs FetchEvent.content_hash; exit 1 on mismatch (corruption/tamper); NULL baselines = unbaselined, skipped. Weekly timer + OnFailure alert. Default run is a **rolling byte-slice** (#55): verifies `--byte-budget` (default 256 MiB) worth of payloads past a persisted ULID watermark (`sweep_state.py` / `clearinghouse_core.integrity_sweep_state`), wrapping at the archive tail — per-run cost flat as the #39 docket volume grows, whole corpus covered every ceil(bytes/budget) runs (so at-rest corruption is caught within one coverage cycle, not every run). `--full` = one whole-corpus pass ignoring the cursor; `--limit N` = row-capped partial (`limited`). The cursor upsert is the sweep's one write (app-role DML on a non-provenance table; #54 REVOKE forbids a `verified_at` on RawPayload itself). Re-alert cadence: the cursor advances past a mismatch too, so a given corruption emails once (#49 exit-1) then isn't re-reported until the next coverage cycle re-scans that slice — "no follow-up" = "not yet re-scanned," not "resolved"
      sweep_state.py  — IntegritySweepState: single-row-per-scope rolling cursor for the integrity sweep (#55); `cursor` = highest verified RawPayload.id (ULID str) or NULL to start a fresh coverage cycle
      seed_manifest.py — frozen-seed tamper-evidence convention (#54): writes/verifies `.sha256` (sha256sum format) + `.meta.json` sidecars for checked-in seed files; `verified_digest()` is the ingest seam — verifies a seed then returns the raw digest a loader writes into FetchEvent.content_hash (git is the in-repo evidence; sidecars are for ingest outside git)
      db/             — ULID SQLAlchemy column type (see db/ulid.md for rationale)
      database.py     — Async engine + session factory
      config.py       — Settings / env access (pydantic-settings)
      logging.py      — configure_logging() + get_logger()
  clearinghouse-domain-legislative/   — Layer 2: legislative-government model (state/federal)
    src/clearinghouse_domain_legislative/
                      — Bill, Legislator, BillAction, StatuteSection, etc. (skeletoned step 7)
      identity.py     — Person/Organization/Role/Assignment + LifecycleMixin (archived_at + deleted_at tombstones — PM archived/deleted axis split, #38/#42) + Organization.active (PM's third axis: operational live/dissolved domain flag — orgs-only, NOT a live-read gate, #43) + OrganizationName (dated name variants mirrored from PM `OrgName`/power-map#239; `Organization.name` stays the resolved current scalar, this child table is the history/association surface, #45) + OrganizationAcronym (acronym variants mirrored from PM `OrgAcronym` — list distinct from names, no type/dated window; `Organization.acronym` stays the resolved current scalar — read mirror adopts PM's `is_canonical` acronym into it symmetric with `name`, #47/#65) + `Role` **seat model** (power-map#261/#263, usa-wa#68): a legislative seat is a durable Role keyed on the structural tuple `(organization_id, role_type, jurisdiction_id, qualifier)` — House = 2 seats/LD (`qualifier` "Position 1"/"Position 2"), Senate = 1 seat/LD (`qualifier` NULL). `Role.jurisdiction_id` (the seat's district, distinct from the org binding-root dropped in the 2026-06-09 decoupling) + `Role.qualifier` are the seat columns; uniqueness splits into two partial indexes — `uq_roles_seat` `(org, role_type, jurisdiction, qualifier)` WHERE jurisdiction NOT NULL (NULLS NOT DISTINCT) for districted seats, `uq_roles_org_name` `(org, name)` WHERE jurisdiction NULL for title-keyed committee/leadership roles. **One-row-per-PM-anchor invariant (#86, from the #84 postmortem)**: each of the four PM anchor columns (`pm_person_id`/`pm_organization_id`/`pm_role_id`/`pm_assignment_id`) carries a **partial unique index** `WHERE <col> IS NOT NULL` (`uq_<table>_pm_<col>`, replacing the old plain lookup index — a unique index serves reads too). A second local row stamped with an existing anchor (PM dedups observations on `(person, role, start_date)`, so two local rows can resolve to one PM assignment — the #84 armed a crash loop with 98 such pairs) now fails loudly at write time instead of silently arming a reconcile crash days later. The one-shot span-collapse migrations (`migrate_sponsor_spans`/`migrate_pdc_spans`/`migrate_committee_spans`) retire the pre-#86 duplicates. They are now all **index-safe** — each retires a stranded row (deleting it + citations to free its anchor) **before** moving that anchor onto the covering span (`migrate_pdc_spans._retire_onto` #91, `migrate_committee_spans` #95, `migrate_sponsor_spans._retire_onto` #97) — so they run **under the live index**, not only before it lands. Their tests run under the live index too (the sponsor tests dropped the module-level `drop_anchor_unique_indexes` at #97); only the specific pre-index-modelling cases (two local rows sharing one anchor at rest, which the index forbids) still opt into the `drop_anchor_unique_indexes` fixture per-test
      role_types.py   — `RoleType` catalog mirror (power-map#268, usa-wa#68): local read-only cache of PM's role_types catalog (`GET /api/v1/role-types`) — `{slug, display_name, expects_jurisdiction, requires_qualifier}` keyed on slug (power-map#271/usa-wa#70 renamed the field from `is_seat`). The sync descriptor reads `expects_jurisdiction` to decide a Role observation's shape (seat-mode structural tuple vs title-mode) at runtime, retiring the hardcoded seat-slug map. `requires_qualifier` (power-map#273/usa-wa#71) is PM's **enforced** flag — a districted-seat observation of such a type (e.g. `state_representative`, per-position) without a `qualifier` is `REJECTED("qualifier_required")` rather than minting a positionless seat (#267); the descriptor mirrors it to defer such a seat pre-flight (`state_senator`=False → NULL qualifier valid). Refreshed by the sidecar's catalog sync ([`role_type_catalog.py`](packages/usa-wa-sync-powermap/src/usa_wa_sync_powermap/role_type_catalog.py) → `sync_role_type_catalog`, first cycle + hourly cadence)
      queries.py      — live_only(): read-side liveness guardrail (archived_at + deleted_at IS NULL) every live read routes through (#38/#42)
  clearinghouse-sync-powermap/        — Layer 1-adjacent: portable Power Map sync engine (sibling-reusable)
    src/clearinghouse_sync_powermap/
      descriptors.py  — EntityDescriptor contract (per-entity sync behaviour; zero usa-wa imports). `_anchor_match` is the shared tolerant anchor lookup (#86): every anchor-keyed concrete `local_match` (person/org/role/assignment) delegates to it, so a pre-index duplicate anchor logs `anchor_invariant_violation` + returns a deterministic winner (newest `updated_at`, id tiebreak) instead of raising `MultipleResultsFound` and poisoning the apply path (the read-side defense in depth behind the DB unique index)
      engine.py       — SyncEngine: changes-feed + reconcile reads, LWW, outbox worker, backoff, merge-orphan anchor self-heal (#36) + merged_into generic re-resolution (#37). Drain ordering is **topological** first, `next_attempt_at` second, `id` last (#96): `_due_entries` sorts by `_drain_priority` (each entity type's index in the dependency-first `build_descriptors` registry order — that order is load-bearing here, not informational), so a dependency **root** (org/role) always drains before its dependents (assignments) inside one `LIMIT batch_limit` cut. Without it a bulk produce starves frozen roots — the role roots failed once (`next_attempt_at` fixed at ~T0), and thousands of dependency-blocked assignments re-deferring to *just before* T0 filled every batch cut ahead of them, so the roots (which the whole assignment tier gates on) went un-attempted for ~42 min. Write side of the anchor invariant (#86): `_anchor_taken` pre-checks for a *different* local row already holding a PM id before stamping (autoflush makes a same-drain sibling visible), consulted at **both** anchor-stamp sites — the drain delivery (`_deliver`) and the sweep's PM-first adoption (`_sweep_row`, which declines the adopt and falls through to a CREATE so the drain owns the single park) — rather than stamping a duplicate whose flush would abort the whole tick and spin the cycle (the fast-loop counterpart of the #84 slow reconcile loop). The conflict dead-letters to **UNAVAILABLE** (via `_park_blocked`): a *blocking* status so the row isn't re-swept/re-POSTed and REJECTED entries don't pile up cycle-over-cycle (which would trip the #85 rejection-rise email every cycle) — an anchor conflict is a permanent "operator dedup then redrive" state, not a data edit the sweep auto-retries. An `anchor_invariant_violation` ERROR is logged once at the park (the sweep re-check is `log=False`). The partial unique index is the hard backstop the single-drainer check can't race
      client.py       — PowerMapClient Protocol + value types (ObservationResult, ChangePage…)
      models.py       — sync-schema OutboxEntry + SyncState + EnrichFingerprint (delivery ledger + feed cursor + enrich re-propagation stamp)
      testing.py      — shipped test doubles (FakeEntity/Descriptor/Client) for this + sibling tests
      pmclient.py     — GeneratedPowerMapClient: adapts the generated SDK to the PowerMapClient Protocol
  powermap-client/                    — GENERATED OpenAPI client for Power Map (do not hand-edit)
                      — openapi-python-client output; excluded from ruff/coverage/pre-commit.
                        Regenerate when PM's API changes (see "Regenerating the PM client" below).
  usa-wa-adapter-legislature/         — Layer 3: WA Legislature SOAP source mapping
    src/usa_wa_adapter_legislature/
      adapter.py      — WALegislatureAdapter(BaseAdapter): discover/fetch_one/normalize; dispatches the resources — committees:<biennium> (GetActiveCommittees) + committees-roster:<biennium> (GetCommittees, sub-project 3) + committee-meetings:<begin>:<end> (GetCommitteeMeetings, #39) + **sponsors:<biennium>** (SponsorService.GetSponsors, P1b) + **committee-members-hist:<biennium>:<id>:<agency>:<name>** (`GetCommitteeMembers`, #82 — the biennium+committee id ride the resource id + stamped url). normalize routes by the stamped URL; both member resources emit the **Person cluster only** (`normalize_member_persons`), since party/seat/committee tenure are archive-derived merged spans. The retired `committee-members:` (GetActiveCommitteeMembers) resource + its per-biennium membership normalizer were removed in #82 — `GetCommitteeMembers(current, …)` returns the identical set, so the daily fan-out keys the same op by the current biennium and one uniform archive covers current + history. The member normalizers need the runner's `session` (`_require_session`); a distinct `member_client` isolates the members fan-out from the committees pull for tests
      synthesis.py    — pure functions emitting canonical-row dicts for anchors WSL doesn't expose (legislature/chamber/biennium/regular)
      bootstrap.py    — bootstrap_synthetic_anchors: idempotent ON CONFLICT DO NOTHING upserts of the 6 anchor rows; returns BootstrapAnchors
      transport.py    — WSLClient: per-service zeep wrapper with lazy WSDL load; SOAP calls via asyncio.to_thread. fetch_active_committees + fetch_committee_meetings + fetch_committees + **fetch_sponsors** (SponsorService.GetSponsors) + **fetch_historical_committee_members** (`GetCommitteeMembers(biennium, agency, Name)`, #82 — the **only** archived roster op; the daily fan-out AND the harvest both use it, so the retired `GetActiveCommitteeMembers` archival pair `fetch_/parse_committee_members` was deleted; a committee absent that biennium, or a sub-1999-00 biennium, raises a benign Fault swallowed to an *empty* WireFetch, matched on the two specific messages so unrelated faults still propagate) return WireFetch (parsed records + pristine SOAP wire for archival, #54). Non-archival parsed-dict siblings get_committees / **get_sponsors** / **get_active_committee_members** (light reads; the last survives only for the identity probe, which needs a second *endpoint* to cross-check `Id` stability). parse_committee_meetings / parse_committees / **parse_sponsors** / **parse_historical_committee_members** re-deserialize an *archived* wire offline through the same operation binding (no data re-pull) — the #56 cache path; each guarded by a transport cassette round-trip test. **Central WSL rate limiter (#77)**: a global min-interval gate every SOAP operation POST passes through (`_CapturingTransport.post` → `_WSL_LIMITER`), so no caller (daily refresh, reconcilers, the historical harvests) can burst the single WSL host. Thread-safe slot-reservation (spaces concurrent `to_thread` callers without holding the lock while sleeping); env-tunable `USA_WA_WSL_MIN_REQUEST_INTERVAL` (default 0.5s, set 0 to disable); `configure_wsl_rate_limit()` maps a harvest's `--pause-seconds` onto it (the test suite zeroes it via an autouse fixture)
      harvest_sponsors.py — Phase A member backfill CLI (#77, epic #76): sweep `GetSponsors(biennium)` from the 1991-92 floor to current through `AdapterRunner(fill_only=True)`, archiving each `sponsors:<biennium>` wire (#54) and materializing **Persons + `wa_legislature_member_id` identifiers ONLY** (the sponsor normalizer is persons-only since #78-2c). Party/chamber-seat/committee tenure are **merged spans** built from the full archive in Phase B (#78), NOT per-biennium here. Persons dedup across biennia by the stable WSL `Id` (#81 confirmed stable 1991→2025, 0 re-keys), so a member seen in many biennia collapses to one Person. Same op/resource key as the daily path (older biennia = older resource ids). Pacing is **central** — `--pause-seconds` sets the WSL limiter, no per-window sleep. `--from-biennium`/`--to-biennium`/`--dry-run`/`--force`. Closed biennia cache-hit on re-run
      span_emit.py    — the **generic** span→Assignment emitter (#82, extracted from the sponsor one): resolve Person, inject `resolve_role(session, span)` + `citation_target(span, biennium)`, upsert one Assignment per tenure, append-only cite-every-biennium. The callable citation target is what lets sponsor spans cite one roster per biennium while committee spans cite one per (biennium, committee). Citations are **insert-only** (#54 REVOKEs DELETE from the app role) and keyed on the attesting FetchEvent's `resource_id` (a daily re-pull mints a fresh FetchEvent, so id-keying would append a citation every run). Also `close_stale_spans` (#83): the sweep every span builder (sponsor/committee/PDC) runs after emitting — closes any `is_active` Assignment of the builder's `(assignment_source, kinds)` whose 4-part span `source_id` the rebuilt span set no longer asserts (`valid_to` = end of the biennium before current, clamped ≥ `valid_from`). Without it the restricted daily re-drives strand departed members / committee-switchers / superseded-wire orphans as open rows forever; keyed on span identity (not member id) so a sitting member leaving one committee still closes. Mass-close guards: an empty asserted set is a no-op, and closing >`max_close_fraction` (default 0.5) of the open swept-kind rows past a `close_fraction_floor` (default 5) aborts with `stale_span_sweep_aborted_mass_close` + `sweep_aborted=true` in the builder's completion log — a truncated-but-valid roster wire must not read as mass departure (the #44/#56 floor pattern), while a *legitimate* mass close (a wholesale WSL committee-Id re-key stales every old-Id span at once) is run deliberately via the CLIs' `--max-close-fraction 1.0`; a boundary missed by the daily cadence closes late and self-corrects on the next unrestricted rebuild
      committee_membership_observations.py / committee_span_emit.py / committee_member_cohort.py — the #82 committee-membership trio: roster→Observation projection (kind=`committee`, discriminator = the committee's stable WSL `Id`), emission onto the committee Org's shared `member` Role (an un-ingested committee is skipped+logged), and the archive-first cohort provider (offline re-parse of each `committee-members-hist:` RawPayload + its per-roster citation targets). The provider's "latest roster per (biennium, committee)" query **joins `RawPayload`** — load-bearing, not an optimization: the runner re-records a FetchEvent on every forced re-pull but skips the payload when the wire is byte-identical (`skip_unchanged`), so from the daily fan-out's second run the newest event is payload-less; ordering on FetchEvent alone would read the current biennium as an empty roster and close every open membership span. `FetchEvent.id` breaks a `fetched_at` tie; the scan is memoized (both accessors run per build)
      harvest_committee_members.py — Phase A committee-roster harvest (#82): enumerate each biennium's House/Senate standing committees **from the local `committees-roster:` archive** (no extra GetCommittees call; a biennium `harvest_committees` never covered falls back to a live, **unarchived** `GetCommittees` pull — run that harvest first to provenance the enumeration), fan `GetCommitteeMembers(biennium, agency, Name)` over them, archive each wire (#54). Persons only, `fill_only`. Joint/`Other` skipped (no membership op, #39). Floor `1999-00`; central pacing via `--pause-seconds` (~40 committees x ~14 biennia)
      harvest_committee_member_spans.py — Phase B committee-membership span builder (#82): archive → observations → merged spans → emit. `restrict_to_biennium` scopes the **daily** re-drive to the current (member, committee) pairs (each with full history). The daily refresh re-drives this (`refresh._rebuild_committee_member_spans`)
      migrate_committee_spans.py — one-shot migration (#82): a committee span's `source_id` shares the legacy per-biennium key's 4-part shape, so a span starting at the legacy biennium **upserts it in place** (keeping id + `pm_assignment_id`) — on a shallow archive there is nothing to migrate. Once the harvest **deepens** a span past that biennium the shipped row is *stranded*; legacy = a committee Assignment the emitted span-key set doesn't claim (shape can't distinguish them, unlike #78-3). Each is mapped to the covering span by `(person_id, role_id)` + validity window; its stranded row + citations are hard-deleted to free the anchor, which is **then** moved onto the covering span (index-safe delete-before-transfer, #95 — the reverse order would collide with `uq_assignments_pm_assignment_id`). **Run it in the same window as the Phase A harvest, sidecar paused**: PM keys on `(person, role, start_date)`, so a deepened span the sidecar sees first gets its *own* PM assignment, after which the legacy row's anchor can only be dropped — orphaning that PM row (counted `anchors_dropped` + warned per row; correcting already-orphaned ones is the #80 start-date gap). **Owner role** (`DATABASE_URL_OWNER`); idempotent; `--dry-run`
      harvest_sponsor_spans.py — Phase B span builder (#78): archive-derived, no WSL pull. `build_sponsor_spans` reads every archived `sponsors:<biennium>` offline → `sponsor_observations` → `tenure_spans.build_tenure_spans` → `sponsor_span_emit.emit_sponsor_spans` (one merged Assignment per tenure, cite-every-biennium). The **daily refresh re-drives this** for the current biennium (#78-2c, `refresh._rebuild_member_spans`).
      migrate_sponsor_spans.py — one-shot migration (#78-3 + #97): collapse **stranded** party/`chamber-senate` Assignments (each carrying a `pm_assignment_id`) onto the span that shares their `(person_id, role_id)` — transfer the PM anchor to the span, hard-delete the stranded row + its citations, so the local cache holds ONE row per PM assignment (the descriptor's `local_match` invariant). Two stranded shapes: **(1)** pre-#78 per-biennium 3-part rows (`{member}:{dim}:{YYYY-YY}`, #78-3); **(2)** superseded 4-part shallow spans (#97) — the 2c daily path builds a span keyed on the *current* biennium start, and when the full-natural-depth backfill (`harvest_sponsor_spans`) later merges the same tenure into an **earlier-start** span, the current-start row is stranded (same `_superseded_pairs` case #91 fixed for PDC House, #95 for committees — the #78-3 migration only handled shape 1, so on the 2c deploy the 202 shipped 4-part rows were left uncollapsed as `orphans_no_span`). Anchor transfer is **index-safe** (`_retire_onto` #97: delete+flush the stranded row before assigning its anchor, so it runs under the live `uq_assignments_pm_assignment_id` #86 index — its tests dropped the `drop_anchor_unique_indexes` crutch). Matches PM's structural `(person, role)` key; a keeper already carrying a *different* anchor drops the stranded one (`anchors_dropped` + warned — the orphaned-upstream #80 case, avoided by running before the sidecar drains). Leaves `chamber-house` (PDC/#79) + `committee` (#82) rows untouched; a stranded row with no covering span is left + counted (`orphans_no_span`). **Owner role** (deletes citations, #54); idempotent; `--dry-run`. **Re-run at #97** (full-depth Senate/party backfill): `spans_built=920 superseded_retired=164 anchors_transferred=164 orphans=0` → Senate 241 spans (1991→2025) + party 679, all produced to PM.
      meeting_windows.py — biennium → (begin, end) window + committee-meetings:<begin>:<end> resource-id keying (#39); once-per-window cache key for docket frugality
      normalize/      — per-resource normalizers. **members.py**: shared member-cluster helpers (P1b) — `get_or_create_person`/`get_or_create_role` **SELECT-or-INSERT against the session** (flush for id) so Assignments carry real intra-batch FKs the runner can't resolve; `canonicalize_party` folds both endpoint encodings (R/D + Republican/Democrat); `is_person` (single source of truth, also imported by the probe) filters the name-blanked stubs; `district_number`/`ld_slug`/`resolve_ld_jurisdiction`; deterministic `source_id` builders; `EntityCollector` dedups by (type, source_id). **sponsors.py** (#78-2c): per named row → Person + `wa_legislature_member_id` PersonIdentifier **only** (persons-only). Party/Senate-seat tenure are no longer emitted per-biennium here — they are archive-derived merged spans built by the Phase B span engine (`harvest_sponsor_spans`), which the daily refresh re-drives for the current biennium; the retired inline `_emit_party`/`_emit_chamber` became `sponsor_observations` + `tenure_spans` + `sponsor_span_emit`. (`committee_members.py` was **retired in #82** — membership is now an archive-derived merged span, not a per-biennium row.) committees.py: WSL Committee → Organization (House/Senate → chamber, Joint → legislature; org_type='committee'). committee_meetings.py: meeting refs → Joint/`Other` Organizations (#39) — dedup by stable Id, name=LongName verbatim, short_name=Name, org_type='other', parent=legislature; House/Senate skipped (CommitteeService's domain). parent_for_agency shared (extended for 'Other'). Local `name` is the verbatim double-prefixed LongName *as produced* (the read mirror still adopts PM's curated canonical), while the PM-emitted name is the clean `short_name` for org_type='other' (`OrganizationDescriptor.observed_name`, #61). parent_for_agency + clean_field (normalize/fields.py) shared with committees.py
      committee_seed.py — frozen Joint/`Other` seed (de)serialization (deterministic bytes for stable hashing); DEFAULT_SEED_PATH = data/joint_other_committees_seed.json
      harvest_committee_meetings.py — backfill CLI (#39): sweep a biennium range through the runner (archive wire + upsert org_type='other'), then freeze the deduped cohort to the seed + seed_manifest sidecars. Closed windows = cache hits on re-run
      ingest_committee_seed.py — no-WSL seed loader (#39): verified_digest gates the bytes → synthetic FetchEvent.content_hash + archived RawPayload, fill-only upsert (seed is a floor, not an authority)
      refresh.py      — `python -m usa_wa_adapter_legislature.refresh` CLI entrypoint; biennium-from-date with USA_WA_BIENNIUM override. Daily run also pulls the current biennium's meeting window for additive Joint/`Other` discovery (best-effort; window-absence ≠ retirement, #39). The meeting pull is **forced** past the cache TTL (#63 — 24h TTL vs ~24h timer cadence was a fetch/skip jitter knife-edge): deterministic daily discovery, archival still dedup-bounded; committees stay TTL-governed. Force applies only to the date-current biennium — a `USA_WA_BIENNIUM` backfill of a closed window stays cache-governed (harvest owns closed-window re-pulls); non-current runs log `wsl_refresh_noncurrent_biennium` at warning (a stale env pin would otherwise silently redirect daily discovery). The refresh runs the `AdapterRunner` **`fill_only=True`** (#65): additive discovery *inserts* newly-appearing committees but **never overwrites an existing row** — `name`/`acronym` are PM-curated and the read-mirror resolves them, so re-writing them here would clobber the curation and bump `updated_at`, winning LWW against PM (the daily 4080-entry outbox ping-pong #65 diagnosed). Existing committees are PM's to maintain via the sidecar mirror; renames flow via the reconcilers. Daily run also drives the **member cluster** (P1b, `_discover_members`): the forced `GetSponsors` pull + a per-committee **`GetCommitteeMembers(current, agency, name)`** fan-out (#82 — the same op the historical harvest uses, keyed by the current biennium, so one uniform archive covers current + history; sequential; roster **enumerated from the DB** — the `org_type='committee'` rows scoped to `active` + live, so no extra GetActiveCommittees call and defunct backfilled committees are excluded). It then **re-drives the member span builder** (#78-2c, `_rebuild_member_spans` → `build_sponsor_spans`) for the current biennium: the sponsor normalize is now persons-only, and party/Senate-seat tenure is materialized as archive-derived merged Assignment **spans** (current biennium = the open end), replacing the retired per-biennium inline emission. Best-effort in its own SAVEPOINT, and gated to the date-current biennium (a backfill pin's spans are the `harvest_sponsor_spans` CLI's job). It also re-drives the **committee-membership span builder** (#82, `_rebuild_committee_member_spans`) scoped to the current cohort — the committee analog of `_rebuild_member_spans`, same best-effort SAVEPOINT + date-current gating; `RefreshOutcome.committee_spans` surfaces the count. Both the meeting + member forced pulls set `skip_unchanged=True` (a byte-identical re-pull re-records the FetchEvent for the TTL/ledger but skips normalize+persist — no duplicate Citation set daily; distinct from harvest `--force`, which re-normalizes to re-materialize rolled-back rows)
      probe_committee_extent.py — write-free discovery CLI (#64): walks bienniums backward from current calling `GetCommittees` + `GetCommitteeMeetings`, tallying committee/meeting counts + meeting wire bytes, stopping after N consecutive empty bienniums (`--max-empty`, default 2; bounded by `--max-bienniums`). Talks to `WSLClient` **directly, not the runner** — no `FetchEvent`/`RawPayload` written; answers "how much history exists" to scope the sub-project 3 backfill. Also `probe_committee_floor` — a **committee-only** backward walk (GetCommittees only, no slow meeting pulls) to the earliest biennium with data, used by the harvest to auto-scope its range
      probe_member_identity.py — write-free discovery CLI (P1b sub-project, #27 step 0): answers "is the WSL member `Id` a stable `Person.source_id`?" before any member ingest. Talks to `WSLClient` **directly, not the runner** (no `FetchEvent`/`RawPayload`); matches members **by name** (`LastName`,`FirstName` — deliberately not `Id`) across two axes and tallies `Id` agreement: cross-endpoint (`SponsorService.GetSponsors` vs `CommitteeService.GetActiveCommitteeMembers`) and cross-biennium (`GetSponsors(current)` vs `GetSponsors(prior)`). **Finding (2026-07-06): `Id` is stable across endpoint, biennium, AND chamber change (94/94 + 125/125, 0 divergences) → canonical `source_id` = `GetSponsors.Id`, no name-match fallback.** `GetSponsors` returns **one row per (member, chamber-tenure)**: a member appears once per tenure under a stable `Id`, so a mid-biennium House→Senate mover has two *named* rows (Alvarado `34024`, V. Hunt `35410`) and a boundary mover / departed member carries a **name-blanked stub** (`"Representative "`/`"Senator "`, null name/district/party — Orwall/Slatter House stubs, departed Hawkins/Hunt/Rivers). `is_person` filters the blanked stubs; the sponsor normalizer iterates rows and dedups Person by `Id`. The non-archival transport pulls it uses — `get_sponsors` / `get_active_committee_members` — are the parsed-dict siblings of `get_committees` (archival `fetch_*` forms land in step 1)
      harvest_committees.py — Phase A backfill CLI (sub-project 3): sweep `GetCommittees(biennium)` over a range through `AdapterRunner(fill_only=True)`, archiving the full-roster wire under **`committees-roster:<biennium>`** (a distinct provenance key from the daily `committees:<biennium>` GetActiveCommittees archive — a different SOAP op) and materializing standing committees keyed by WSL `Id` **without clobbering** PM-curated rows (#65). **Identity = the WSL `Id`** (redesign model A): WSL re-keys committees across eras (same name, new `Id` ~every decade), so each `Id` is its own committee org and same-name bodies coexist — a re-key is a *different* committee (the sidecar's `pm_match` cross-Id guard keeps the historical rows from over-matching onto each other's PM orgs). `--pause-seconds` drips against WSL; auto-probes the floor when `--from-biennium` omitted; closed rosters are cache hits on re-run. No seed frozen (deferred). `--dry-run` rolls back
      committee_roster_cohort.py — `CommitteeRosterCohortProvider` (Phase B): biennium → `{source_id: LongName}`, **archive-first** (re-parses the archived `committees-roster:<biennium>` wire offline via `parse_committees`; live GetCommittees fallback only for an un-archived biennium). `archived_bienniums()` enumerates the chain's domain. The roster analog of `meeting_cohort.py` (#56)
      baseline_unbaselined_committees.py — one-off **owner-role** provenance repair CLI (#64): the pre-#54 `committees:2025-26` fetch events carry NULL `content_hash` but DID archive their bodies, so this backfills `content_hash = sha256(RawPayload.body)` (the same digest the runner derives) — converting them from "unbaselined" to integrity-verified while keeping the fetch history + bytes (no deletion). A payload-less NULL-hash event is counted `skipped_no_payload` and left alone. Idempotent. Needs `DATABASE_URL_OWNER` — the app role is REVOKEd UPDATE on the ledger (#54); `--dry-run` previews
  usa-wa-adapter-pdc/                 — Layer 3: WA PDC (Public Disclosure Commission) SODA source
    src/usa_wa_adapter_pdc/
      transport.py    — PDCClient: async `httpx` reader for the PDC `Campaign Finance Summary` Socrata dataset (`3h9x-7bvm`) on data.wa.gov. `fetch_house_winners(election_year)` GETs the seated House winner cohort (`office=STATE REPRESENTATIVE` ∧ `general_election_status='Won in general'` — one row per `(LD, position)`); `fetch_senate_winners(election_year)` (#75) is the Senate sibling (`office=STATE SENATOR` — one row per LD, ~half the chamber each even year). Both return `WireFetch` (pristine JSON bytes archived + hashed #54, plus the decoded rows) via a shared `_fetch_winners`/`_winners_params(office, year)`. `parse_house_winners` / `parse_senate_winners` are the offline re-parsers (#56 cache path). Optional `USA_WA_PDC_APP_TOKEN` → `X-App-Token` (rate-limit only, not auth — sent only when set)
      adapter.py      — PDCAdapter(BaseAdapter): source_slug `usa_wa_pdc`, **archive-only (#79)**. `discover` yields `house-winners:<election_year>` (year = biennium start − 1) + both staggered `senate-winners:<year>` cohorts (`start-1` + `start-3` via `senate_election_years_for_biennium` — WA Senate is 4-yr staggered, so all sitting senators = the union of the two most-recent even years); `fetch_one` archives the SODA JSON, **stamping the resource id onto `FetchEvent.url` as a `#`-fragment** (the endpoint is chamber-agnostic — office is a query filter). `normalize` **raises** — PDC's seat/identity derivation is cross-year (a merged span) and era-matched, which a single-cohort normalize can't express, so it is done by the Phase B builder (`build_pdc_spans`) reading the archive. Both the daily refresh and the harvest drive this adapter via `AdapterRunner.archive_only`. `election_year_for_biennium` + its inverse `seating_biennium_for_election_year` (`2012`→`2013-14`) do the era mapping
      normalize/positions.py — pure helpers: `canonical_position` (`"1"`/`"2"` → PM `qualifier` `"Position 1"`/`"Position 2"`, power-map#263); `house_seat_role_source_id` (`seat:house:ld-{n}:position-{p}`); **`house_span_discriminator`/`parse_house_span_discriminator`** (#79 — colon-free `ld-{n}-position-{p}` so the House span `source_id` `{member}:chamber-house:{ld}-position-{p}:{start}` stays a clean 4-part key symmetric with the Senate seat span; a redistricting LD renumber splits the span, deliberate); `pdc_person_identifier_source_id`; `PDC_PERSON_ID_SCHEME='wa_pdc'`; `fold_token` + `surname_match_set` — **local** name matching (a Layer-3 adapter must not import the Layer-4 sidecar's `normalize_name`) robust to PDC's messy `filer_name` (`"JACOBSEN CYNTHIA P (Cyndy Jacobsen)"`): splits on whitespace/parens/commas only (so intra-surname hyphens/apostrophes stay in-token — `Ortiz-Self`) and adds consecutive-token joins (so a space-joined WSL surname — `Van De Wege` → `vandewege` — is testable by membership)
      normalize/pdc_matching.py — the **pure** roster/match primitives (#79, extracted from the retired house_positions normalizer so the projector reuses them without a cycle): `HouseRosterEntry`/`SenateEntry`, `build_house_roster`/`build_senate_roster` (WSL `GetSponsors` rows → `{LD:[entry]}`), `match_house_member` (within-LD folded-surname + party tiebreak; zero/ambiguous → None, no guess), `find_confirming_senator` (#74 mover signal)
      normalize/pdc_observations.py — **pure** Phase B projectors (#79): `build_house_position_observations` (winners → House Position `Observation`s + `person_wa_pdc` links, reusing the #69 match + #74 mid-biennium mover inference, era-matched by the caller; inferred seats carry no id + are tracked in `inferred_keys`; per-cohort coverage `summary`) and `build_senate_identity_links` (identifier-only Senate #75 links + robustness tally). Replaces the retired per-biennium `normalize_house_positions`/`normalize_senate_identities`/`persons.py` (a per-cohort normalize can't build a cross-year span, #79). Since #101 the House **seat** is no longer emitted here (PDC is identifier-only); this projector runs only for its `pdc_identifiers` links. **#100 `position_fallback` removed (#101 CR):** PDC's `Campaign Finance Summary` dataset omits the House `position` before the 2018 election (proven in #98), and the SOS→PDC fallback that once seated those pre-2018 winners — the `position_fallback` param + its `missing_position` tally + the `PositionFallback` type — was **dropped entirely** once its only driver (`build_sos_house_spans`) was retired; a position-less winner is now simply `incomplete`. A pre-2018 **identifier** backfill (whose link couples to a resolved position) would re-add the SOS→PDC injection
      normalize/pdc_span_emit.py — **identifier-only since #101**: `emit_pdc_identifiers`, the idempotent `person_wa_pdc` child-identifier upsert (per-Person, not per-tenure). The House Position **seat** emission moved to `usa_wa_adapter_sos.house.emit` when SOS became the seat authority (#101)
      pdc_cohort.py   — `PdcWinnerCohortProvider` (Phase B): archive-first `{year:[winner rows]}` + per-year citation targets, re-parsed offline from each `house-winners:`/`senate-winners:` RawPayload. Joins `RawPayload` so "latest" = latest payload-bearing event (the #82 CR lesson — a forced daily re-pull re-records a payload-less FetchEvent); memoized
      build_pdc_spans.py — **Phase B** (#79, **identifier-only since #101**, `python -m usa_wa_adapter_pdc.build_pdc_spans`): the #75 fix. Reads every archived winner cohort offline, pairs each with the roster of the biennium it **seated** (`[Y+1, Y+2]`, archive-first from the WSL sponsor archive — a 2012 cohort matches 2013-14, not current), matches each winner to a WSL Person, and emits the `person_wa_pdc` identifier links (House winners + #74 movers + #75 Senate). **PDC no longer emits or sweeps the House Position seat** — that is the WSL+SOS builder's (`build_house_spans`, `usa_wa_legislature`-sourced); retiring the House span emission is the #101 fix for the #100 CR finding-1 (the daily refresh no longer rebuilds a shallow `usa_wa_pdc` House span for a sweep to close). `restrict_to_biennium` scopes the daily link re-drive to current members. The House match runs PDC-only — the `house_position_fallback` wiring was **removed as dead code** (#101 CR round 2); a pre-2018 identifier backfill (whose link is coupled to a resolved position) would re-add the SOS→PDC injection. **Depends on #77** — a pre-#77 winner's Person is absent so its link is skipped (logged, correct)
      harvest_pdc.py  — **Phase A** (#79, `python -m usa_wa_adapter_pdc.harvest_pdc`): sweep even election years (floor 2008) → archive `house-winners:<Y>` + `senate-winners:<Y>` via `archive_only` (no normalize; era matching is Phase B's job). Cache-hit on re-run
      migrate_pdc_spans.py — one-shot **owner-role** migration (#79): retire the pre-#79 per-biennium `usa_wa_pdc` House rows (`{member}:chamber-house:{biennium}`, 3-part) stranded by the 4-part span key — map each to the covering span by `(person_id, role_id)` + window, transfer the PM anchor, delete the row + citations. `anchors_dropped` counter + deploy-sequencing note (sidecar paused). A row with no covering span is a left-alone `orphans_no_span` (run `build_pdc_spans` first). Idempotent; `--dry-run`
      refresh.py      — `python -m usa_wa_adapter_pdc.refresh`: daily cycle, **identifier-only since #101**. Resolves the biennium (USA_WA_BIENNIUM override, else current; non-current logs `pdc_refresh_noncurrent_biennium`), archives the current cohorts via `archive_only` (forced past TTL for daily determinism), then re-drives `build_pdc_spans` scoped to the current biennium — emitting the `person_wa_pdc` cross-links only (the House Position seat is the WSL+SOS builder's, driven by the SOS refresh). The era roster is read archive-first from the WSL sponsor archive (`sponsors:<biennium>`, written by the WSL refresh, which runs first). Runs **after** the WSL refresh (its Persons must exist)
  usa-wa-adapter-sos/                 — Layer 3: **everything WA Secretary of State** — House Position **authority** (#100/#101). Follows the multi-source target-package pattern ([`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)): one **source** subpackage per SOS feed (each a self-contained archive), the `house/` **application** source-agnostic. Since the #101 re-partition this package **owns** the WA House Position seat: WSL drives membership (who sits), SOS drives the ballot Position 1/2, the seat is emitted `usa_wa_legislature`-sourced (symmetric with the Senate seat, #75), and PDC is demoted to the `person_wa_pdc` identifier link. The `house/` composition root imports WSL + PDC; WSL/PDC never import SOS
    src/usa_wa_adapter_sos/               — `filings/` (source `usa_wa_sos`) + `house/` (application) + `provisioning.py`; the `results/` source (#101 follow-on) is added below
      transport.py    — `SOSFilingsClient`: async `httpx` reader for the votewa candidate-filing export (`eledataweb.votewa.gov/Candidates/ExportToExcel?electionDate=<YYYYMM>&countyCode=xx`) — despite the name serves **CSV** (`text/csv`), one row per candidate carrying `RaceName` (`State Representative Pos. 1/2`), `RaceJurisdictionName` (`Legislative District N`), `BallotName`, `PartyName` + contact/candidacy detail (`Email`/`MailingAddress`/`Phone`/`FilingDate`/`IsWithdrawn`, for #99). `fetch_whofiled(election_year)` returns `WireFetch` (pristine CSV bytes archived + hashed #54 + decoded rows); `parse_whofiled` is the offline re-parser (#56, stdlib `csv`, BOM-tolerant). Central courtesy min-interval gate `_SOS_LIMITER` (the #77 pattern) every GET passes through — env `USA_WA_SOS_MIN_REQUEST_INTERVAL` (default 1.0s, 0 disables), `configure_sos_rate_limit()` maps a harvest's `--pause-seconds`. `general_election_date(year)` → `<year>11` (WA general is November)
      adapter.py      — `SOSAdapter(BaseAdapter)`: source_slug `usa_wa_sos`, **archive-only (#100)**. `discover` yields a `sos-whofiled:<YYYYMM>` per configured election year; `fetch_one` archives the CSV, stamping `{endpoint}?{query}#{resource_id}` onto `FetchEvent.url` (provenance derived from module constants, independent of a faked client). `normalize` **raises** — the House position is a cross-year join to the PDC winner cohort, done by the Phase B provider. `whofiled_resource_id`/`election_year_from_resource_id` key on the YYYY prefix
      provisioning.py — get-or-create the `usa_wa_sos` REST `Source` (archival retention); sibling of the PDC one
      normalize/filings.py — **pure** votewa → House-position primitives (#100): `house_position_qualifier` (`State Representative Pos. 1` → `Position 1`, via PDC's `canonical_position`), `filing_ld`, `sos_party_slug` (canonicalize the token inside `(Prefers Republican Party)`), `build_house_filings` (rows → `{LD: [HouseFiling(qualifier, name_keys, party_slug)]}`), and `position_for(filings, ld, folded_last, party)` — the within-LD lookup that **reuses PDC's `surname_match_set`** to test a WSL member's clean folded surname against the messy SOS ballot name; zero/ambiguous → `None`, shared surname broken by party (never guessed, symmetric with `match_house_member`)
      sos_cohort.py   — `SosFilingCohortProvider` (Phase B): archive-first `{election_year: {LD: [HouseFiling]}}` re-parsed offline from each `sos-whofiled:` RawPayload (joins `RawPayload` so "latest" = latest payload-bearing event, the #82 lesson; memoized). `house_filings()` feeds the #101 builder's within-LD position lookup; `citation_events()` gives the per-year `sos-whofiled:` CitationTarget the seat cites (cite-every-biennium). Both scans are memoized. (The #100 `fallback_factory` was **removed** in the #101 CR round 2 — its only consumer, the retired `build_sos_house_spans`, was deleted; the House seat reads `house_filings()` directly.)
      normalize/house_seats.py — **pure** #101 projector: `build_house_seat_observations(house_roster, sos_filings, biennium)` joins the sitting WSL House roster (who sits — LD + party) to the SOS filings (`position_for` → the ballot Position) → `chamber-house` `Observation`s keyed on the **identical** span discriminator `ld-{n}-position-{p}` PDC used (so the migration collapses onto the covering span). A matched member with **no** resolvable position emits **nothing**, counted `missing_position` (OQ1: post-1965 "position unknown" is a data gap, not a position-less `state_representative` — PM rejects that via `requires_qualifier`; the genuine pre-1965 at-large seat is power-map#302)
      house_span_emit.py — the House Position seat emitter (#101, moved out of `usa_wa_adapter_pdc.normalize.pdc_span_emit` when SOS became the seat authority): `emit_house_position_spans` binds the House-position spans to the generic `span_emit` — WSL Person + get-or-created `state_representative` seat Role (per `(LD, Position)`, resolved from the span discriminator), Assignment `source` **defaults to `usa_wa_legislature`** (the seat authority; PDC was pre-#101), cite-every-biennium onto the driver-supplied cohort (`sos-whofiled:<YYYYMM>`). Reuses the PDC seat-Role source-id/discriminator helpers (one-directional Layer-3 sibling import)
      harvest_sos.py  — **Phase A** (`python -m usa_wa_adapter_sos.filings.harvest`): sweep even general-election years (floor **2008**, the PDC winner floor) → archive `sos-whofiled:<YYYYMM>` via `archive_only` (no normalize). Central pacing via `--pause-seconds`; closed years cache-hit on re-run; `--dry-run`/`--force`
      build_house_spans.py — **Phase B** (#101, `python -m usa_wa_adapter_sos.house.build`): the WSL+SOS House Position span builder — the re-partition core. Reads the WSL sponsor roster (archive-first, who sits) + the SOS filing archive (the Position) **offline**, projects `house_seats` observations per biennium, merges them into `TenureSpan`s, and emits one **`usa_wa_legislature`-sourced** `state_representative` Position seat Assignment per tenure (`emit_house_position_spans(assignment_source='usa_wa_legislature')`, cite-every-biennium onto `sos-whofiled:<Y>`), then `close_stale_spans(usa_wa_legislature, {chamber-house})`. **One builder for daily + historical** (`restrict_to_biennium` = current scopes the daily re-drive, `None` = full backfill) → a cross-2018 member builds the same deep open span either way, so the #100 CR finding-1 depth mismatch **cannot recur**. Coverage: Position **2008→present** (votewa floor); pre-2008 stays honestly position-less. Depends on #77 (Persons + sponsor archive) + the SOS harvest. **Historical backfill: run sidecar-paused in the same window as the migration** (`--biennium` scopes to a biennium's current members; `--dry-run`)
      refresh.py      — `python -m usa_wa_adapter_sos.house.refresh` (#101): the **daily** driver of the House Position seat. Archives the current election's `sos-whofiled:` cohort (forced past TTL for daily determinism) + re-drives `build_house_position_spans` scoped to the current biennium. Runs **after** the WSL refresh (reads its sponsor archive + binds its Persons); independent of the PDC refresh. Systemd timer 06:45 UTC
      migrate_house_source.py — one-shot **owner-role** #101 migration (`python -m usa_wa_adapter_sos.house.migrate`), run **after** `build_house_spans`: retire existing `usa_wa_pdc` 4-part chamber-house rows onto the covering `usa_wa_legislature` span, transferring the PM anchor. **Covering-window collapse, not an exact-`source_id` re-point** — PDC omits the pre-2018 position, so a cross-2018 incumbent's existing PDC span is *shallow* (`…:2019-20`, first PDC-positioned biennium) while the SOS builder emits a *deeper* `…:2017-18` (a different `source_id`); mapping by `(person, role)` + validity window (the `migrate_pdc_spans` `_covering_span`/`_retire_onto` pattern #91/#97) transfers the anchor to the deep keeper + deletes the shallow PDC row + its citations, so the sidecar can't mint a duplicate PM assignment (a naive in-place flip would strand the anchor on a superseded row — the #86 index can't catch it, the two rows carry different anchors). A PDC row with no covering keeper is left `orphans_no_keeper`; 3-part legacy rows are `migrate_pdc_spans`'s job (`skipped_legacy`). **Sidecar-paused, same window as the harvest + build, before anything drains to PM.** Idempotent; `--dry-run`
  usa-wa-api/                         — Layer 4: WA deployment (FastAPI + MCP + REST)
    src/usa_wa_api/api/
      main.py         — App factory, lifespan, router registration
      deps.py         — FastAPI dependencies (DB session, auth)
    tests/            — API tests; conftest defines savepointed db_session + AsyncClient
  usa-wa-sync-powermap/               — Layer 4: PM sync deployment binding + sidecar daemon
    src/usa_wa_sync_powermap/
      descriptors/    — concrete EntityDescriptors (jurisdiction, organization, role, person, assignment) — full identity cluster + PM-first match cascade + enrich-on-match; the org `pm_match` name stage carries a **cross-Id re-key guard** (committee-backfill redesign, model A — identity is the WSL `Id`, same-name committees coexist): WSL re-keys committees across eras, so a normalized-name match can land on a PM org already claimed by a *different* committee; each candidate is detail-fetched (PM search omits identifiers) and dropped if it carries an `org_wa_legislature_committee_id` identifier → create-new, only an *unclaimed* same-name org is adopted (the over-match that crash-looped the sidecar); `events.py` is the entity-event sub-resource read-mirror (person/org `fetch_record` pulls `/{id}/events`, `upsert_from_pm` mirrors via `sync_entity_events`); `org_names.py` is the dated-name read-mirror (org `upsert_from_pm` mirrors the embedded `OrgDetail.names[]` via `sync_org_names` → `OrganizationName`, #45; **skip-and-logs** a `pm_org_name_id` already claimed by a different org so the global `(source, source_id)` key can't crash the cycle — redesign defense-in-depth); `org_acronyms.py` is the sibling acronym read-mirror (org `upsert_from_pm` mirrors the embedded `OrgDetail.acronyms[]` via `sync_org_acronyms` → `OrganizationAcronym`, same skip-and-log guard, and adopts PM's `is_canonical` acronym into the `Organization.acronym` scalar symmetric with `name`, #47/#65). `person.py` `to_observation` emits the Person's child `person_identifiers` rows (mapped scheme → PM slug via `SCHEME_TO_IDENTIFIER_TYPE`, skipping the source-derived primary) as `additional_identifiers` (#69) — so a cross-source identifier like PDC's `wa_pdc` on a WSL-sourced Person attaches to the *same* PM person the primary resolves (deterministic, no name-match); it propagates to the anchored cohort via the enrich-payload fingerprint drift (the base `to_enrich_observation` merges child `additional_identifiers` with the demoted primary)
      registry.py     — build_descriptors() (the entity set the sidecar syncs) + build_reconciler() (#73 Axis 1: wires `include_local_cohort=True` so the subscription set is the **mirror set** — jurisdiction `lineage` via PM discovery ∪ OUR locally-anchored producer rows — not the whole PM WA subtree, which over-subscribed ~1,000 PM-only strangers we never mirror; the produced rows are enumerated from the local anchored cohort so the feed still delivers PM's edits to them) + **build_pm_client()** (#85: the single production construction path for `GeneratedPowerMapClient`, so the central PM min-interval governor — `POWERMAP_MIN_REQUEST_INTERVAL`, an httpx request hook every generated op passes through — is always wired; no CLI or daemon can burst PM's live 429 rate limit by forgetting the knob). Shared by bootstrap + `__main__` + every PM CLI
      sidecar.py      — Sidecar: per-cycle run (catalog sync → re-discovery backstop → per-descriptor reconciles → tick (feed → sweep → drain)) + isolated run loop. **Cycle-failure containment (#85, from the #84 postmortem)**: each descriptor's reconcile runs in its OWN session + error boundary (one poison entity can't roll back the other descriptors' reconcile stamps, the feed cursor, or the drain — the #84 amplification that generated ~1.7M reads over 4 days); every contained component failure still fails the cycle *verdict* (isolation must not defeat the retry signal), which drives exponential backoff in `run_forever` (`retry.backoff`: 60s base → 1h cap, reset on a clean cycle) and the **failure-streak alert** — the sidecar is a `Restart=` service the #49 `OnFailure=` handler can't see, so after `FAILURE_ALERT_THRESHOLD` (default 5 ≈ ~30 min) consecutive failed cycles it emails the operator once per streak via the injected `alert` callable. **Rejection visibility (#85)**: a `sidecar_cycle_summary` INFO line every cycle (outbox backlog + REJECTED reason breakdown via `engine.rejected_breakdown`) and one email when the REJECTED count *rises* (static pile never re-spams; the #84 case — 12 `identifier_conflict` rejections unnoticed for a week — now alerts on arrival)
      alerts.py       — operator email via the exe.dev gateway (#85): `build_alert(USA_WA_ALERT_EMAIL)` binds the Sidecar's alert callable (fail-closed: unset recipient → None + loud startup warning; send failures are swallowed — never crash the loop being watched). In-process reuse of notify-failure.sh's gateway POST only (the shell handler's systemd introspection is meaningless here)
      config.py       — SidecarSettings (POWERMAP_BASE_URL, POWERMAP_API_KEY)
      reconcile_committee_active.py — one-shot producer CLI (#44): diffs the produced committee cohort against `CommitteeService.GetCommittees(biennium)` and reconciles PM `active` both ways — `active=false` for committees the roster dropped, `active=true` for ones that reappear (reactivation self-heals a modest partial-pull false retirement on the next clean run). Guarded by an empty-pull check + cohort floor (denominator = active cohort); skips archived/deleted/unanchored; emit-only (PM stays authority for `active`, mirrors it back — no local write). **Live-era scoping (#90)**: the diff is restricted to committees whose WSL `Id` appears in the current **or** immediately-prior biennium roster (`present_ids ∪ prior_ids`, the prior roster's raw `Id`s read archive-first via `CommitteeRosterCohortProvider`); the historical committee backfill (`harvest_committees`, model A) floods the produced cohort with ~152 defunct-era Ids (all defaulting `active=true`) that would otherwise read as a mass retirement and trip the floor every run — they now fall out before the diff (counted `scoped_out`) while a genuine prior-biennium retirement still fires. Weekly timer (Sun 07:00 UTC, #48) + ad-hoc; out-of-band from routine sync (`to_observation` keeps `active` out, #43)
      committee_name_reconcile.py — shared rename-detection spine (#46 + #56): given a current/prior `{source_id: name}` cohort it diffs on the stable id, runs the guardrails (empty-pull / low-overlap / rename-storm, the storm fraction gated by `storm_floor_min_overlap` so a tiny overlap can't hair-trigger it), and emits the windowed dated-name evidence via `OrganizationDescriptor.to_names_observation` (prior name typed `former`, new name `legal`, #58 — name_type is observation, not curation). The cohort name value is both **diffed and emitted**, so each caller controls which name reaches PM; cohort/`produced` queries are parametrized by `org_type`; emit-to-PM-only, no local write
      reconcile_committee_names.py — one-shot producer CLI (#46): the write-side sibling of #45's read mirror. Detects a WSL committee **rename** (stable `Id`, changed `LongName`) by diffing `GetCommittees(current)` vs `GetCommittees(prior)` on `normalize_name(LongName)` — WSL's own raw name, **not** the PM-resolved `Organization.name` scalar (which would false-fire on PM canonicalisation and miss round-tripped renames). Builds `{Id: LongName}` maps and delegates to `committee_name_reconcile` (org_type='committee'). Guarded by empty-pull (either roster) + low-overlap (`--min-overlap-fraction`, default 0.5 — stable WSL Ids mean a healthy diff overlaps near-totally; a thin overlap = wrong-biennium pull, which would otherwise read as a hollow "renamed: 0") + rename-storm floor (`--max-rename-fraction`, default 0.34); skips unanchored + the live-cohort-absent (counted **hidden** = archived/deleted-but-produced vs **unproduced** = never-produced/other-source). Weekly timer (Sun 07:30 UTC, #53) + ad-hoc; `--dry-run` previews
      reconcile_committee_meeting_names.py — one-shot producer CLI (#56): the meeting-derived sibling of #46, for the Joint/`Other` (`org_type='other'`) class `CommitteeService` can't see (#39; e.g. ESEC `Id 13945`). Diffs two bienniums' `GetCommitteeMeetings`-derived cohorts (`MeetingCohortProvider` — archive-first: re-parses the closed window's archived SOAP wire offline via the same zeep binding, so an immutable docket isn't re-pulled weekly; live fallback only for an un-archived window) on the stable `Id`; the cohort name is the **clean `Name`** (#61 `observed_name`), not the agency-double-prefixed `LongName` stored as `Organization.name`, so the double-prefix never reaches PM and a PM canonicalisation can't false-fire. Same windowed emit + shared spine as #46, but **re-tuned guards** for a dormancy-prone cohort: low-overlap **off by default** (`--min-overlap-fraction` 0.0 — a body absent from one window is dormancy, not a wrong-biennium signal) and the storm fraction only weighed past `--storm-floor-min-overlap` (default 5). Window-absence ≠ rename (the diff intersects ids present in **both** windows). Weekly timer (Sun 07:45 UTC) + ad-hoc; `--dry-run` previews. Backfill caveat: the detector diffs current-vs-prior biennium, so an older rename (ESEC = 2023) needs a targeted `--biennium`
      validate_committees.py — read-only local↔PM validation CLI (#64): for each PM-linked produced org, diffs local canonical state ↔ live `OrgDetail` (`get_entity`) and buckets discrepancies (unlinked / missing / merged / name / acronym / names-window / acronyms / parent drift), splitting `reconciled` (PM curation roundtripped — e.g. a mirrored `former` window) from `divergent` (mirror lag/break). Emit-nothing; sequential reads + bounded `RetryableClientError` backoff; reports the unbaselined-fetch-event count. Exit 0 clean / 1 divergent / 2 auth / 3 empty-cohort abort. `merged` is modeled but not live-detectable (PM's `get_entity` collapses a 404 without `merged_into`)
      committee_name_chain.py — pure full-timeline rename-chain builder (sub-project 3, Phase B): given `{biennium: {source_id: LongName}}` across all archived bienniums, walks each stable id's **consecutive appearances** and emits every `normalize_name` transition as a windowed `former`→`legal` hop (effective bounds = boundary biennium start, #58). Deep-history guardrails: normalize-before-compare (formatting churn ≠ rename), dormancy-aware (absence gap spanned), per-boundary rename-storm floor (systematic reformat dropped, recorded in `storm_skipped`). No DB/PM
      reconcile_committee_name_chain.py — Phase B emit CLI (sub-project 3): the deep-history counterpart of #46 — reads every archived roster via `CommitteeRosterCohortProvider`, builds the full chain (`committee_name_chain`), and emits each `former`/`legal` transition through the #46/#56 spine's per-row `_emit_names`. Classifies an absent id (hidden vs unproduced), reports storm-skipped boundaries, empty-archive abort. Emit-only (PM curates `is_canonical`; the #45 mirror brings windows back, now sticking via #65 fill-only). `--dry-run`; exit 0/1/2/3. Backfill-once (not a timer) — the daily/weekly #46/#56 detectors carry renames forward
      heal_committee_curation.py — one-shot force-adopt heal CLI (#65 Part 2): for the whole anchored produced cohort, re-fetch each PM `OrgDetail` and force-apply it via `OrganizationDescriptor.upsert_from_pm` + a clock-parity stamp — the PM-wins branch of `apply_record` run **unconditionally**, bypassing LWW. Unsticks committees the pre-fill-only refresh left LWW-locked (local clock ahead of PM), so PM's curation (name/acronym/windows) is finally adopted; idempotent (no-op at parity). Local `canonical` write (app role); read-only PM; no operator token; `--dry-run` previews. Exit 0/2/3
      prune_subscriptions.py — one-shot reclaim CLI (#73 Axis 1 step 6): the counterpart to `build_reconciler`'s mirror-set scoping. `sync_subscriptions` is additive (never unsubscribes), so the ~1,000 PM-only strangers the old whole-subtree walk registered stay subscribed-but-inert (feed delivers, reconciler fetch-then-skips). This diffs PM's `list_subscriptions` against the freshly-discovered mirror set (`SubscriptionReconciler.prune_subscriptions`) and `remove_subscriptions` the difference. Guarded against a discovery collapse: empty desired-set aborts (`empty_desired`), stale fraction over `--max-prune-fraction` aborts (`prune_floor`, default 0.9 — permissive since the first run legitimately removes ~half). Strangers have no local row, so nothing is evicted locally; idempotent (second run finds nothing stale). **Run-once** after the mirror-set scoping lands, not a timer; no operator token; `--dry-run` previews. Exit 0 clean / 2 auth / 3 aborted
      __main__.py     — daemon entrypoint (python -m usa_wa_sync_powermap)
alembic/              — single alembic root; env.py imports clearinghouse_core.models.Base
docs/specs/           — Architecture specs (source of truth for design decisions)
docs/plans/           — Per-phase implementation plans
docs/research/        — Discovery outputs (Archiver/Watcher contracts, multi-state IA delta)
docs/                 — Reference docs (COMMANDS, SKILLS)
deploy/               — Systemd unit + deployment config
```

## Infrastructure

**Single-VM setup.** Code committed to main is the deployed code.

| Service | Framework | Port | Managed by |
|---|---|---|---|
| API (live) | FastAPI | 8000 | `systemctl` (`usa-wa.service`) |
| PM sync sidecar | asyncio daemon | — | `systemctl` (`usa-wa-sync-powermap.service`) |
| WSL refresh (daily) | oneshot + timer | — | `systemctl` (`usa-wa-wsl-refresh.timer` → `.service`; 06:00 UTC). Pulls committees **and** the current-biennium meeting window for additive Joint/`Other` discovery (#39) |
| PDC refresh (daily) | oneshot + timer | — | `systemctl` (`usa-wa-pdc-refresh.timer` → `.service`; 06:30 UTC, #69; **identifier-only since #101**). Archives the current winner cohorts + re-drives the builder → `person_wa_pdc` cross-links only (the House Position seat is the SOS refresh's since #101). Ordered after the WSL refresh (binds onto its House Persons + sponsor archive) |
| SOS refresh (daily) | oneshot + timer | — | `systemctl` (`usa-wa-sos-refresh.timer` → `.service`; 06:45 UTC, #101). Archives the current votewa filing cohort + re-drives the WSL+SOS House Position span builder → `usa_wa_legislature` `state_representative` Position seat **spans**. Ordered after the WSL refresh (reads its sponsor archive + binds its Persons); independent of the PDC refresh |
| Committee active reconcile (weekly) | oneshot + timer | — | `systemctl` (`usa-wa-reconcile-committee-active.timer` → `.service`; Sun 07:00 UTC) |
| Committee rename detection (weekly) | oneshot + timer | — | `systemctl` (`usa-wa-reconcile-committee-names.timer` → `.service`; Sun 07:30 UTC) |
| Joint/Other rename detection (weekly) | oneshot + timer | — | `systemctl` (`usa-wa-reconcile-committee-meeting-names.timer` → `.service`; Sun 07:45 UTC, #56) |
| Provenance integrity sweep (weekly) | oneshot + timer | — | `systemctl` (`usa-wa-integrity-sweep.timer` → `.service`; Sun 08:00 UTC) |
| Failure alerts | templated oneshot | — | `OnFailure=` → `usa-wa-notify-failure@.service` |
| API (dev) | FastAPI | 8001 | manual uvicorn |

`8001` = `8000 + 1`. The exe.dev proxy transparently forwards ports 3000–9999; the dev server is reachable at `https://usa-wa.exe.xyz:8001/`.

### Failure alerting (#49)

The unattended oneshots fail silently on a headless box — a `failed` state in the
journal nobody is watching. Each failable oneshot (`usa-wa-migrate`,
`usa-wa-wsl-refresh`, `usa-wa-pdc-refresh`, `usa-wa-sos-refresh`,
`usa-wa-reconcile-committee-active`, `usa-wa-reconcile-committee-names`,
`usa-wa-reconcile-committee-meeting-names`, `usa-wa-integrity-sweep`) carries
`OnFailure=usa-wa-notify-failure@%n.service`, so systemd starts the templated
handler on a non-zero exit **or** a `TimeoutStartSec=` hang. `%n` (the failing
unit's full name) becomes the handler's instance.

[`deploy/usa-wa-notify-failure@.service`](deploy/usa-wa-notify-failure@.service)
runs [`scripts/notify-failure.sh`](scripts/notify-failure.sh), which emails the
operator via the **exe.dev email gateway** (`POST
http://169.254.169.254/gateway/email/send`, a documented VM feature — no MTA/SMTP
creds needed). The reconcile exit-code contract (#44: 1 rejected / 2 auth / 3
guardrail abort) is surfaced **in the subject line** so a mass-retirement abort is
triageable without opening the journal. Recipient is `USA_WA_ALERT_EMAIL`
(`/etc/usa-wa/.env`); the script **fails closed** if it's unset — set it before
relying on alerts. The handler has no `OnFailure=` on itself (a failed send must
not recurse); a dropped alert still leaves the failure in the journal. The
serving units (`usa-wa`, `sync-powermap`) restart in place via `Restart=` and so
don't route through this one-shot alert — the sidecar closes that gap itself
(#85): after N consecutive failed cycles (and on a REJECTED-count rise) it emails
the same `USA_WA_ALERT_EMAIL` in-process via `usa_wa_sync_powermap.alerts`.

### DB role topology (defense-in-depth, issue #22)

DDL and DML rights are split across roles so a misconfigured DSN can't migrate/drop the live DB:

| Role | Rights | Used by |
|---|---|---|
| `usa_wa_owner` | owns all tables/sequences; CREATE/ALTER/DROP | `alembic upgrade head` only — the `usa-wa-migrate.service` oneshot |
| `usa_wa_app` | SELECT/INSERT/UPDATE/DELETE only (no DDL) | live API, sync sidecar, WSL refresh timer, on-box CLIs |
| `usa_wa_test_owner` | owns the **separate** `usa_wa_test` database; DDL | `TEST_DATABASE_URL` — the suite owns its own schema lifecycle (`create_all`/drop per session) |

- `DATABASE_URL` (app role) serves; `DATABASE_URL_OWNER` (owner role, migrate host only) migrates. `alembic/env.py` prefers `DATABASE_URL_OWNER` when set, else `DATABASE_URL`.
- [`scripts/grants.sql`](scripts/grants.sql) is the version-controlled source of truth for grants — idempotent, re-applied after every migration by [`scripts/migrate.sh`](scripts/migrate.sh). `ALTER DEFAULT PRIVILEGES` means new tables auto-grant DML to the app role. **Add new schemas to it** when a migration introduces one.
- Provision prod once as superuser: `psql -d usa_wa -v reassign_from=usa_wa -f scripts/grants.sql` (then per-role `ALTER ROLE … PASSWORD` out-of-band; passwords are never committed).
- The **test DB** needs only its role + ownership — do **not** run `grants.sql` against it (its schemas don't exist until the suite creates them, so the schema-grant steps would error). Provision with: `psql -c "CREATE ROLE usa_wa_test_owner LOGIN PASSWORD '…'"` then `ALTER DATABASE usa_wa_test OWNER TO usa_wa_test_owner`.
- Both the API lifespan and the sidecar log a startup fingerprint (`current_user` + `current_database`) — role/DB confusion shows up in the first `journalctl` line.

## Server Lifecycle

**Port 8000 belongs to systemd.** Never start uvicorn manually on port 8000.

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
activate their guarded `.service`). The two serving units
(`usa-wa`/`usa-wa-sync-powermap`) carry a widened `StartLimitIntervalSec=300`/
`StartLimitBurst=10` so an off-main checkout — which fails the guard on every
`Restart=` attempt — settles into `failed` instead of looping forever (a
transient dependency blip under ~50s still self-heals). **Recovery after an
off-main wedge:** returning to `main` doesn't auto-restart a `failed` unit — the
normal deploy (`systemctl restart …`) clears it; a bare `reset-failed` + `start`
also works. `test_unit_ordering.py` asserts the guard is present on every
code-running service, cross-checks the on-disk set (so a new service can't
silently omit it), and asserts every `Restart=` unit's start-limit window is
wide enough to bound the loop (`StartLimitIntervalSec >= RestartSec * StartLimitBurst`).

**Deploy convention: units never sync the venv (issue #30).** Every systemd
entrypoint runs `uv run --frozen --no-sync` (`usa-wa.service`,
`usa-wa-sync-powermap.service`, `usa-wa-wsl-refresh.service`,
`usa-wa-pdc-refresh.service`, `usa-wa-sos-refresh.service`,
`usa-wa-reconcile-committee-active.service`,
`usa-wa-reconcile-committee-names.service`,
`usa-wa-reconcile-committee-meeting-names.service`,
`usa-wa-integrity-sweep.service`, `scripts/migrate.sh`).
`--no-sync` runs against the installed venv as-is; `--frozen` skips re-locking.
So unit start never mutates the environment — the daily WSL refresh timer can't
silently apply a dependency change a `git pull` landed in `uv.lock`. (Note:
`--frozen` *alone* would not prevent this — it still syncs the venv to the lock;
`--no-sync` is the flag that stops it.) **Dependency changes land only via a
deliberate `uv sync --locked` after a pull that touches `uv.lock`:**

```bash
git pull
uv sync --locked                       # reconcile venv ⇄ uv.lock deliberately
sudo systemctl restart usa-wa-migrate  # if DB models changed (restart, not start — see note)
sudo systemctl restart usa-wa usa-wa-sync-powermap
```

`uv sync` here uses `--locked` (not `--frozen`): it additionally asserts
`uv.lock` is consistent with `pyproject.toml`, catching a committed lock that
went stale — a deploy-time integrity check worth failing on. Units stay on
`--frozen` so a lock/pyproject drift can't wedge the daily timer.

If the venv is missing a locked dependency, units fail loudly at import — the
intended signal to run `uv sync`. **First provision (or after a venv wipe)
requires a plain `uv sync`** — `--no-sync` units can't start against an absent
`.venv`.

**Units are installed as copies, not symlinks.** Every `/etc/systemd/system/usa-wa*`
unit is a root-owned copy of its `deploy/` counterpart, so after editing a unit file
run `sudo cp deploy/<unit> /etc/systemd/system/` **before** the `daemon-reload` the
rows below prescribe — `daemon-reload` alone re-reads the stale installed copy and
silently deploys nothing.

| Situation | Action |
|---|---|
| Code committed to main | `sudo systemctl restart usa-wa` (run `uv sync --locked` first if `uv.lock` changed — units are `--no-sync`; see convention above) |
| Testing a worktree/branch | `uv run uvicorn ... --port 8001 --reload` |
| Debugging the live service | `sudo journalctl -u usa-wa -f` |
| After editing `deploy/usa-wa.service` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa` |
| After editing `deploy/usa-wa-wsl-refresh.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-wsl-refresh.timer` |
| After editing `deploy/usa-wa-pdc-refresh.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-pdc-refresh.timer` |
| After editing `deploy/usa-wa-sos-refresh.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-sos-refresh.timer` |
| After editing `deploy/usa-wa-reconcile-committee-active.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-reconcile-committee-active.timer` |
| After editing `deploy/usa-wa-reconcile-committee-names.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-reconcile-committee-names.timer` |
| After editing `deploy/usa-wa-reconcile-committee-meeting-names.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-reconcile-committee-meeting-names.timer` |
| After editing `deploy/usa-wa-integrity-sweep.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-integrity-sweep.timer` |
| After editing `deploy/usa-wa-notify-failure@.service` | `sudo systemctl daemon-reload` (templated `OnFailure=` handler — nothing to restart; next failure picks it up) |
| After DB model changes | `sudo systemctl restart usa-wa-migrate` (runs alembic + grants under the owner role), then restart usa-wa — run `uv sync --locked` first if `uv.lock` changed (`migrate.sh` is `--no-sync`). **`restart`, not `start`** — the unit is a `RemainAfterExit` oneshot, so once it's `active (exited)` from an earlier migrate this boot, `start` is a silent no-op (exits 0, applies nothing). |
| Run the WSL refresh now (ad-hoc) | `sudo systemctl start usa-wa-wsl-refresh.service` |
| Run the PDC refresh now (ad-hoc) | `sudo systemctl start usa-wa-pdc-refresh.service` |
| Run the SOS refresh now (ad-hoc) | `sudo systemctl start usa-wa-sos-refresh.service` |
| Run the committee active reconcile now (ad-hoc) | `sudo systemctl start usa-wa-reconcile-committee-active.service` |
| Run the committee rename detection now (ad-hoc) | `sudo systemctl start usa-wa-reconcile-committee-names.service` |
| Run the Joint/Other rename detection now (ad-hoc) | `sudo systemctl start usa-wa-reconcile-committee-meeting-names.service` |
| Run the provenance integrity sweep now (ad-hoc) | `sudo systemctl start usa-wa-integrity-sweep.service` |

**Validating unit edits (#51).** A path-filtered pre-commit hook
(`systemd-verify-units` → [`scripts/verify-units.sh`](scripts/verify-units.sh))
runs `systemd-analyze verify` on any changed `deploy/*.{service,timer}`. It
fails on a non-zero exit **and** on stderr warning markers (`Unknown key name`,
`Unknown section`, `ignoring`, …), because `systemd-analyze` exits 0 on
unknown/misspelled directives — a plain `$?` gate would pass them. Catches:
directive/section typos, malformed syntax, nonexistent `ExecStart=` binaries.
Does **not** catch misspelled `After=`/`Before=` ordering deps (systemd treats
ordering against absent units as legitimate) — that gap is closed instead by
[`scripts/tests/test_unit_ordering.py`](scripts/tests/test_unit_ordering.py)
(#52), which asserts the intended `After=`/`Before=` graph as data and
cross-checks the on-disk unit set so a new unit forces an explicit ordering
decision. No-ops where `systemd-analyze` is
absent. Because `verify` resolves absolute `ExecStart=` paths
(`/usr/local/bin/uv`) and `User=exedev` against the *local* box, off-VM it can
false-**fail** even with `systemd-analyze` present — a failure off-VM means "run
it on the VM," not "your unit is broken." Run ad-hoc:
`./scripts/verify-units.sh deploy/*.service deploy/*.timer`.

**Dev server workflow.** Run on port `8001` so the live service stays up. Load env first:

```bash
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload
```

**After finishing work.** Always restart the systemd service to pick up changes merged to main:

```bash
sudo systemctl restart usa-wa
```

## Environment Variables

Two env files, loaded in order (later values override):

1. **`/etc/usa-wa/.env`** — production secrets (`DATABASE_URL`, etc.). Survives repo resets and worktree switches. Managed manually on the VM.
2. **`.env`** (repo root, git-ignored) — dev/agent secrets (`GH_TOKEN`, `TEST_DATABASE_URL`). Never commit.

The systemd service loads both automatically. For shell commands:

```bash
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)
```

Currently defined:
- `GH_TOKEN` — GitHub personal access token (used by `gh` CLI)
- `DATABASE_URL` — PostgreSQL connection string (app role `usa_wa_app` — DML only)
- `DATABASE_URL_OWNER` — owner-role DSN for migrations (migrate host only; `usa-wa-migrate.service` + `scripts/migrate.sh`). `alembic/env.py` prefers it over `DATABASE_URL`. Absent from the live API/sidecar units.
- `TEST_DATABASE_URL` — PostgreSQL connection string for the test database (test role; database name must end in `_test`)
- `BUILD_ID` — git SHA stamped by the systemd unit's `ExecStartPre`; defaults to `"dev"` outside systemd
- `USA_WA_OPERATOR_TOKEN` — shared secret gating the mutating operator endpoint `POST /sync/redrive` (re-drives dead-lettered `UNAVAILABLE` outbox entries). **Fail-closed:** if unset, the endpoint is locked for everyone, so it must be set in `/etc/usa-wa/.env` before the re-drive route can be used. The on-box CLI (`python -m usa_wa_api.cli.redrive`) needs no token — shell access is the trust boundary.
- `USA_WA_BIENNIUM` — optional override for the auto-computed WA biennium label (e.g. `2025-26`) used by the WSL **and** PDC refreshes. Without it, `refresh.py` derives the biennium from the current UTC date (WA bienniums start on odd years). Useful for backfills and early-year edge cases.
- `USA_WA_PDC_APP_TOKEN` — **optional** Socrata application token for the PDC refresh (#69), sent as the `X-App-Token` header only when set. Rate-limiting only (moves throttling from per-IP to per-app), **not** authentication — the dataset is public and readable without it, so it's not required at the once-daily single-GET volume. Register one free in a data.wa.gov profile to raise limits.
- `USA_WA_WSL_MIN_REQUEST_INTERVAL` — **optional** central courtesy floor (seconds) between any two WSL SOAP calls, across all `WSLClient` instances/services (#77). Default `0.5` (≤2 req/s); `0` disables. A harvest's `--pause-seconds` overrides it for that run via `configure_wsl_rate_limit()`. Protects the single WSL upstream from bursts regardless of which caller is running.
- `USA_WA_SOS_MIN_REQUEST_INTERVAL` — **optional** central courtesy floor (seconds) between any two WA SOS votewa calls (#100), the #77 pattern applied to `eledataweb.votewa.gov`. Default `1.0` (gentle — votewa is a low-QPS government site and the harvest is a handful of GETs); `0` disables. A harvest's `--pause-seconds` overrides it via `configure_sos_rate_limit()`.
- `USA_WA_ALERT_EMAIL` — recipient for oneshot failure alerts (#49). Consumed by `scripts/notify-failure.sh` (the `usa-wa-notify-failure@.service` `OnFailure=` handler). Must be **you / an exe.dev team member** (gateway recipient allow-list). The script **fails closed** if unset, so set it in `/etc/usa-wa/.env` to arm alerting. See § Failure alerting.

PM sidecar tunables (`SidecarSettings`, env-overridable): `OUTBOX_COMMIT_CHUNK_SIZE` (delivered entries per DB commit during a drain; default 1 = per-entry), `POWERMAP_SEARCH_MATCH_CAP` (max candidate window the org/person name-match cascade pages; default unset = per-entity default), `SUBSCRIPTION_BACKSTOP_CADENCE` (how often the full-subtree re-discovery walk re-runs; default 6h — #73 Axis 2, graph drift is slow), `RECONCILE_CADENCE` (anchored-cohort backstop re-fetch of OUR whole cohort by id, each person also pulling `/events`; default 12h — #73 Axis 2, a dropped-feed-event safety net for a low-churn dataset, applied to the producer descriptors in `build_descriptors`; the feed is the real-time path), `POWERMAP_MIN_REQUEST_INTERVAL` (#85: central courtesy floor in seconds between any two PM HTTP calls — the #77 pattern, applied inside `GeneratedPowerMapClient` via `build_pm_client`; default 0.2 ≈ ≤5 req/s, 0 disables; PM's 429 limit is live and the anchored-cohort crawl tripped it at ~8 req/s) and `FAILURE_ALERT_THRESHOLD` (#85: consecutive failed cycles before the streak email; default 5 ≈ ~30 min under the backoff schedule). The sidecar's streak/rejection alerts reuse `USA_WA_ALERT_EMAIL` (see § Failure alerting) — unset = alerting disabled with a loud startup warning.

## Common Commands

```bash
# Install dependencies
uv sync

# Load environment (required before running server, migrations, or gh)
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)

# Run tests
uv run pytest

# Run a subset of tests (skip the coverage gate, which measures all of packages/)
uv run pytest --no-cov packages/usa-wa-api/tests/test_health.py

# Run integration tests (requires PostgreSQL)
uv run pytest -m integration

# Run linter
uv run ruff check .

# Database migrations (need the owner role — see § DB role topology)
# prod: sudo systemctl restart usa-wa-migrate (restart, not start — RemainAfterExit
#       oneshot no-ops on start once already active); ad-hoc alembic needs DATABASE_URL_OWNER
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"

# FastAPI dev server
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload
```

Everyday commands only. **Operational & backfill CLIs — command + one-line purpose
below; full options, exit codes, and design rationale in
[`docs/COMMANDS.md`](docs/COMMANDS.md).** Prod runs the daily/weekly ones on systemd
timers (see § Server Lifecycle); the rest are run-once / ad-hoc. Pair backfills with
`USA_WA_BIENNIUM` to target a non-current biennium.

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.refresh` | Daily WSL pull — committees + meeting window + member cluster |
| `python -m usa_wa_adapter_pdc.refresh` | Daily PDC pull — House Position seats (#69) + Senate cross-links (#75) |
| `python -m usa_wa_sync_powermap.backfill_contact_labels` | Re-observe orgs w/ phone so PM adopts contact label (#31) |
| `python -m usa_wa_sync_powermap.reconcile_committee_active` | Reconcile PM `active` vs current roster (#44; weekly) |
| `python -m usa_wa_sync_powermap.reconcile_committee_names` | Committee rename → dated-name evidence (#46; weekly) |
| `python -m usa_wa_sync_powermap.reconcile_committee_meeting_names` | Joint/Other rename detection (#56; weekly) |
| `python -m usa_wa_sync_powermap.validate_committees` | Read-only local↔PM drift report (#64) |
| `python -m usa_wa_sync_powermap.heal_committee_curation` | Force-adopt PM curation for LWW-locked committees (#65) |
| `python -m usa_wa_sync_powermap.prune_subscriptions` | Unsubscribe PM-only strangers; re-run to stale=0 (#73) |
| `python -m clearinghouse_core.integrity` | Provenance integrity sweep — rolling byte-slice (#54/#55; weekly) |
| `python -m usa_wa_adapter_legislature.baseline_unbaselined_committees` | OWNER-role provenance repair (#64) |
| `python -m usa_wa_adapter_legislature.probe_committee_extent` | Write-free: how much committee history exists (#64) |
| `python -m usa_wa_adapter_legislature.probe_member_identity [--history]` | Write-free: is the WSL member Id stable (#27/#81) |
| `python -m usa_wa_adapter_legislature.harvest_committee_meetings` | Joint/Other backfill + seed freeze (#39) |
| `python -m usa_wa_adapter_legislature.ingest_committee_seed` | No-WSL Joint/Other seed loader (#39) |
| `python -m usa_wa_adapter_legislature.harvest_sponsors` | Historical member backfill — Persons only, Phase A (#77) |
| `python -m usa_wa_adapter_legislature.harvest_sponsor_spans` | Merged-span member Assignments, Phase B (#78) |
| `python -m usa_wa_adapter_legislature.migrate_sponsor_spans` | Collapse stranded party/Senate rows (3-part legacy #78-3 + superseded 4-part #97) onto merged spans (owner role) |
| `python -m usa_wa_adapter_legislature.harvest_committee_members` | Historical committee rosters — Persons only, Phase A (#82) |
| `python -m usa_wa_adapter_legislature.harvest_committee_member_spans` | Merged committee-membership spans, Phase B (#82) |
| `python -m usa_wa_adapter_legislature.migrate_committee_spans` | Retire per-biennium committee rows stranded by deeper spans (#82) |
| `python -m usa_wa_adapter_legislature.harvest_committees` | Committee historical backfill, Phase A (sub-project 3) |
| `python -m usa_wa_sync_powermap.reconcile_committee_name_chain` | Full committee rename-chain emit, Phase B (sub-project 3) |
| `python -m usa_wa_adapter_pdc.harvest_pdc` | Historical PDC winner cohorts — archive-only, Phase A (#79) |
| `python -m usa_wa_adapter_pdc.build_pdc_spans` | Era-matched `person_wa_pdc` identifier links, Phase B (#79; identifier-only since #101) |
| `python -m usa_wa_adapter_pdc.migrate_pdc_spans` | Retire pre-#79 per-biennium PDC House rows onto spans (#79) |
| `python -m usa_wa_adapter_sos.filings.harvest` | Archive WA SOS votewa filing cohorts — Phase A (#100) |
| `python -m usa_wa_adapter_sos.house.build` | WSL+SOS House Position seat spans (2008→present), Phase B (#101) |
| `python -m usa_wa_adapter_sos.house.migrate` | Re-source usa_wa_pdc House rows → usa_wa_legislature (owner role, #101) |

### Regenerating the PM client

`packages/powermap-client/` is generated from Power Map's live OpenAPI; never hand-edit it. To refresh after PM ships API changes:

```bash
cd /tmp && rm -rf pmgen && mkdir pmgen && cd pmgen
curl -fsS https://power-map.exe.xyz/openapi.json -o pm-openapi.json
printf 'package_name_override: powermap_client\nproject_name_override: powermap-client\n' > cfg.yml
uvx openapi-python-client generate --path pm-openapi.json --config cfg.yml --meta uv
# review the diff, then replace the vendored copy:
rm -rf /home/exedev/usa-wa/packages/powermap-client
cp -r powermap-client /home/exedev/usa-wa/packages/powermap-client
```

Then `uv sync` and re-run the `GeneratedPowerMapClient` wrapper tests — the wrapper's path/model dispatch (`pmclient.py`) is what breaks if PM renames an operation or model.

## Agent Skills

Skills in `skills/` (agentskills.io) and `.claude/skills/` (Claude Code). Reference: `docs/SKILLS.md`

## Conventions

**Commit Messages:**
```
#<number> [type]: <description>      # with issue
[type]: <description>                # without issue
```
Types: feat, fix, refactor, docs, test, chore

**Logging:**
```python
from clearinghouse_core.logging import get_logger
logger = get_logger(__name__)
```
Entry points only: `configure_logging()` is called once inside the FastAPI `lifespan`. Never in library modules.

**Date & Time:**
- All UTC
- ISO 8601: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (timestamps), `YYYY-MM-DD` (dates)

**General:**
- No inline module imports; all at file top
- Docstrings for public modules, classes, functions
- Test structure mirrors source within each package (`packages/<name>/src/<pkg>/foo.py` → `packages/<name>/tests/test_foo.py`)
- Explicit imports only
- Small, focused functions
