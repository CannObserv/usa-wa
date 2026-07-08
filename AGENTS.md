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

```
packages/
  clearinghouse-core/                 — Layer 1: framework primitives (jurisdiction-agnostic)
    src/clearinghouse_core/
      models.py       — Declarative Base, TimestampMixin (side-effect-imports jurisdictions + provenance for Base.metadata)
      jurisdictions.py — Jurisdiction cache mirror (4 tables: types/relationship_types lookups, jurisdictions, jurisdiction_relationships) — local copy of Power Map's Jurisdiction extension
      provenance.py   — Source, FetchEvent, RawPayload, Citation, Note, DocumentIdentifier (every canonical fact traces back to these)
      adapter.py      — BaseAdapter contract + FetchedPayload / NormalizedBatch / ResourceRef
      runner.py       — AdapterRunner: cache-or-fetch decision, idempotent upsert, provenance writing (derives FetchEvent.content_hash = sha256(RawPayload.body) — the #54 integrity baseline, single chokepoint)
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
      identity.py     — Person/Organization/Role/Assignment + LifecycleMixin (archived_at + deleted_at tombstones — PM archived/deleted axis split, #38/#42) + Organization.active (PM's third axis: operational live/dissolved domain flag — orgs-only, NOT a live-read gate, #43) + OrganizationName (dated name variants mirrored from PM `OrgName`/power-map#239; `Organization.name` stays the resolved current scalar, this child table is the history/association surface, #45) + OrganizationAcronym (acronym variants mirrored from PM `OrgAcronym` — list distinct from names, no type/dated window; `Organization.acronym` stays the resolved current scalar — read mirror adopts PM's `is_canonical` acronym into it symmetric with `name`, #47/#65) + `Role` **seat model** (power-map#261/#263, usa-wa#68): a legislative seat is a durable Role keyed on the structural tuple `(organization_id, role_type, jurisdiction_id, qualifier)` — House = 2 seats/LD (`qualifier` "Position 1"/"Position 2"), Senate = 1 seat/LD (`qualifier` NULL). `Role.jurisdiction_id` (the seat's district, distinct from the org binding-root dropped in the 2026-06-09 decoupling) + `Role.qualifier` are the seat columns; uniqueness splits into two partial indexes — `uq_roles_seat` `(org, role_type, jurisdiction, qualifier)` WHERE jurisdiction NOT NULL (NULLS NOT DISTINCT) for districted seats, `uq_roles_org_name` `(org, name)` WHERE jurisdiction NULL for title-keyed committee/leadership roles
      role_types.py   — `RoleType` catalog mirror (power-map#268, usa-wa#68): local read-only cache of PM's role_types catalog (`GET /api/v1/role-types`) — `{slug, display_name, expects_jurisdiction, requires_qualifier}` keyed on slug (power-map#271/usa-wa#70 renamed the field from `is_seat`). The sync descriptor reads `expects_jurisdiction` to decide a Role observation's shape (seat-mode structural tuple vs title-mode) at runtime, retiring the hardcoded seat-slug map. `requires_qualifier` (power-map#273/usa-wa#71) is PM's **enforced** flag — a districted-seat observation of such a type (e.g. `state_representative`, per-position) without a `qualifier` is `REJECTED("qualifier_required")` rather than minting a positionless seat (#267); the descriptor mirrors it to defer such a seat pre-flight (`state_senator`=False → NULL qualifier valid). Refreshed by the sidecar's catalog sync ([`role_type_catalog.py`](packages/usa-wa-sync-powermap/src/usa_wa_sync_powermap/role_type_catalog.py) → `sync_role_type_catalog`, first cycle + hourly cadence)
      queries.py      — live_only(): read-side liveness guardrail (archived_at + deleted_at IS NULL) every live read routes through (#38/#42)
  clearinghouse-sync-powermap/        — Layer 1-adjacent: portable Power Map sync engine (sibling-reusable)
    src/clearinghouse_sync_powermap/
      descriptors.py  — EntityDescriptor contract (per-entity sync behaviour; zero usa-wa imports)
      engine.py       — SyncEngine: changes-feed + reconcile reads, LWW, outbox worker, backoff, merge-orphan anchor self-heal (#36) + merged_into generic re-resolution (#37)
      client.py       — PowerMapClient Protocol + value types (ObservationResult, ChangePage…)
      models.py       — sync-schema OutboxEntry + SyncState + EnrichFingerprint (delivery ledger + feed cursor + enrich re-propagation stamp)
      testing.py      — shipped test doubles (FakeEntity/Descriptor/Client) for this + sibling tests
      pmclient.py     — GeneratedPowerMapClient: adapts the generated SDK to the PowerMapClient Protocol
  powermap-client/                    — GENERATED OpenAPI client for Power Map (do not hand-edit)
                      — openapi-python-client output; excluded from ruff/coverage/pre-commit.
                        Regenerate when PM's API changes (see "Regenerating the PM client" below).
  usa-wa-adapter-legislature/         — Layer 3: WA Legislature SOAP source mapping
    src/usa_wa_adapter_legislature/
      adapter.py      — WALegislatureAdapter(BaseAdapter): discover/fetch_one/normalize; dispatches the resources — committees:<biennium> (GetActiveCommittees) + committees-roster:<biennium> (GetCommittees, sub-project 3) + committee-meetings:<begin>:<end> (GetCommitteeMeetings, #39) + **sponsors:<biennium>** (SponsorService.GetSponsors, P1b) + **committee-members:<committee_id>:<agency>:<name>** (GetActiveCommitteeMembers, P1b — the committee id rides the resource id + stamped url so normalize resolves the committee Org). normalize routes by the stamped URL. The **member** normalizers need the runner's `session` (constructor arg + `_require_session`) to resolve Person/Role ids for Assignment FKs; a distinct `member_client` isolates the members fan-out from the committees pull for tests
      synthesis.py    — pure functions emitting canonical-row dicts for anchors WSL doesn't expose (legislature/chamber/biennium/regular)
      bootstrap.py    — bootstrap_synthetic_anchors: idempotent ON CONFLICT DO NOTHING upserts of the 6 anchor rows; returns BootstrapAnchors
      transport.py    — WSLClient: per-service zeep wrapper with lazy WSDL load; SOAP calls via asyncio.to_thread. fetch_active_committees + fetch_committee_meetings + fetch_committees + **fetch_sponsors** (SponsorService.GetSponsors) + **fetch_committee_members** (CommitteeService.GetActiveCommitteeMembers, P1b) return WireFetch (parsed records + pristine SOAP wire for archival, #54). Non-archival parsed-dict siblings get_committees / **get_sponsors** / **get_active_committee_members** (probe/light reads). parse_committee_meetings / parse_committees / **parse_sponsors** / **parse_committee_members** re-deserialize an *archived* wire offline through the same operation binding (no data re-pull) — the #56 cache path; each guarded by a transport cassette round-trip test
      meeting_windows.py — biennium → (begin, end) window + committee-meetings:<begin>:<end> resource-id keying (#39); once-per-window cache key for docket frugality
      normalize/      — per-resource normalizers. **members.py**: shared member-cluster helpers (P1b) — `get_or_create_person`/`get_or_create_role` **SELECT-or-INSERT against the session** (flush for id) so Assignments carry real intra-batch FKs the runner can't resolve; `canonicalize_party` folds both endpoint encodings (R/D + Republican/Democrat); `is_person` (single source of truth, also imported by the probe) filters the name-blanked stubs; `district_number`/`ld_slug`/`resolve_ld_jurisdiction`; deterministic `source_id` builders; `EntityCollector` dedups by (type, source_id). **sponsors.py** (steps 4/5): per named row → Person + `wa_legislature_member_id` PersonIdentifier + party Assignment (major-party only; independent/blank → none) + Senate seat Role/Assignment (House emits no chamber Role/Assignment — deferred to #69). **committee_members.py** (step 6): per member → membership Assignment via the committee's shared `member` Role (no position — no WSL source). committees.py: WSL Committee → Organization (House/Senate → chamber, Joint → legislature; org_type='committee'). committee_meetings.py: meeting refs → Joint/`Other` Organizations (#39) — dedup by stable Id, name=LongName verbatim, short_name=Name, org_type='other', parent=legislature; House/Senate skipped (CommitteeService's domain). parent_for_agency shared (extended for 'Other'). Local `name` is the verbatim double-prefixed LongName *as produced* (the read mirror still adopts PM's curated canonical), while the PM-emitted name is the clean `short_name` for org_type='other' (`OrganizationDescriptor.observed_name`, #61). parent_for_agency + clean_field (normalize/fields.py) shared with committees.py
      committee_seed.py — frozen Joint/`Other` seed (de)serialization (deterministic bytes for stable hashing); DEFAULT_SEED_PATH = data/joint_other_committees_seed.json
      harvest_committee_meetings.py — backfill CLI (#39): sweep a biennium range through the runner (archive wire + upsert org_type='other'), then freeze the deduped cohort to the seed + seed_manifest sidecars. Closed windows = cache hits on re-run
      ingest_committee_seed.py — no-WSL seed loader (#39): verified_digest gates the bytes → synthetic FetchEvent.content_hash + archived RawPayload, fill-only upsert (seed is a floor, not an authority)
      refresh.py      — `python -m usa_wa_adapter_legislature.refresh` CLI entrypoint; biennium-from-date with USA_WA_BIENNIUM override. Daily run also pulls the current biennium's meeting window for additive Joint/`Other` discovery (best-effort; window-absence ≠ retirement, #39). The meeting pull is **forced** past the cache TTL (#63 — 24h TTL vs ~24h timer cadence was a fetch/skip jitter knife-edge): deterministic daily discovery, archival still dedup-bounded; committees stay TTL-governed. Force applies only to the date-current biennium — a `USA_WA_BIENNIUM` backfill of a closed window stays cache-governed (harvest owns closed-window re-pulls); non-current runs log `wsl_refresh_noncurrent_biennium` at warning (a stale env pin would otherwise silently redirect daily discovery). The refresh runs the `AdapterRunner` **`fill_only=True`** (#65): additive discovery *inserts* newly-appearing committees but **never overwrites an existing row** — `name`/`acronym` are PM-curated and the read-mirror resolves them, so re-writing them here would clobber the curation and bump `updated_at`, winning LWW against PM (the daily 4080-entry outbox ping-pong #65 diagnosed). Existing committees are PM's to maintain via the sidecar mirror; renames flow via the reconcilers. Daily run also drives the **member cluster** (P1b, `_discover_members`): the forced `GetSponsors` pull + a per-committee `GetActiveCommitteeMembers` fan-out (sequential; roster **enumerated from the DB** — the `org_type='committee'` rows scoped to `active` + live, so no extra GetActiveCommittees call and defunct backfilled committees are excluded). Both the meeting + member forced pulls set `skip_unchanged=True` (a byte-identical re-pull re-records the FetchEvent for the TTL/ledger but skips normalize+persist — no duplicate Citation set daily; distinct from harvest `--force`, which re-normalizes to re-materialize rolled-back rows)
      probe_committee_extent.py — write-free discovery CLI (#64): walks bienniums backward from current calling `GetCommittees` + `GetCommitteeMeetings`, tallying committee/meeting counts + meeting wire bytes, stopping after N consecutive empty bienniums (`--max-empty`, default 2; bounded by `--max-bienniums`). Talks to `WSLClient` **directly, not the runner** — no `FetchEvent`/`RawPayload` written; answers "how much history exists" to scope the sub-project 3 backfill. Also `probe_committee_floor` — a **committee-only** backward walk (GetCommittees only, no slow meeting pulls) to the earliest biennium with data, used by the harvest to auto-scope its range
      probe_member_identity.py — write-free discovery CLI (P1b sub-project, #27 step 0): answers "is the WSL member `Id` a stable `Person.source_id`?" before any member ingest. Talks to `WSLClient` **directly, not the runner** (no `FetchEvent`/`RawPayload`); matches members **by name** (`LastName`,`FirstName` — deliberately not `Id`) across two axes and tallies `Id` agreement: cross-endpoint (`SponsorService.GetSponsors` vs `CommitteeService.GetActiveCommitteeMembers`) and cross-biennium (`GetSponsors(current)` vs `GetSponsors(prior)`). **Finding (2026-07-06): `Id` is stable across endpoint, biennium, AND chamber change (94/94 + 125/125, 0 divergences) → canonical `source_id` = `GetSponsors.Id`, no name-match fallback.** `GetSponsors` returns **one row per (member, chamber-tenure)**: a member appears once per tenure under a stable `Id`, so a mid-biennium House→Senate mover has two *named* rows (Alvarado `34024`, V. Hunt `35410`) and a boundary mover / departed member carries a **name-blanked stub** (`"Representative "`/`"Senator "`, null name/district/party — Orwall/Slatter House stubs, departed Hawkins/Hunt/Rivers). `is_person` filters the blanked stubs; the sponsor normalizer iterates rows and dedups Person by `Id`. The non-archival transport pulls it uses — `get_sponsors` / `get_active_committee_members` — are the parsed-dict siblings of `get_committees` (archival `fetch_*` forms land in step 1)
      harvest_committees.py — Phase A backfill CLI (sub-project 3): sweep `GetCommittees(biennium)` over a range through `AdapterRunner(fill_only=True)`, archiving the full-roster wire under **`committees-roster:<biennium>`** (a distinct provenance key from the daily `committees:<biennium>` GetActiveCommittees archive — a different SOAP op) and materializing standing committees keyed by WSL `Id` **without clobbering** PM-curated rows (#65). **Identity = the WSL `Id`** (redesign model A): WSL re-keys committees across eras (same name, new `Id` ~every decade), so each `Id` is its own committee org and same-name bodies coexist — a re-key is a *different* committee (the sidecar's `pm_match` cross-Id guard keeps the historical rows from over-matching onto each other's PM orgs). `--pause-seconds` drips against WSL; auto-probes the floor when `--from-biennium` omitted; closed rosters are cache hits on re-run. No seed frozen (deferred). `--dry-run` rolls back
      committee_roster_cohort.py — `CommitteeRosterCohortProvider` (Phase B): biennium → `{source_id: LongName}`, **archive-first** (re-parses the archived `committees-roster:<biennium>` wire offline via `parse_committees`; live GetCommittees fallback only for an un-archived biennium). `archived_bienniums()` enumerates the chain's domain. The roster analog of `meeting_cohort.py` (#56)
      baseline_unbaselined_committees.py — one-off **owner-role** provenance repair CLI (#64): the pre-#54 `committees:2025-26` fetch events carry NULL `content_hash` but DID archive their bodies, so this backfills `content_hash = sha256(RawPayload.body)` (the same digest the runner derives) — converting them from "unbaselined" to integrity-verified while keeping the fetch history + bytes (no deletion). A payload-less NULL-hash event is counted `skipped_no_payload` and left alone. Idempotent. Needs `DATABASE_URL_OWNER` — the app role is REVOKEd UPDATE on the ledger (#54); `--dry-run` previews
  usa-wa-adapter-pdc/                 — Layer 3: WA PDC (Public Disclosure Commission) SODA source
    src/usa_wa_adapter_pdc/
      transport.py    — PDCClient: async `httpx` reader for the PDC `Campaign Finance Summary` Socrata dataset (`3h9x-7bvm`) on data.wa.gov. `fetch_house_winners(election_year)` GETs the seated House winner cohort (`office=STATE REPRESENTATIVE` ∧ `general_election_status='Won in general'` — one row per `(LD, position)`); `fetch_senate_winners(election_year)` (#75) is the Senate sibling (`office=STATE SENATOR` — one row per LD, ~half the chamber each even year). Both return `WireFetch` (pristine JSON bytes archived + hashed #54, plus the decoded rows) via a shared `_fetch_winners`/`_winners_params(office, year)`. `parse_house_winners` / `parse_senate_winners` are the offline re-parsers (#56 cache path). Optional `USA_WA_PDC_APP_TOKEN` → `X-App-Token` (rate-limit only, not auth — sent only when set)
      adapter.py      — PDCAdapter(BaseAdapter): source_slug `usa_wa_pdc`. `discover` yields `house-winners:<election_year>` (year = biennium start − 1; WA House is entirely up each even November) **and, when a `senate_roster` is supplied (#75), both staggered `senate-winners:<year>` cohorts** (`start-1` + `start-3` via `senate_election_years_for_biennium` — WA Senate is 4-yr staggered, so all sitting senators = the union of the two most-recent even years); `fetch_one` archives the SODA JSON, **stamping the resource id onto `FetchEvent.url` as a `#`-fragment** (the endpoint is chamber-agnostic — office is a query filter); `normalize` routes by that fragment (Senate → the identifier-only Senate normalizer, else House). **Session-aware** (`_require_session`) — the normalizers resolve the existing WSL Person (+ get-or-create the seat Role for the House Assignment's FKs). Holds the `house_roster` + `senate_roster` (both from one `GetSponsors` pull) the matches need
      normalize/positions.py — pure helpers: `canonical_position` (`"1"`/`"2"` → PM `qualifier` `"Position 1"`/`"Position 2"`, power-map#263); deterministic `house_seat_role_source_id`/`house_seat_assignment_source_id` (the latter `{member_id}:chamber-house:{biennium}`, symmetric with P1b's Senate `chamber-senate`)/`pdc_person_identifier_source_id`; `PDC_PERSON_ID_SCHEME='wa_pdc'`; `fold_token` + `surname_match_set` — **local** name matching (a Layer-3 adapter must not import the Layer-4 sidecar's `normalize_name`) robust to PDC's messy `filer_name` (`"JACOBSEN CYNTHIA P (Cyndy Jacobsen)"`): splits on whitespace/parens/commas only (so intra-surname hyphens/apostrophes stay in-token — `Ortiz-Self`) and adds consecutive-token joins (so a space-joined WSL surname — `Van De Wege` → `vandewege` — is testable by membership)
      normalize/house_positions.py — the **Position resolver** (#69): PDC is not a Person source. Per PDC winner, `build_house_roster` (WSL `GetSponsors` House rows → `(LD, folded-last)→member id`) + `_match_member` resolve the *existing* WSL `Person` within its LD (folded last name + party tiebreak; zero/ambiguous → `pdc_house_unresolved`, no guess). On a match: a `person_wa_pdc` child `PersonIdentifier` on that WSL Person (carried to PM as an `additional_identifier` — deterministic cross-link, no name-match) + get-or-create the House `state_representative` seat Role (`source=usa_wa_legislature`, symmetric with the Senate seat) + a chamber seat Assignment. A not-yet-ingested member is logged + skipped. **Mid-biennium replacement inference (#74):** a winner who moved to the Senate mid-biennium defers (their House row is a blanked stub); a second pass fills the vacated seat by within-LD elimination — if an LD has exactly one deferred winner + one unmatched roster member **and** the deferred winner reappears as that LD's sitting Senator (`build_senate_roster` confirming signal, guarding against masking a name-match miss), the unmatched member is assigned the deferred position. Such a seat is *inferred*: no `person_wa_pdc` id, a reduced-confidence `FactCitation`, `pdc_house_seat_inferred` log. Both-reps-moved (two deferrals) → ambiguous → `pdc_house_unresolved`. The confirmed mover's own `person_wa_pdc` (their PDC winner identity) is **cross-linked** onto their current (Senate) `Person` (`_link_pdc_identifier`), independent of whether the replacement's seat could be inferred
      normalize/senate_identity.py — the **Senate cross-link** (#75): the Senate counterpart to house_positions, but **identifier-only** (WSL's P1b already emits the single-seat-per-LD Senate Role/Assignment — no ballot Position for PDC to add). Per PDC Senate winner, match to the existing WSL Senate `Person` in its LD via `build_senate_roster` + `surname_match_set` (single seat/LD → unique) → attach a `person_wa_pdc` child `PersonIdentifier` (carried to PM as an `additional_identifier`, same as the House). No match / not-yet-ingested → `pdc_senate_unresolved` / `pdc_senate_person_absent` logs, plus a per-run `pdc_senate_summary` tally (winners/matched/unresolved/…) — the **robustness check on WSL** (PDC is an independent record of who won; a departed member's stale winner row is flagged, never force-matched; a stable handful of `unresolved` = departed senators, a spike = a real WSL break). Verified live 2026-07: 47/50 winners matched, the 3 misses all genuinely-departed senators. (house_positions emits a symmetric `pdc_house_summary`.)
      normalize/persons.py — shared `resolve_wsl_person(session, member_id)` (SELECT the WSL `Person` by `(source, member id)`) used by both PDC normalizers
      refresh.py      — `python -m usa_wa_adapter_pdc.refresh`: daily cycle. Resolves the biennium (USA_WA_BIENNIUM override, else current; non-current logs `pdc_refresh_noncurrent_biennium`), pulls `GetSponsors` **once** for both the House + Senate rosters, and drives PDCAdapter through the runner `fill_only=True` (#65 — additive, never clobbers PM-curated rows). Materializes House seat Assignments (#69) + Senate `person_wa_pdc` cross-links (#75) in one cycle. Runs **after** the WSL refresh (its Persons must exist)
  usa-wa-api/                         — Layer 4: WA deployment (FastAPI + MCP + REST)
    src/usa_wa_api/api/
      main.py         — App factory, lifespan, router registration
      deps.py         — FastAPI dependencies (DB session, auth)
    tests/            — API tests; conftest defines savepointed db_session + AsyncClient
  usa-wa-sync-powermap/               — Layer 4: PM sync deployment binding + sidecar daemon
    src/usa_wa_sync_powermap/
      descriptors/    — concrete EntityDescriptors (jurisdiction, organization, role, person, assignment) — full identity cluster + PM-first match cascade + enrich-on-match; the org `pm_match` name stage carries a **cross-Id re-key guard** (committee-backfill redesign, model A — identity is the WSL `Id`, same-name committees coexist): WSL re-keys committees across eras, so a normalized-name match can land on a PM org already claimed by a *different* committee; each candidate is detail-fetched (PM search omits identifiers) and dropped if it carries an `org_wa_legislature_committee_id` identifier → create-new, only an *unclaimed* same-name org is adopted (the over-match that crash-looped the sidecar); `events.py` is the entity-event sub-resource read-mirror (person/org `fetch_record` pulls `/{id}/events`, `upsert_from_pm` mirrors via `sync_entity_events`); `org_names.py` is the dated-name read-mirror (org `upsert_from_pm` mirrors the embedded `OrgDetail.names[]` via `sync_org_names` → `OrganizationName`, #45; **skip-and-logs** a `pm_org_name_id` already claimed by a different org so the global `(source, source_id)` key can't crash the cycle — redesign defense-in-depth); `org_acronyms.py` is the sibling acronym read-mirror (org `upsert_from_pm` mirrors the embedded `OrgDetail.acronyms[]` via `sync_org_acronyms` → `OrganizationAcronym`, same skip-and-log guard, and adopts PM's `is_canonical` acronym into the `Organization.acronym` scalar symmetric with `name`, #47/#65). `person.py` `to_observation` emits the Person's child `person_identifiers` rows (mapped scheme → PM slug via `SCHEME_TO_IDENTIFIER_TYPE`, skipping the source-derived primary) as `additional_identifiers` (#69) — so a cross-source identifier like PDC's `wa_pdc` on a WSL-sourced Person attaches to the *same* PM person the primary resolves (deterministic, no name-match); it propagates to the anchored cohort via the enrich-payload fingerprint drift (the base `to_enrich_observation` merges child `additional_identifiers` with the demoted primary)
      registry.py     — build_descriptors() (the entity set the sidecar syncs) + build_reconciler() (#73 Axis 1: wires `include_local_cohort=True` so the subscription set is the **mirror set** — jurisdiction `lineage` via PM discovery ∪ OUR locally-anchored producer rows — not the whole PM WA subtree, which over-subscribed ~1,000 PM-only strangers we never mirror; the produced rows are enumerated from the local anchored cohort so the feed still delivers PM's edits to them). Shared by bootstrap + `__main__`
      sidecar.py      — Sidecar: per-cycle tick (feed → reconcile → sweep → drain) + isolated run loop
      config.py       — SidecarSettings (POWERMAP_BASE_URL, POWERMAP_API_KEY)
      reconcile_committee_active.py — one-shot producer CLI (#44): diffs the produced committee cohort against `CommitteeService.GetCommittees(biennium)` and reconciles PM `active` both ways — `active=false` for committees the roster dropped, `active=true` for ones that reappear (reactivation self-heals a modest partial-pull false retirement on the next clean run). Guarded by an empty-pull check + cohort floor (denominator = active cohort); skips archived/deleted/unanchored; emit-only (PM stays authority for `active`, mirrors it back — no local write). Weekly timer (Sun 07:00 UTC, #48) + ad-hoc; out-of-band from routine sync (`to_observation` keeps `active` out, #43)
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
| PDC refresh (daily) | oneshot + timer | — | `systemctl` (`usa-wa-pdc-refresh.timer` → `.service`; 06:30 UTC, #69). Pulls the seated House winner cohort → House `state_representative` seat Assignments (District + Position). Ordered after the WSL refresh (binds onto its House Persons) |
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
`usa-wa-wsl-refresh`, `usa-wa-pdc-refresh`, `usa-wa-reconcile-committee-active`,
`usa-wa-reconcile-committee-names`,
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
don't route through this one-shot alert.

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

**Deploy convention: units never sync the venv (issue #30).** Every systemd
entrypoint runs `uv run --frozen --no-sync` (`usa-wa.service`,
`usa-wa-sync-powermap.service`, `usa-wa-wsl-refresh.service`,
`usa-wa-pdc-refresh.service`,
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
| After editing `deploy/usa-wa-reconcile-committee-active.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-reconcile-committee-active.timer` |
| After editing `deploy/usa-wa-reconcile-committee-names.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-reconcile-committee-names.timer` |
| After editing `deploy/usa-wa-reconcile-committee-meeting-names.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-reconcile-committee-meeting-names.timer` |
| After editing `deploy/usa-wa-integrity-sweep.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-integrity-sweep.timer` |
| After editing `deploy/usa-wa-notify-failure@.service` | `sudo systemctl daemon-reload` (templated `OnFailure=` handler — nothing to restart; next failure picks it up) |
| After DB model changes | `sudo systemctl restart usa-wa-migrate` (runs alembic + grants under the owner role), then restart usa-wa — run `uv sync --locked` first if `uv.lock` changed (`migrate.sh` is `--no-sync`). **`restart`, not `start`** — the unit is a `RemainAfterExit` oneshot, so once it's `active (exited)` from an earlier migrate this boot, `start` is a silent no-op (exits 0, applies nothing). |
| Run the WSL refresh now (ad-hoc) | `sudo systemctl start usa-wa-wsl-refresh.service` |
| Run the PDC refresh now (ad-hoc) | `sudo systemctl start usa-wa-pdc-refresh.service` |
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
- `USA_WA_ALERT_EMAIL` — recipient for oneshot failure alerts (#49). Consumed by `scripts/notify-failure.sh` (the `usa-wa-notify-failure@.service` `OnFailure=` handler). Must be **you / an exe.dev team member** (gateway recipient allow-list). The script **fails closed** if unset, so set it in `/etc/usa-wa/.env` to arm alerting. See § Failure alerting.

PM sidecar tunables (`SidecarSettings`, env-overridable): `OUTBOX_COMMIT_CHUNK_SIZE` (delivered entries per DB commit during a drain; default 1 = per-entry), `POWERMAP_SEARCH_MATCH_CAP` (max candidate window the org/person name-match cascade pages; default unset = per-entity default), `SUBSCRIPTION_BACKSTOP_CADENCE` (how often the full-subtree re-discovery walk re-runs; default 6h — #73 Axis 2, graph drift is slow) and `RECONCILE_CADENCE` (anchored-cohort backstop re-fetch of OUR whole cohort by id, each person also pulling `/events`; default 12h — #73 Axis 2, a dropped-feed-event safety net for a low-churn dataset, applied to the producer descriptors in `build_descriptors`; the feed is the real-time path).

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

# WSL refresh — one-shot pull from CommitteeService.GetActiveCommittees, plus an
# additive current-biennium meeting-window pull for Joint/Other discovery (#39).
# Prod runs this daily at 06:00 UTC via the usa-wa-wsl-refresh.timer systemd
# unit; the command below is the manual / backfill form (pair with USA_WA_BIENNIUM).
python -m usa_wa_adapter_legislature.refresh

# PDC refresh (#69 + #75) — source WA House members' Position (1/2), which WSL doesn't expose,
# from the PDC Campaign Finance Summary Socrata dataset (3h9x-7bvm) on data.wa.gov, and emit the
# House state_representative seat Assignment P1b deferred. Pulls the seated House winner cohort
# for the biennium's election year (start-1), pulls GetSponsors ONCE for both the House + Senate
# rosters, matches each winner to the existing WSL Person (within LD, by folded last name +
# party), and attaches a person_wa_pdc identifier + seat Assignment (qualifier="Position N").
# ALSO (#75) discovers both staggered Senate winner cohorts (start-1 + start-3) and cross-links a
# person_wa_pdc identifier onto each sitting Senator's WSL Person (identifier-only — WSL owns the
# Senate seat); a departed member's stale winner row logs pdc_senate_unresolved (WSL robustness
# check). fill_only (#65). Prod runs this daily at 06:30 UTC (after the WSL refresh) via
# usa-wa-pdc-refresh.timer; the form below is the manual / backfill surface (pair with
# USA_WA_BIENNIUM). USA_WA_PDC_APP_TOKEN (optional) raises Socrata's rate limit.
python -m usa_wa_adapter_pdc.refresh

# Joint/Other committee backfill (#39) — sweep CommitteeMeetingService.GetCommitteeMeetings
# over a biennium range (the only source of Joint/Other committees), archiving the pristine
# SOAP wire and upserting org_type='other' rows, then FREEZE the deduped durable cohort to
# data/joint_other_committees_seed.json (+ .sha256/.meta.json sidecars). Hits live WSL (one
# POST per window) AND mutates the DB — not read-only; --dry-run still upserts but skips the
# seed write. Closed windows are cache hits on re-run. Commit the produced seed.
python -m usa_wa_adapter_legislature.harvest_committee_meetings --from-biennium 2023-24 --to-biennium 2025-26

# Joint/Other seed ingest (#39) — the no-WSL counterpart: materialize the frozen cohort on a
# fresh deploy. verified_digest gates the seed bytes (fails closed on a sidecar mismatch),
# writes a synthetic hashed FetchEvent + archived RawPayload, and fill-only upserts (existing
# rows untouched — the seed is a floor, not an authority). Needs the committed seed file.
python -m usa_wa_adapter_legislature.ingest_committee_seed

# Contact-label backfill (#31) — re-observation of produced orgs holding a phone,
# so PM adopts the synthesized contact display_label. Idempotent + re-runnable;
# --dry-run counts the cohort without submitting. No operator token (shell = trust boundary).
# Since #34 the sidecar self-heals carry-field drift on its own (anchored-cohort
# reconcile re-enqueues an ENRICH on a local-fingerprint mismatch), so this is now a
# force-push convenience, not the only recovery path.
python -m usa_wa_sync_powermap.backfill_contact_labels --dry-run
python -m usa_wa_sync_powermap.backfill_contact_labels

# Committee active-flag reconciliation (#44) — reconciles PM `active` for WSL committees
# against the current biennium's `GetCommittees(biennium)` roster: `active=false` for the
# absent, `active=true` for the returning (reactivation self-heals a transient partial-pull
# false retirement next cycle). Explicit-membership diff (not current-only
# GetActiveCommittees), guarded by an empty-pull abort + a cohort floor (--max-absent-fraction,
# default 0.34) so a partial WSL pull can't mass-retire. Skips archived/deleted/unanchored;
# emit-only (PM mirrors `active` back). Idempotent; no operator token (shell = trust boundary).
# Prod runs this weekly (Sun 07:00 UTC) via usa-wa-reconcile-committee-active.timer (#48);
# the forms below are the manual / backfill / dry-run surface.
# --dry-run previews the diff. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_active --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_active --biennium 2025-26

# Committee rename detection (#46) — write-side sibling of #45's read mirror. Diffs
# `GetCommittees(current)` vs `GetCommittees(prior)` on the stable `Id`; a changed
# `normalize_name(LongName)` is a rename. Emits windowed dated-name evidence (prior name
# typed `former`, effective_end = biennium-start boundary; new name typed `legal`,
# effective_start = same, open end — #58) so PM curates is_canonical and the #45 read mirror
# brings the windows back — emit-only, no local
# write. Diffs WSL's RAW LongName, not the PM-resolved Organization.name scalar (which would
# false-fire on PM canonicalisation + miss round-tripped renames). Guarded by empty-pull
# (either roster) + low-overlap (--min-overlap-fraction, default 0.5; stable WSL Ids → a real
# diff overlaps heavily, so a thin overlap = wrong-biennium pull) + rename-storm floor
# (--max-rename-fraction, default 0.34). Skips unanchored + live-cohort-absent (hidden vs
# unproduced). Idempotent; no operator token (shell = trust boundary).
# Prod runs this weekly (Sun 07:30 UTC) via usa-wa-reconcile-committee-names.timer (#53),
# staggered 30 min off the active reconcile; the forms below are the manual / dry-run surface.
# --dry-run previews. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_names --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_names --biennium 2025-26

# Joint/Other rename detection (#56) — meeting-derived sibling of #46 for the org_type='other'
# class CommitteeService can't see (#39; e.g. ESEC Id 13945). Diffs two bienniums'
# GetCommitteeMeetings-derived cohorts (current + prior) on the stable `Id`; the cohort name
# is the CLEAN `Name` (#61 observed_name), not the double-prefixed LongName stored as
# Organization.name — so the "Joint Joint …" form never reaches PM. Same windowed emit +
# shared spine as #46, but re-tuned guards for a dormancy-prone cohort: low-overlap OFF by
# default (--min-overlap-fraction 0.0 — window-absence is dormancy, not a wrong-biennium
# signal) and the storm fraction only weighed past --storm-floor-min-overlap (default 5).
# Window-absence ≠ rename (intersects ids present in BOTH windows). Emit-only; idempotent; no
# operator token. Archive-first + read-only: a closed window is re-parsed offline from the
# RawPayload the daily refresh / #39 harvest already archived (no ~1.5MB re-pull); only an
# un-archived window falls back to a live, un-archived pull. Prod runs this weekly (Sun 07:45
# UTC) via usa-wa-reconcile-committee-meeting-names.timer, staggered 15 min off #46.
# --dry-run previews. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# NOTE backfill: the detector diffs current-vs-PRIOR biennium, so an older rename (ESEC =
# 2023) needs a targeted --biennium 2023-24 (diffs vs 2021-22) to surface.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_meeting_names --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_meeting_names --biennium 2023-24

# Provenance integrity sweep (#54/#55) — re-hashes stored RawPayload bodies against
# their FetchEvent.content_hash baseline; a divergence is corruption/tamper at rest.
# NULL baselines (pre-#54 legacy) are counted as "unbaselined", never a mismatch.
# Exit 0 clean / 1 mismatch (the non-zero the #49 OnFailure handler emails on).
# The default run is a ROLLING byte-slice (#55): it verifies --byte-budget (default
# 256 MiB) worth of payloads past a persisted ULID watermark and wraps at the archive
# tail, so per-run cost stays flat as the #39 docket volume grows (whole corpus
# re-verified every ceil(bytes/budget) runs). Its one write is the cursor upsert on
# clearinghouse_core.integrity_sweep_state (app-role DML; not the provenance tables).
# --full forces a whole-corpus pass ignoring the cursor (post-incident audit);
# --limit N is a row-capped partial (surfaced as limited). Prod runs this weekly
# (Sun 08:00 UTC) via usa-wa-integrity-sweep.timer.
python -m clearinghouse_core.integrity                # rolling slice (resumes + wraps)
python -m clearinghouse_core.integrity --full         # whole corpus, ignore cursor
python -m clearinghouse_core.integrity --limit 500    # row-capped partial

# Committee ↔ PM validation (#64) — read-only. For each PM-linked produced org, diff local
# canonical state against PM's live OrgDetail and bucket discrepancies (name/acronym/window/
# parent drift, unlinked/missing/merged), splitting reconciled (PM curation roundtripped)
# from divergent. Emit-nothing; sequential reads + bounded backoff. No operator token.
# Exit 0 clean / 1 divergent / 2 auth / 3 empty-cohort abort.
python -m usa_wa_sync_powermap.validate_committees          # human table
python -m usa_wa_sync_powermap.validate_committees --json   # machine-readable

# Force-adopt PM curation for LWW-locked committees (#65 Part 2) — one-shot heal. For the
# anchored produced cohort, re-fetch each PM OrgDetail and force-apply it (upsert_from_pm +
# clock-parity stamp), bypassing LWW. Unsticks committees the pre-fill-only refresh locked
# out of PM's curation. Idempotent (no-op at parity). App-role local write; no token.
python -m usa_wa_sync_powermap.heal_committee_curation --dry-run
python -m usa_wa_sync_powermap.heal_committee_curation

# Subscription prune (#73 Axis 1 step 6) — one-shot reclaim. build_reconciler narrowed the
# subscription set to the mirror set (jurisdiction lineage ∪ OUR anchored producer rows), but
# sync_subscriptions is additive, so the ~1,000 PM-only strangers the old whole-subtree walk
# registered stay subscribed-but-inert (feed delivers, reconciler fetch-then-skips them). This
# diffs PM's list_subscriptions against the freshly-discovered mirror set and unsubscribes the
# difference. Guarded: empty desired-set aborts (empty_desired), stale fraction over
# --max-prune-fraction aborts (prune_floor, default 0.9 — permissive since the first run removes
# ~half). Strangers have no local row (nothing evicted); no operator token. --dry-run previews.
# Exit 0 clean / 2 auth / 3 aborted. RE-RUN TO CONVERGENCE: PM auto-subscribes the producer on
# observation write, so a concurrently-draining outbox regenerates a shrinking residual — the
# first pass over a busy system removes the bulk, then re-run until a --dry-run shows stale=0
# (best run when the outbox is quiescent). Observed 2026-07-07: 1226 → 303 → 31 → 0.
python -m usa_wa_sync_powermap.prune_subscriptions --dry-run
python -m usa_wa_sync_powermap.prune_subscriptions   # re-run until dry-run shows stale=0

# Committee historical extent probe (#64) — write-free discovery. Walks bienniums backward
# from current, tallying committee/meeting counts + meeting wire bytes, stopping after N
# consecutive empty bienniums. Talks to WSL directly (NOT the runner) — no FetchEvent/
# RawPayload written. Answers "how much history exists" to scope the sub-project 3 backfill.
python -m usa_wa_adapter_legislature.probe_committee_extent
python -m usa_wa_adapter_legislature.probe_committee_extent --start-biennium 2025-26 --max-empty 2

# Member Id-stability probe (P1b #27 step 0) — write-free discovery. Answers "is the WSL
# member Id a stable Person.source_id?" before member ingest: matches members BY NAME
# (not Id) across GetSponsors vs GetActiveCommitteeMembers (cross-endpoint) and
# GetSponsors(current) vs GetSponsors(prior) (cross-biennium), tallying Id agreement.
# Talks to WSL directly (NOT the runner). Finding 2026-07-06: Id stable both axes →
# canonical source_id = GetSponsors.Id. --json for compact output.
python -m usa_wa_adapter_legislature.probe_member_identity
python -m usa_wa_adapter_legislature.probe_member_identity --biennium 2025-26 --json

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

# One-off provenance repair (#64) — OWNER ROLE. The pre-#54 committees:2025-26 fetch
# events have NULL content_hash but DID archive their bodies, so backfill
# content_hash = sha256(RawPayload.body) — converting them to integrity-verified while
# keeping the fetch history + bytes (no deletion). Payload-less NULL-hash events are
# skipped+counted. Idempotent. Needs DATABASE_URL_OWNER (the app role is REVOKEd UPDATE
# on the ledger, #54). --dry-run previews.
python -m usa_wa_adapter_legislature.baseline_unbaselined_committees --dry-run
python -m usa_wa_adapter_legislature.baseline_unbaselined_committees
```

Full reference: `docs/COMMANDS.md`

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
