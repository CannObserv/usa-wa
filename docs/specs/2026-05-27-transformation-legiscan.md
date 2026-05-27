# Transformation spec — LegiScan → hybrid legislative IA

- **Date:** 2026-05-27 (review update 2026-05-28)
- **Status:** final (revisions feed hybrid IA v1; v1.1 landed)
- **Direction:** **LegiScan → ours, only.** usa-wa consumes LegiScan via indirect-provider adapters; we never publish to LegiScan. The `our → legiscan` columns preserved below remain useful as a schema-completeness diagnostic but are not adapter behavior.
- **Scope:** Field-level mapping between every entity in [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md) and the JSON wire shapes returned by LegiScan API v1.91 endpoints.

## 2026-05-28 review update

- **Unidirectional** (see Direction above). The `→` and `↔` arrows in the per-entity tables that imply emit-to-LegiScan are documentation-only; treat as schema completeness checks, not transformation behavior.
- **Bill titles are 1:N in the hybrid IA (v1.1).** LegiScan exposes `Bill.title` (the canonical title) plus `Bill.description` (a longer summary). Mapping:
  - LegiScan `Bill.title` → `canonical.bill_titles` row with `title_type='canonical'`, `is_current=true`, AND denormalized to `canonical.bills.title`.
  - LegiScan `Bill.description` → `canonical.bills.short_description` (single value; **not** a title — it's the summary).
  LegiScan does not surface multi-classified or chamber-specific titles, so its inbound rows are simpler than OCD's or uscongress's.
- **Person rich attributes defer to Power Map.** LegiScan's `Person` exposes external-ID schemes (`ftm_eid`, `votesmart_id`, `opensecrets_id`, `knowwho_pid`, `ballotpedia`, `bioguide_id`) plus role context. The ID schemes already go to our `canonical.person_identifiers` (v1). Any biographical or contact data LegiScan eventually adds defers to Power Map (`locations`, `contact_methods`, `links`, `note`, planned `lifecycle_events`). usa-wa's local Person carries identity essentials only.
- **References:**
  - Hybrid IA v0: [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)
  - Multi-state IA delta (LegiScan section): [`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`](../research/2026-05-26-multi-state-legislative-ia-delta.md)
  - LegiScan field shapes were verified against two open-source typed clients tracking LegiScan v1.91:
    - `populist-vote/legiscan` (Rust crate, strongly typed structs in `src/api/*.rs`)
    - `sh-patterson/legiscan-mcp` (TypeScript, `src/types/legiscan.ts` — explicitly annotated "Based on LegiScan API v1.91 User Manual")
  - Free-tier quota: 30,000 queries / month (per `sh-patterson/legiscan-mcp` README §"API Limits").
  - LegiScan PDF manual at <https://legiscan.com/misc/LegiScan_API_User_Manual.pdf> returned HTTP 403 from the WebFetch environment; static-value tables below cite the typed clients (cross-checked between Rust and TS) as the authoritative substitute.

## Why this exists

Dual purpose. **(a) Completeness check:** prove that the hybrid IA v0 can losslessly absorb every field LegiScan publishes for a WA bill, so that whichever transformation we land on next (WSL SOAP, OpenStates, etc.) we already know the v0 shape survives contact with a commercially curated schema. **(b) Adapter blueprint:** specify exactly how a future `usa_wa_legiscan` adapter would map LegiScan JSON onto our canonical tables, in case usa-wa decides to corroborate WSL SOAP with LegiScan's normalized vocab or use LegiScan as a fallback when SOAP is rate-limited.

## Schema-level orientation

LegiScan is a commercial API, not a formal data model. It exposes flat JSON shapes per *endpoint* (`getBill`, `getRollCall`, `getPerson`, etc.) with extensive denormalization optimized for bill-tracking dashboards. Our IA's canonical entities map onto endpoint *outputs*, not onto LegiScan tables. The relevant operations and what they return:

| LegiScan op | Returns | Caching cadence (per Rust crate hints) | Our entities sourced |
|---|---|---|---|
| `getSessionList(state)` | array of `Session` | daily | `LegislativeSession` |
| `getMasterList(session_id\|state)` | `Record<bill_id, MasterListItem>` (lightweight summary) | hourly | `Bill` (summary tier) |
| `getMasterListRaw` | `Record<bill_id, {bill_id, number, change_hash}>` | hourly | change-detection seed |
| `getBill(bill_id)` | one fat `Bill` object (sponsors, history, sasts, subjects, texts, votes refs, amendments refs, supplements refs, calendar) | 3 hours | `Bill`, `BillSponsorship`, `BillAction`, `BillVersion` (metadata), `Amendment` (refs), `VoteEvent` (summary refs) |
| `getBillText(doc_id)` | `BillText` with base64 doc | static | `BillVersion.current_text` (deferred to P3) |
| `getAmendment(amendment_id)` | `Amendment` with base64 doc | static | `Amendment.amendment_text` |
| `getSupplement(supplement_id)` | `Supplement` (fiscal note / analysis / etc.) with base64 doc | static | *no v0 home* — see lossy directions |
| `getRollCall(roll_call_id)` | `RollCall` with `votes[]` per-legislator | static | `VoteEvent`, `VoteCount`, `PersonVote` |
| `getPerson(people_id)` | `Person` with external IDs (ftm_eid, votesmart_id, opensecrets_id, knowwho_pid, ballotpedia, bioguide_id) | weekly | `Person` (+ identity-cluster gap) |
| `getSessionPeople(session_id)` | `Session` + `Person[]` | weekly | bulk-seed `Person` + `Assignment` |
| `getSponsoredList(people_id)` | `Person` + `Session[]` + `SponsoredBillItem[]` | weekly | `BillSponsorship` enumeration |
| `getSearch(state, query[, year[, page]])` | paged `SearchResult[]` | 1 hour | discovery only |
| `getSearchRaw` | `bill_id + change_hash` (no display fields) | 1 hour | change-detection seed |
| `getDatasetList([state[, year]])` | available `Dataset[]` (with `access_key`) | weekly | bulk-export catalog |
| `getDataset(session_id, access_key)` | base64 ZIP of full session export (JSON + CSV) | weekly | bulk-load all of the above |
| `getMonitorList`, `getMonitorListRaw`, `setMonitor` | account-scoped tracking list | live | n/a (operational) |

Where the shape essentially matches us: `Bill`, `Session`, `RollCall` per-legislator votes, sponsor IDs+roles. Where LegiScan diverges meaningfully: no `Person`-as-identity separate from a sponsor's role (LegiScan inlines name + external IDs into each `Sponsor`), no `Organization` graph (committees exist but only as bill-attached records, not as first-class entities with parent/role/assignment), no archetype for `Role`/`Assignment` (membership is implicit in sponsorship + session-people queries), no statute corpus, no campaign-finance / lobbying domain at all.

---

## Per-entity correspondence tables

Direction key: **→** = usa-wa to LegiScan (synthesize the API payload from canonical rows; serializer use case, rarely needed except for testing). **←** = LegiScan to usa-wa (the adapter direction). **↔** = bidirectional, no information loss either way.

### Universal entity shape (provenance spine)

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `id` (ULID) | — | — | — | generated locally | LegiScan IDs are integers, not ULIDs. |
| `jurisdiction_id` | `state` + `state_id` | most | ← | `state == "WA"` → `"usa-wa"`; `state_id == 47` is WA's LegiScan id | `state` is universal; `state_id` only via getBill/getPerson/getDataset. |
| `source` | — | — | ← | adapter literal: `"usa_wa_legiscan"` | LegiScan has no equivalent self-identification field. |
| `source_id` | varies per entity | varies | ← | per-entity LegiScan integer ID stringified (e.g., `bill_id`, `people_id`, `roll_call_id`, `session_id`, `amendment_id`, `doc_id`) | LegiScan IDs are globally unique per LegiScan account context. |
| `primary_source_id` | — | — | — | n/a (denorm to local `sources` row) | |
| `last_fetched_at` | — | — | ← | wall-clock at fetch time | |
| `last_fetch_event_id` | — | — | ← | local `fetch_events` PK | |
| `created_at` / `updated_at` | — | — | — | server defaults | |

### `LegislativeSession` ↔ `Session` (`getSessionList`, `getBill.session`)

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `id` | — | — | — | local ULID | |
| `jurisdiction_id` | `state_id` → state code | `getSessionList` | ← | int → slug | WA `state_id == 47`. |
| `source_id` | `session_id` | `getSessionList` | ↔ | stringify | LegiScan's stable integer key. |
| `slug` | derive from `year_start` + `special` + counter | — | → / ← | `"usa-wa-{year_start}"` for regular; `"usa-wa-{year_start}-special-{n}"` when `special > 0` | LegiScan does not provide our OpenStates-style slug; adapter mints it. |
| `name` | `session_name` (preferred) or `session_title` | `getSessionList` | ↔ | passthrough | |
| `classification` | derived: `special` flag | `getSessionList` | ← | `special == 0` → `regular`; `special >= 1` → `special` | Our `sine_die` / `extraordinary` / `other` have no LegiScan equivalent — LegiScan only distinguishes regular vs. special. **Lossy →**. |
| `start_date` | `year_start` (year only) | `getSessionList` | ← | `date(year_start, 1, 1)` as a coarse approximation | **Lossy ←** — LegiScan gives year not exact convene date. Use WSL SOAP for precise dates; LegiScan for cross-source mapping. |
| `end_date` | `year_end` (year only) | `getSessionList` | ← | `date(year_end, 12, 31)` coarse approximation | Same caveat. |
| `is_active` | `sine_die` (inverse) + `prior` | `Session` | ← | `is_active = (sine_die == 0 AND prior == 0)` | LegiScan's `prior == 1` means archived; `sine_die == 1` means session has adjourned permanently. |
| `biennium_label` | none | — | → | derived locally; WA-specific | LegiScan has no biennium concept; it slices by year-pair via `year_start` + `year_end`. |
| — | `prefile` (0/1) | `Session` | ← | drop or store on a v1 flag column | WA has a pre-filing window; LegiScan flags it. Worth capturing — see Open revisions §3. |
| — | `session_tag` | `Session` | ← | drop or store | LegiScan-internal display token like `"2025R1"`. |
| — | `session_hash` / `dataset_hash` | `Session` / `Dataset` | ← | drop or use for change detection | Hash of all bills in the session — useful for "has anything changed?" loops. |

### `Bill` ↔ `Bill` object (`getBill`)

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `legislative_session_id` | `session_id` (FK) + embedded `session{}` object | `getBill` | ↔ | resolve via local `(jurisdiction_id, source, source_id=session_id)` lookup | |
| `chamber` | `body` (originating, text like "H" or "S") + `body_id` (int) | `getBill` | ← | `body == "H"` → `house`, `"S"` → `senate` | LegiScan also exposes `current_body` / `current_body_id` (where the bill is now). We currently only have one `chamber` column. **Lossy ←** — see Open revisions §1. |
| `number` | `bill_number` (string, e.g., `"HB1234"`) | `getBill` | ← | parse numeric tail | |
| `bill_type` | `bill_type` (string, e.g., `"B"`, `"JR"`) + `bill_type_id` (string) | `getBill` | ↔ | map via 23-value LegiScan `BillType` enum (see vocab below) | Rust crate types `bill_type_id` as `String`; TS types it as `string` too. Treat as opaque code. |
| `title` | `title` | `getBill` | ↔ | passthrough | LegiScan's `title` is the short headline — aligned with our v0 rename (was inverted in P0). |
| `short_description` | `description` | `getBill` | ↔ | passthrough | LegiScan's `description` is the long statement-of-effect. |
| `current_status` | `status` (integer in `BillStatus` enum) + `status_date` | `getBill` | ← | map integer via 13-value vocab table below | LegiScan denormalizes "current status" onto the bill — same pattern we adopted. |
| `current_step` | derive from `progress[]` last entry | `getBill` | ← | take `progress[-1].event` and resolve via the LegiScan event-code table | Approximate — LegiScan's `progress[]` is a curated milestone log (3-12 events per bill). See vocab below. |
| `introduced_at` | `progress[0].date` if `progress[0].event` is "Introduced" (event=1), else `history[]` filter | `getBill` | ← | parse date; fallback to first `history[]` action with action text matching "introduced" | LegiScan doesn't expose an explicit `introduced_at` column. |
| `current_text` | resolve from `texts[-1]` then `getBillText(doc_id)` returning base64 `doc` | `getBill` + `getBillText` | ← | base64 decode by `mime`/`mime_id` | Two-call hydration; LegiScan returns metadata in `texts[]` array, body via `getBillText`. |
| — | `change_hash` | `getBill` | ← | store for change detection on `bills.last_fetch_event_id` cycle | |
| — | `completed` (0/1) | `getBill` | ← | drop or fold into status | |
| — | `state_link` | `getBill` | ← | store as a `citations` row pointing at the WA Leg URL | LegiScan helpfully resolves the upstream WA Legislature URL — useful for cross-checking SOAP and LegiScan agree on the bill. |
| — | `referrals[]` | `getBill` | ← | normalize to `BillAction` rows with `action_type="referral"` and `acting_organization_id=committee_id` | Cross-references inside the bill payload. |
| — | `pending_committee_id` | `getBill` | ← | derive `current_step` text | The committee the bill is currently sitting in. |
| — | `subjects[]` (array of `{subject_id, subject_name}`) | `getBill` | ← | **no v0 home** | See Open revisions §6 — subject tagging is a confirmed multi-source pattern (OCD has it, LegiScan has it, unitedstates has it) and we have no place to put it. |
| — | `sasts[]` (Same-As/Similar-To, 9 relation types) | `getBill` | ← | **no v0 home** | See Open revisions §7 — `RelatedBill` was deferred in P0 Tier 2; transformation specs strengthen the case. |
| — | `calendar[]` (hearings/exec sessions/markups) | `getBill` | ← | **no v0 home** | See Open revisions §8 — hearing scheduling has no canonical entity yet. |

### `BillSponsorship` ↔ `Sponsor` (embedded in `getBill`)

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `bill_id` | parent `Bill.bill_id` | `getBill` | ← | resolve to local Bill ULID | |
| `person_id` | `people_id` | `getBill.sponsors[]` | ← | resolve to local Person; or insert from `getPerson(people_id)` if unseen | Set when `committee_sponsor == 0`. |
| `organization_id` | `committee_id` (when `committee_sponsor != 0`) | `getBill.sponsors[]` | ← | resolve to local Organization (committee) | LegiScan confirms our committee-as-sponsor polymorphism. WA in practice never emits `committee_sponsor=1`, so this path is exercised only by federal / other-state mappings. |
| `role` | `sponsor_type_id` | `getBill.sponsors[]` | ↔ | 4-value table: `0` Generic → `generic`, `1` Primary → `primary`, `2` Co → `co`, `3` Joint → `joint` | **Exact alignment.** Our 4-value vocab was chosen to match LegiScan's. |
| `sponsor_order` | `sponsor_order` | `getBill.sponsors[]` | ↔ | passthrough (1-indexed both sides) | |
| `withdrawn_at` | — | — | — | nullable; LegiScan doesn't track cosponsor withdrawals | **Lossy ←** — our `withdrawn_at` is informed by `unitedstates/congress`. LegiScan flatly does not surface this. |

### `BillAction` ↔ `HistoryStep` (embedded in `getBill.history[]`)

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `bill_id` | parent | ← | | resolve to local Bill ULID | |
| `action_at` | `date` | `getBill.history[]` | ← | parse ISO date (no time component in LegiScan history) | **Lossy ←** — LegiScan's history is date-precision only; WSL SOAP provides timestamps. |
| `chamber` | `chamber` (text) + `chamber_id` (int) | `getBill.history[]` | ← | `"H"` → `house`, `"S"` → `senate`, `""` → null | |
| `acting_organization_id` | inferred from `chamber` + `referrals[]` parallel lookup | `getBill` | ← | resolve chamber/committee Org | LegiScan does not directly tag each history step with the committee — only the chamber. For committee actions we fall back to text matching `action`. **Lossy ←**. |
| `action_type` | `action` (free text) | `getBill.history[]` | ← | passthrough as source-vocab text; v1 normalization deferred | LegiScan's `action` is the human description. The closest thing to a classification is the `importance` boolean (see Open revisions §2). |
| `description` | `action` | `getBill.history[]` | ← | passthrough | Same field doubles as both action_type text and description in LegiScan. |
| — | `importance` (0/1) | `getBill.history[]` | ← | **no v0 home** | LegiScan's "is this a major lifecycle step" boolean — used to filter `progress[]`. See Open revisions §2. |

LegiScan also surfaces `progress[]` — a precomputed array of `{date, event}` for *just* the major milestones, with `event` being an integer from LegiScan's event vocab. This is `history[]` filtered to `importance == 1`. Adapter strategy: skip `progress[]` in primary ingestion (use the full `history[]`), but optionally use `progress` for cheap dashboard summaries.

### `BillVersion` ↔ `TextReference` (embedded in `getBill.texts[]`)

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `bill_id` | parent | ← | | | |
| `source_id` | `doc_id` | `getBill.texts[]` | ↔ | stringify | |
| `version_type` | `type_id` (int) + `type` (string) | `getBill.texts[]` | ← | 14-value `TextType` table — see vocab below | LegiScan's vocab is richer than ours (`original`/`substitute`/`engrossed`/`first_engrossed`/`enrolled`). See Open revisions §4. |
| `version_at` | `date` | `getBill.texts[]` | ← | parse date | |
| `is_current` | inferred: last entry by `date`, or matching the bill's `status` | — | ← | local computation | LegiScan does not flag a "current" version — adapter computes it. |
| — | `mime` + `mime_id` | `getBill.texts[]` | ← | drop (or store on `BillVersionLink` post-v1) | We're single-version per row; LegiScan keeps mimetype on the row but doesn't return multiple mimetypes for the same version. |
| — | `url`, `state_link` | `getBill.texts[]` | ← | store as citation | |
| — | `text_size`, `text_hash` | `getBill.texts[]` | ← | drop in metadata-only ingest; use `text_hash` for change detection if we hydrate text | |

### `Amendment` ↔ `Amendment` (`getBill.amendments[]` ref + `getAmendment(amendment_id)`)

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `bill_id` | parent `Bill.bill_id` or `Amendment.bill_id` | both | ← | resolve to local | |
| `source_id` | `amendment_id` | `getBill.amendments[]` / `getAmendment` | ↔ | stringify | |
| `label` | `title` | both | ↔ | passthrough | LegiScan example: "Senate Amendment 001". |
| `amendment_text` | `doc` (base64) | `getAmendment` | ← | base64 decode by `mime`/`mime_id` | Two-call hydration like bill text. |
| `sponsor_person_id` | — | — | ← | **lossy** — see Lossy directions §3 | LegiScan amendments do not carry sponsor IDs in the typed schemas reviewed. |
| `sponsor_organization_id` | — | — | ← | **lossy** | Same. |
| `status` | `adopted` (0/1) | both | ← | `adopted == 1` → `adopted`; else `offered` (no finer distinction) | **Lossy ←** — our 6-value vocab (`offered`/`adopted`/`rejected`/`withdrawn`/`pending`/`tabled`) collapses to LegiScan's 2-value flag. WSL SOAP carries finer detail. |
| `offered_at` | `date` (interpreted as offer date) | both | ← | parse date | LegiScan's single `date` field is ambiguous about which lifecycle event it marks. |
| `adopted_at` | `date` if `adopted == 1` | both | ← | conditional | Same ambiguity. |
| `rejected_at` / `withdrawn_at` | — | — | ← | **lossy** — null in LegiScan adapter rows | |
| — | `chamber` + `chamber_id` | both | ← | could populate a denormalized chamber column on `Amendment` in v1 | Useful for "which chamber proposed this amendment" queries. |
| — | `description` | both | ← | store as additional metadata (subsumed under our `amendment_text` for now) | |

### Vote cluster ↔ `BillVote` ref + `RollCall` (`getRollCall(roll_call_id)`)

`getBill.votes[]` returns `BillVote` ref objects with aggregate yea/nay/nv/absent counts and a `roll_call_id`. Full per-legislator detail requires a follow-up `getRollCall(roll_call_id)` call.

#### `VoteEvent` ↔ `RollCall`

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `source_id` | `roll_call_id` | `getRollCall` | ↔ | stringify | |
| `subject_type` | always `bill` for LegiScan | — | ← | literal | **Lossy ←** — LegiScan's roll calls always tie to a `bill_id`. Amendment votes are not exposed as separate roll calls under any of the typed clients reviewed. See Lossy directions §1. |
| `subject_id` | `bill_id` resolved | `getRollCall` | ← | resolve to local | |
| `bill_id` (denorm) | `bill_id` | `getRollCall` | ↔ | resolve | |
| `amendment_id` | — | — | ← | always null from LegiScan-sourced events | See Lossy directions §1. |
| `motion_description` | `desc` | `getRollCall` | ← | passthrough | E.g., "Third Reading: Final Passage". |
| `context_type` | inferred from `chamber_id` (floor) or absent committee context | `getRollCall` | ← | always `floor` from LegiScan | **Lossy ←** — LegiScan does not expose committee-vote roll calls in the typed schemas; only floor votes (final passage, concurrence, override). Committee votes via SOAP only. |
| `context_organization_id` | `chamber_id` resolved | `getRollCall` | ← | resolve chamber Org | |
| `chamber` (denorm) | `chamber` (`"H"`/`"S"`) | `getRollCall` | ← | normalize | |
| `event_at` | `date` | `getRollCall` | ← | parse date (date-precision; no time) | |
| `outcome` | `passed` (0/1) | `getRollCall` | ← | `passed == 1` → `passed`; else `failed` | **Lossy ←** — our `tabled` / `withdrawn` / `inconclusive` outcomes collapse to `failed` when LegiScan-sourced. |

#### `VoteCount` ↔ `RollCall` aggregate fields

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `vote_event_id` | parent | ← | | | |
| `count_type` + `value` | one row per: `yea`, `nay`, `nv`, `absent` | `getRollCall` | ← | emit 4 rows | `nv` → `present_not_voting`; `absent` → `absent`. Our `excused` count has no LegiScan equivalent (folded into `absent`). **Lossy ←**. LegiScan also emits `total` which we don't need as a stored count (sum on read). |

#### `PersonVote` ↔ `IndividualVote` (`RollCall.votes[]`)

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `vote_event_id` | parent | ← | | | |
| `person_id` | `people_id` | `RollCall.votes[]` | ← | resolve to local Person | |
| `vote` | `vote_id` (int) + `vote_text` (string) | `RollCall.votes[]` | ↔ | 4-value table: `1` Yea → `yea`, `2` Nay → `nay`, `3` NotVoting → `present_not_voting`, `4` Absent → `absent` | **Lossy ←** — our `abstain` / `excused` have no LegiScan equivalent. LegiScan collapses to coarser 4-value vocab (vs. OCD's 7-value). |

### Identity cluster ↔ `Person` + sponsor metadata

LegiScan has no first-class `Organization`, `Role`, or `Assignment`. Identity is captured in two shapes: `Person` (returned by `getPerson` and within `getSessionPeople`) and inline `Sponsor` records embedded in `getBill.sponsors[]`. The `Sponsor` shape is a `Person` plus 4 sponsorship-specific fields (`sponsor_type_id`, `sponsor_order`, `committee_sponsor`, `committee_id`).

#### `Person` ↔ LegiScan `Person`

| Our field | LegiScan field | Endpoint | Direction | Transform | Notes |
|---|---|---|---|---|---|
| `source_id` | `people_id` | `getPerson` | ↔ | stringify | LegiScan's stable people-graph ID. |
| `name_full` | `name` | `getPerson` | ↔ | passthrough | |
| `name_first` | `first_name` | `getPerson` | ↔ | | |
| `name_last` | `last_name` | `getPerson` | ↔ | | |
| `name_middle` | `middle_name` | `getPerson` | ↔ | | |
| `name_suffix` | `suffix` | `getPerson` | ↔ | | |
| `name_used` | `nickname` | `getPerson` | ↔ | when non-empty | Treat LegiScan's `nickname` as our preferred-display name. |
| `gender` | — | — | ← | always null from LegiScan | **Lossy ←**. |
| `birth_year` | — | — | ← | always null | **Lossy ←**. |
| `current_district` (denorm on Person) | `district` | `getPerson` | ↔ | passthrough | LegiScan keeps current district inline on Person. |
| `powermap_person_id` | — | — | n/a | local-canonical FK, set post-match | |
| — | `person_hash` | `getPerson` / `Sponsor` | ← | drop or use for change detection | |
| — | `party_id` (int) + `party` (text) | `getPerson` | ← | **no v0 home directly on Person** — derived to an Assignment to a Party Org with role `Member` | LegiScan inlines current party affiliation on Person; we model it as an Assignment. The adapter has to synthesize: Org="Washington Democratic Party", Role="Member", Assignment(Person, Role, valid_from=session.year_start). |
| — | `role_id` (int) + `role` (text) | `getPerson` | ← | derive to an Assignment to a chamber Org | LegiScan's `role_id` is a 3-value enum (1=Representative, 2=Senator, 3=Joint Conference) — our `Role.name` is "Representative" / "Senator" within the chamber Org. |
| — | `state_id` (int) + `state` (state code) | `getPerson` | ← | resolve to jurisdiction_id | |
| — | `committee_sponsor`, `committee_id` | `getPerson` | ← | drop on Person (these belong on `BillSponsorship`); LegiScan returns them on Person only as a stale convenience | |
| — | `state_federal` (0=state, 1=federal) | `getPerson` | ← | drop; always 0 for WA | |
| **external IDs** | `ftm_eid`, `votesmart_id`, `opensecrets_id`, `knowwho_pid`, `ballotpedia`, `bioguide_id` | `getPerson` | ← | **no v0 home** | **The most important finding for v1.** See Open revisions §0. |

#### `Organization` ↔ implicit

LegiScan has no formal Organization entity. Adapter synthesizes Org rows from:

| Our Organization shape | LegiScan signal | Direction | Notes |
|---|---|---|---|
| chamber (`org_type="chamber"`) | `body` / `body_id` / `chamber_id` across endpoints | ← | LegiScan implicitly identifies chambers via `body_id` (lower=1, upper=2 typically). |
| party (`org_type="party"`) | `Person.party` / `party_id` (6-value `PartyId` enum) | ← | Adapter synthesizes one Org per party encountered. |
| committee (`org_type="committee"`) | `getBill.committee{}` (current) + `getBill.referrals[]` (historical) + `Sponsor.committee_id` | ← | LegiScan provides `committee_id` but no standalone `getCommittee` endpoint exists in v1.91 (cross-checked against both typed clients). |
| subcommittee / caucus / candidate_committee / lobbying_firm / pac / government_agency / other | — | — | LegiScan does not surface these. **Lossy ←**. |
| `parent_organization_id` | committee → chamber implied by `committee.chamber_id` | ← | Single level of parenting only — no subcommittee → committee hierarchy in LegiScan. |

#### `Role` ↔ implicit

LegiScan has no Role entity. Adapter synthesizes Role rows:

| Our Role | LegiScan signal | Direction | Notes |
|---|---|---|---|
| (Senate, "Senator") / (House, "Representative") | `Person.role_id` (1/2/3) | ← | Map RoleId enum → our role.name within the chamber Org. |
| (Committee X, "Member") | implicit; no per-member committee assignments in LegiScan | ← | **Lossy ←** — LegiScan does not surface committee membership rosters. WSL SOAP does. |
| (Committee X, "Chair") / "Vice Chair" / "Ranking Member" | not surfaced | ← | **Lossy ←**. |
| (Party Y, "Member") | `Person.party_id` | ← | One role per party encountered. |
| (Chamber, "Speaker" / "Majority Leader" / etc.) | not surfaced | ← | **Lossy ←** — leadership roles are invisible to LegiScan. |

#### `Assignment` ↔ implicit (via `getSessionPeople`)

`getSessionPeople(session_id)` returns the people active in a session — adapter derives one `Assignment(Person, Role=Senator/Representative, Org=chamber, valid_from=session.year_start, valid_to=session.year_end)` per person plus one party-membership Assignment from `party_id`.

| Our Assignment field | LegiScan source | Direction | Notes |
|---|---|---|---|
| `person_id` | `people_id` | ← | |
| `role_id` | derived (RoleId + chamber, or party) | ← | |
| `valid_from` | `session.year_start` (Jan 1) | ← | **Lossy ←** — date precision. |
| `valid_to` | `session.year_end` (Dec 31) | ← | Same. |
| `is_active` | derived from session `sine_die` | ← | |
| `source_id` | synthesize: `"assignment:{people_id}:{role_slug}:{session_id}"` | ← | LegiScan does not provide stable assignment IDs. |

### Statute cluster

**No LegiScan equivalent.** LegiScan does not model statute corpora. `StatuteCode`, `StatuteTitle`, `StatuteChapter`, `StatuteSection`, `BillStatuteChange` are sourced exclusively from WSL RCW. The transformation is one-way trivial: the LegiScan adapter never writes to these tables.

The closest LegiScan signal is `Bill.subjects[]` (subject tagging) which is informationally related but structurally distinct. See Open revisions §6.

### PDC cluster

**No LegiScan equivalent.** LegiScan does not model lobbying or campaign finance. `LobbyingActivity`, `LobbyingPosition`, `Contribution` are PDC-only. (LegiScan's `Stance` field — Watch/Support/Oppose — is account-level *monitor list* state, not third-party-disclosed lobbying positions. Don't confuse the two.)

---

## Vocabulary alignment

### Bill status / step vocab

LegiScan's 13-value `BillStatus` enum (per `legiscan-mcp/src/types/legiscan.ts`, cross-checked against the Rust crate which only covers 7 of 13):

| LegiScan code | Name | Our `current_status` |
|---|---|---|
| 0 | NA | `unknown` / null |
| 1 | Introduced | `introduced` |
| 2 | Engrossed | `engrossed` |
| 3 | Enrolled | `enrolled` |
| 4 | Passed | `passed` |
| 5 | Vetoed | `vetoed` |
| 6 | Failed | `failed` |
| 7 | Override | `veto_override` |
| 8 | Chaptered | `chaptered` (enacted) |
| 9 | Refer | `referred` |
| 10 | Report Pass | `committee_reported_favorably` |
| 11 | Report DNP | `committee_reported_do_not_pass` |
| 12 | Draft | `draft` |

Our v0 says `current_status` is "source-vocabulary text; vocab alignment to be addressed per transformation." Recommendation for v1: keep source-vocab text on the column **but** add a normalized `current_status_class` column with the 13-value vocab above as the cross-source standard. WSL SOAP → LegiScan code mapping is a separate exercise (WSL has more granular status text). See Open revisions §5.

### Bill action types (progress events vs. history actions)

LegiScan exposes two parallel structures:

- **`history[]`** — full action log, free-text `action` field + `importance: 0|1`. No formal classification.
- **`progress[]`** — curated milestone log with `event` as an integer code. The event-code table is not exposed by the public typed clients I could verify, but the Rust crate documents `Progress.event: i32` and treats it opaquely. By cross-referencing the LegiScan API user manual references in the multi-state IA delta doc, the event-code set covers roughly: Introduced, Engrossed, Enrolled, Passed, Vetoed, Failed, Override, Chaptered, Refer, Report Pass, Report DNP (i.e., the same 11 codes that mirror `BillStatus`).

OCD's 40-value `BILL_ACTION_CLASSIFICATIONS` is strictly richer than LegiScan's progress vocab. Recommendation: when we adopt OCD's action vocab in v1, treat LegiScan's `progress.event` as a coarse mapping target (LegiScan → OCD is many-to-one, lossy). See Open revisions §5.

### Sponsor role vocab

LegiScan's 4-value `SponsorType` enum:

| LegiScan code | Name | Our `BillSponsorship.role` |
|---|---|---|
| 0 | Sponsor (Generic) | `generic` |
| 1 | Primary Sponsor | `primary` |
| 2 | Co-Sponsor | `co` |
| 3 | Joint Sponsor | `joint` |

**Exact alignment.** Our v0 4-value vocab was deliberately chosen to match LegiScan. No transformation required. This is the cleanest vocab agreement across the v0 ↔ LegiScan boundary.

### Vote outcome / vote choice

LegiScan's per-legislator `VoteValue` enum (`RollCall.votes[].vote_id`):

| LegiScan code | Name | Our `PersonVote.vote` |
|---|---|---|
| 1 | Yea | `yea` |
| 2 | Nay | `nay` |
| 3 | Not Voting | `present_not_voting` |
| 4 | Absent | `absent` |

Our `abstain` and `excused` have no LegiScan code — LegiScan folds them into `Not Voting` and `Absent` respectively. **Lossy ←.**

Vote outcome (RollCall-level): LegiScan has a single `passed: 0|1` flag. Our `VoteEvent.outcome` 6-value vocab (`passed` / `failed` / `tabled` / `withdrawn` / `inconclusive` / `other`) collapses to LegiScan's 2-value flag. **Lossy ←.**

### Other LegiScan vocabularies referenced

For completeness, the LegiScan enums we do *not* directly consume but a future v1 might:

- **`TextType` (14)**: Introduced, CommitteeSubstitute, Amended, Engrossed, Enrolled, Chaptered, FiscalNote, Analysis, Draft, ConferenceSubstitute, Prefiled, VetoMessage, VetoResponse, Substitute. Richer than our v0 `BillVersion.version_type` examples; see Open revisions §4.
- **`SupplementType` (8)**: FiscalNote, Analysis, FiscalNoteAnalysis, VoteImage, LocalMandate, CorrectionsImpact, Miscellaneous, VetoLetter. No v0 home.
- **`SASTType` (9)**: SameAs, SimilarTo, ReplacedBy, Replaces, CrossFiled, EnablingFor, EnabledBy, Related, CarryOver. No v0 home — `BillRelationship` deferred in P0 Tier 2. Open revisions §7.
- **`EventType` (3)**: Hearing, ExecutiveSession, MarkupSession. No v0 home — see Open revisions §8.
- **`BillType` (23)**: Bill, Resolution, ConcurrentResolution, JointResolution, JointResolutionConstitutionalAmendment, ExecutiveOrder, ConstitutionalAmendment, Memorial, Claim, Commendation, CommitteeStudyRequest, JointMemorial, Proclamation, StudyRequest, Address, ConcurrentMemorial, Initiative, Petition, StudyBill, InitiativePetition, RepealBill, Remonstration, CommitteeBill. Maps directly onto our v0 `Bill.bill_type` column (currently free-text WA-specific values). Adoption of LegiScan's 23-value vocab as a normalized side column is a clean v1 addition.
- **`RoleId` (3)**: Representative (1), Senator (2), JointConference (3). Our v0 already covers chamber via Org graph + Role names — no change needed.
- **`PartyId` (6)**: Democrat, Republican, Independent, GreenParty, Libertarian, Nonpartisan. Adapter synthesizes Org+Assignment rows from this.
- **`MimeType` (6)**: HTML, PDF, WordPerfect, MSWord, RichTextFormat, MSWord2007. Used when hydrating text/amendment/supplement blobs.
- **`Stance` (3)**: Watch (0), Support (1), Oppose (2). LegiScan account-level monitor state. **Do not confuse** with our PDC `LobbyingPosition` (support/oppose/neutral). Different domains, similar-sounding vocab.

---

## Lossy directions

Numbered for cross-reference from Open revisions.

1. **VoteEvent subject is always `bill` from LegiScan.** Our v0 polymorphism (`subject_type ∈ {bill, amendment, motion}`) is not exercised by LegiScan-sourced events. Amendment votes and procedural motions are not exposed as separate roll calls in the typed clients verified. **Implication:** WSL SOAP remains the authoritative source for amendment and motion votes. LegiScan can only corroborate final-passage / concurrence / override votes.

2. **VoteEvent context is always `floor` from LegiScan.** Committee votes are not exposed via `getRollCall`. Committee work appears in `getBill.history[]` as action text but without vote tallies. **Implication:** ditto WSL SOAP authoritative for committee votes.

3. **Amendment sponsorship and lifecycle granularity.** LegiScan's `Amendment` has `adopted: 0|1` and a single `date` field. We lose: who sponsored the amendment (no `sponsor_person_id` / `sponsor_organization_id`), the offered/adopted/rejected/withdrawn/pending/tabled distinction (collapses to adopted-or-not), and timestamps of each lifecycle event (single `date` field, semantics ambiguous).

4. **Person identity richness.** LegiScan-sourced Person records have no `gender`, no `birth_year`, no `name_used` distinct from `nickname`. Worse for v1: LegiScan exposes a **broad external-ID surface** (`ftm_eid` FollowTheMoney, `votesmart_id`, `opensecrets_id`, `knowwho_pid`, `ballotpedia`, `bioguide_id`) that v0 has nowhere to store. **This is the single most impactful lossy direction** because it forecloses on cross-system identity resolution — the very thing power-map is designed for. See Open revisions §0.

5. **VoteCount granularity.** LegiScan emits 4 counts (yea/nay/nv/absent). Our v0 has 6 (`yea`/`nay`/`excused`/`absent`/`present_not_voting`/`other`). LegiScan-sourced rows always have `excused=0` and `other=0`; the `nv` count maps to our `present_not_voting`.

6. **Session date precision.** LegiScan provides `year_start` / `year_end` only. Our `LegislativeSession.start_date` / `end_date` are date-precision; LegiScan-sourced rows lose the convene/adjourn day-of-year. WSL SOAP carries the precise dates.

7. **No Role/Assignment per-row source IDs.** LegiScan exposes membership only implicitly via `getSessionPeople` and `getBill.sponsors[].role_id`. Adapter must synthesize Assignment `source_id` values; round-tripping back to LegiScan is impossible.

8. **Action timestamps are date-precision.** LegiScan's `history[].date` is a date with no time component. Same-day actions cannot be ordered by `action_at` alone — needs the v1 `BillAction.order: int` field recommended in P0.

9. **Statute and lobbying domains have no LegiScan signal.** Statute cluster (RCW) and PDC cluster (lobbying, contributions) are zero-information from LegiScan; no transformation possible in either direction.

10. **Action multi-classification is not surfaced.** LegiScan's `history[].action` is free text; even the implicit "is this major?" `importance` flag is binary. Our v1 ambition to adopt OCD's 40-value classification + array-of-classifications-per-action will be sourced exclusively from WSL SOAP / OpenStates, not LegiScan.

11. **Amendment text mimetype variants.** LegiScan returns a single `mime`+`mime_id` per amendment. If WA Legislature publishes both PDF and HTML variants of the same amendment text, LegiScan picks one. WSL SOAP exposes both.

---

## Indirect-provider adapter notes

If usa-wa wanted to ingest WA data from LegiScan as a corroboration or SOAP-fallback source:

**What's mechanically possible (within v0 shape):**

- Full `Bill` ingestion including sponsorships, history, version metadata, amendment metadata, final-passage roll calls (with per-legislator detail), subject tags (dropped, until v1), SAST relations (dropped, until v1), calendar (dropped, until v1).
- `LegislativeSession` ingestion (degraded date precision).
- `Person` baseline (name + district), with external IDs dropped pre-v1.
- `Assignment` for chamber + party (degraded date precision).

**What gets sacrificed vs. WSL SOAP as primary:**

- Committee membership rosters (LegiScan does not expose them).
- Committee votes (LegiScan exposes floor votes only).
- Amendment sponsorship + finer-grained amendment lifecycle status.
- Action timestamps (date-precision only).
- Leadership roles (Speaker, Majority Leader, etc.).
- Person demographic fields (gender, birth_year).

**Operational envelope:**

- Free-tier quota: **30,000 queries / month**. Per-bill ingestion costs at minimum: 1 × `getBill` + N × `getRollCall` per significant vote + (optional) 1 × `getBillText` per current-text refresh + (optional) 1 × `getAmendment` per amendment. A budget of ~5 calls per bill is realistic. **Implication:** at 5 calls × 5,000 bills/biennium, full WA coverage approaches the monthly quota cap. For corroboration-only use (sample-mode, change-hash-gated polling) the quota is comfortable. For full-primary ingestion, paid tier required.
- Caching cadences from the Rust crate's per-endpoint comments: `getBill` 3 hours, `getMasterList` 1 hour, `getSessionList` daily, `getPerson` / `getSessionPeople` / `getSponsoredList` weekly, `getRollCall` / `getBillText` / `getAmendment` static (immutable once published). Use `change_hash` (`Bill`) and `session_hash` (`Session`) for cheap incremental polling — neither costs a per-bill API call beyond the master-list refresh.
- Bulk-export route: `getDatasetList` → `getDataset(session_id, access_key)` returns a base64 ZIP of the entire session in one call (mime_type and zip fields). For initial backfill this is dramatically cheaper than per-bill iteration. Adapter strategy: use bulk for cold start, change-hash for incremental.
- LegiScan does not expose a free `getCommittee` endpoint in v1.91 (verified against both typed clients reviewed). Committee data must be derived from inline `getBill.committee{}` and `getBill.referrals[]`.

**Recommendation:** treat LegiScan as a **secondary, corroboration-only source** for WA. WSL SOAP remains primary. Use LegiScan's normalized `BillStatus` vocab and `change_hash` for cheap freshness signals; defer to LegiScan only when SOAP is rate-limited / unavailable.

---

## Open revisions for hybrid IA v1

Numbered for stable reference from future docs. Each item is a concrete shape change to v0.

### 0. Person external-ID columns — the headline question

**Critical question for the identity cluster.** Should `Person` carry a fixed set of external-ID columns (`ftm_eid`, `votesmart_id`, `opensecrets_id`, `knowwho_pid`, `ballotpedia`, `bioguide_id`, `legiscan_people_id`) directly, or stay with the single `powermap_person_id` FK and assume power-map federates the rest?

Two designs:

**Design A — direct columns on `canonical.persons`:**

```
+ legiscan_people_id   int nullable
+ ftm_eid              int nullable          # FollowTheMoney
+ votesmart_id         int nullable
+ opensecrets_id       text(32) nullable
+ knowwho_pid          int nullable
+ ballotpedia          text(128) nullable    # slug, not int
+ bioguide_id          text(16) nullable     # federal-only but cheap
```

Plus indexed UNIQUE constraints per ID.

Pros: trivial joins; transformation completeness immediate; matches LegiScan's wire shape with zero adapter logic; matches OCD's `PersonIdentifier` pattern as denormalized columns; matches federal `bioguide_id` convention.

Cons: schema churn when new ID systems appear; doesn't scale to unforeseen ID systems; partially duplicates what power-map will eventually carry.

**Design B — 1:N `canonical.person_identifiers` child table:**

```
canonical.person_identifiers
  person_id   ULID FK
  scheme      text(32)    # 'legiscan', 'ftm', 'votesmart', 'opensecrets', 'knowwho', 'ballotpedia', 'bioguide'
  value       text(128)
  UNIQUE (person_id, scheme), UNIQUE (jurisdiction_id, scheme, value)
```

Pros: matches OCD's `PersonIdentifier` shape exactly; arbitrary ID systems addable without schema migration; the right long-term shape.

Cons: every Person query needs a join or aggregation; more boilerplate.

**Recommendation: Design B.** It's the OCD-canonical shape, scales without schema churn, and aligns with the producer/archival framing — when we push to power-map, a 1:N child table is what power-map expects on its side too. The cost (a join per lookup) is tolerable and well-indexed.

**Action for v1:** add `canonical.person_identifiers` per Design B. Drop the temptation to add a `legiscan_people_id` column directly to `canonical.persons`.

### 1. Bill originating vs. current chamber

v0 has a single `Bill.chamber` column. LegiScan exposes both `body` (originating) and `current_body` (current). OpenStates models this via `BillAction` entries on different Organization FKs. WSL SOAP also distinguishes the two.

**Action for v1:** rename `Bill.chamber` → `Bill.originating_chamber`; add `Bill.current_chamber: text(16) nullable`. Backfill from WSL SOAP. The LegiScan adapter populates both naturally.

### 2. BillAction `is_major` flag and explicit ordering

LegiScan's `history[].importance: 0|1` is a precomputed major-milestone filter. unitedstates/congress has a similar concept. OCD's `order: PositiveIntegerField` solves the same-day-multiple-actions ordering problem.

**Action for v1:** add `BillAction.is_major: bool default false` and `BillAction.order: int nullable`. The LegiScan adapter sets `is_major` from `importance`; WSL SOAP keeps `is_major=false` until we adopt a heuristic.

### 3. LegislativeSession prefile / sine_die / archived flags

v0 has only `is_active`. LegiScan exposes three richer session flags: `prefile`, `sine_die`, `prior`.

**Action for v1:** consider adding `LegislativeSession.prefile_at: date nullable` (start of pre-filing window) and `LegislativeSession.sine_die_at: date nullable` (date of permanent adjournment). `is_active` stays but is derived. `prior` collapses cleanly into `is_active=false`.

### 4. BillVersion vocab expansion

v0 lists "original / substitute / engrossed / first_engrossed / enrolled / etc." as examples. LegiScan ships a 14-value `TextType` enum. OCD ships a 7-value `BILL_VERSION_CLASSIFICATIONS`. Both supersets are richer than our v0.

**Action for v1:** adopt LegiScan's 14-value vocab as the normalized values for `BillVersion.version_type`. Keep the column free-text but document the allowed values. (Alternative: adopt OCD's 7-value vocab as a subset and treat LegiScan's extra 7 values as adapter-mapped to the closest OCD value. Decision: pick one in v1 planning.)

### 5. Bill status normalized side column

v0 says `current_status` is "source-vocabulary text; vocab alignment to be addressed per transformation." LegiScan's 13-value `BillStatus` is a credible cross-source normalized vocab. OCD computes status from action classifications rather than carrying a status column.

**Action for v1:** keep `current_status: text` (source-vocab) and add `current_status_class: text(32) nullable` constrained to the 13-value LegiScan vocab. Also add `current_status_at: timestamptz nullable` (from `status_date`).

### 6. BillSubject (subject tagging)

v0 has no subject-tagging entity. OCD has `Bill.subject[]: ArrayField`, LegiScan has `Bill.subjects[]: {subject_id, subject_name}[]`, unitedstates has `subjects[]: string[]` + `subjects_top_term`. Three-source agreement.

**Action for v1:** add `canonical.bill_subjects (bill_id, subject_label, source)`, 1:N from Bill. Cheap. Source = adapter slug; subject_label = LegiScan's `subject_name` text. Optionally add a `subject_normalized` column if a cross-source taxonomy ever materializes.

### 7. BillRelationship (SAST / companion / replaces)

v0 punts on inter-bill relationships. OCD has `RelatedBill` with 5 relation types, LegiScan has `sasts[]` with 9, unitedstates has `related_bills[]`. The P0 multi-state IA delta recommended this as Tier 1 (#3). v0 deferred it — transformation specs reinforce the case.

**Action for v1:** add `canonical.bill_relationships (bill_id, related_bill_id, relation_type)` with `relation_type` constrained to: `same_as`, `similar_to`, `replaced_by`, `replaces`, `cross_filed`, `enabling_for`, `enabled_by`, `related`, `carry_over`, `companion`, `prior_session` (union of LegiScan + OCD).

### 8. Hearing / calendar / event modeling

v0 has no hearing entity (deferred from P0 skeleton). LegiScan exposes `Bill.calendar[]: {type_id, type, date, time, location, description}[]` with 3 event types. OCD has a full Event graph.

**Action for v1:** add a minimal `canonical.bill_events (bill_id, event_type, event_at, location, description, acting_organization_id)` with `event_type` constrained to `hearing` / `executive_session` / `markup_session` / `public_hearing` / `work_session`. Defer the OCD-style Event-with-AgendaItem-and-RelatedEntity graph to post-MVP.

### 9. Supplement / fiscal note attachments

LegiScan's `Supplement` covers fiscal notes, analyses, vote images, local mandate notes, corrections impact, etc. v0 has no home for these. They are not BillVersions (not text of the bill) and not Amendments (not proposed changes).

**Action for v1:** consider `canonical.bill_supplements (bill_id, supplement_type, label, url, mime, source_id)` — but only if WSL SOAP also exposes these (verify in step 3 transformation specs). If WSL exposes them, they're worth modeling; if only LegiScan exposes them and the data is sparse for WA, defer.

### 10. Person external-ID indexing notes (corollary to §0)

If Design B is adopted (recommended), index `(jurisdiction_id, scheme, value)` for cross-source-ID lookup. Heaviest read pattern will be "find Person where legiscan.people_id = 12345" during the LegiScan adapter's resolution step. Without a partial index per scheme, this becomes a sequential scan on a wide-and-narrow table.

---

## References

- Hybrid IA v0: [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)
- Multi-state IA delta (LegiScan section, source artifact identification, vocabulary lists): [`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`](../research/2026-05-26-multi-state-legislative-ia-delta.md) — specifically §"Source 2 — LegiScan" and the cross-source agreement table.
- LegiScan typed client (Rust, strongly typed structs): `github.com/populist-vote/legiscan`, files `src/api/get_bill.rs`, `get_roll_call.rs`, `get_person.rs`, `get_session.rs`, `get_amendment.rs`, `get_bill_text.rs`, `get_master_list.rs`, `get_dataset.rs`, `search.rs`.
- LegiScan typed client (TypeScript, MCP server tracking LegiScan v1.91): `github.com/sh-patterson/legiscan-mcp`, file `src/types/legiscan.ts` lines 1–137 (all static-value enums), 144–541 (all data structures), and README §"API Limits" for quota documentation.
- LegiScan API user manual PDF: `legiscan.com/misc/LegiScan_API_User_Manual.pdf` — **returned HTTP 403 from this environment.** Vocabulary tables here are verified by cross-checking two independent typed clients; both agree on the enum sets reproduced above.
- OpenStates / OCD vocabularies (for vocab comparison context): `openstates/data/common.py`, summarized in the multi-state IA delta §"Vocabularies — OpenStates as candidate standard".
- Power-map producer/archival framing: [`../research/2026-05-26-power-map-integration-contract.md`](../research/2026-05-26-power-map-integration-contract.md).
- P0.5 implementation plan: `docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`.
