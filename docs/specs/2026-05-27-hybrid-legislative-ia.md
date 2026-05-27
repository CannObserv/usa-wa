# Hybrid legislative information architecture (v0)

- **Date:** 2026-05-27
- **Status:** v0 draft (transformations in step 2 will surface revisions → v1)
- **Scope:** All canonical legislative-domain entities. Supersedes the P0-skeleton entity descriptions in [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md) §Canonical data spine.
- **Tracks:** [GH #3](https://github.com/CannObserv/usa-wa/issues/3); see plan at [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md).

## Problem

The P0 entity skeleton in `clearinghouse-domain-legislative` was sized for "one WA bill end-to-end" and did not survive contact with the multi-state IA delta ([`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`](../research/2026-05-26-multi-state-legislative-ia-delta.md)). Three structural gaps and one naming inversion needed addressing before any P1a normalization code lands:

1. `Bill.biennium` as a text column leaks across special sessions.
2. No vote cluster at all — but votes are universal in OpenStates / LegiScan / uscongress, and WSL SOAP returns roll calls.
3. `BillSponsorship` is two-valued (prime/co) and assumes a non-null `legislator_id`, which can't represent committee-sponsored bills (federal Congress, some states).
4. `Bill.title` / `Bill.short_description` are inverted relative to industry convention.

A larger restructuring was decided during P0.5 planning: adopt power-map's identity vocabulary (Person / Organization / Role / Assignment) for all of `Legislator`, `Committee`, and `Filer`. This aligns usa-wa's identity model with the cohort's canonical-identity service and positions usa-wa as a producer of identity data archived in power-map. See the producer/archival framing below and [project_identity_producer_archival](../../../.claude/projects/-home-exedev-usa-wa/memory/project_identity_producer_archival.md).

## Producer/archival framing

usa-wa is **both** a query layer over primary WA sources **and** a producer of identity data. As the WA Legislature adapter ingests SOAP responses, it produces canonical Person / Organization / Role / Assignment records in usa-wa's local Postgres. The long-term direction is to push those records to [power-map](https://github.com/CannObserv/power-map) as the cohort's identity system of record. usa-wa keeps a local copy for query latency; the canonical truth lives upstream once the power-map write API ([CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156) §9) is available.

Consequences:

- **The canonical identity model uses power-map's vocabulary** so cross-service joins work without translation: Person, Organization, Role, Assignment.
- **Each Person and Organization carries a nullable `powermap_*_id` column** that's populated when power-map confirms a mapping. Until then, usa-wa's own ULID is the local truth.
- **A future archival-push job** stages the local-canonical → power-map writes when the upstream write API matures (P3+). The records' shape is designed today to make that push mechanical (no schema impedance mismatch).
- **State-resource resilience.** When WSL SOAP rate-limits, breaks compatibility, or rotates IDs, the local cache and the archival truth in power-map keep MCP/REST queries serving. This is a free side effect of the architecture; it's not what motivated the adoption, but it's worth preserving.

## Universal entity shape

Every canonical entity in this spec carries the following columns. Per-entity blocks below only call out domain-specific additions.

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | Always. Auto-generated via `clearinghouse_core.db.ulid.ULID`. |
| `jurisdiction_id` | text(32) NOT NULL, indexed | Slug per `feedback_jurisdiction_naming` — `usa-wa`, `usa-or`, `usa-fed`. |
| `source` | text(64) NOT NULL | Matches the producing adapter's `source_slug` (`usa_wa_legislature`, `usa_wa_pdc`, `usa_wa_rcw`). |
| `source_id` | text(128) NOT NULL | Source-stable identifier within the adapter. |
| `primary_source_id` | ULID nullable | Denormalized FK to `clearinghouse_core.sources.id` for cheap citation rendering. |
| `last_fetched_at` | timestamptz nullable | Last successful normalization fetch. |
| `last_fetch_event_id` | ULID nullable | FK to `clearinghouse_core.fetch_events.id`. |
| `created_at` | timestamptz NOT NULL, server_default=now() | Via `TimestampMixin`. |
| `updated_at` | timestamptz NOT NULL, server_default=now(), onupdate=now() | Via `TimestampMixin`. |

**Natural-key UNIQUE constraint:** `UNIQUE (jurisdiction_id, source, source_id)` on every entity unless a per-entity block specifies otherwise (some derived entities — Role, Assignment — use a synthesized natural key).

All FKs use the `ULID` SQLAlchemy column type. Schema is `canonical.*` for every table in this spec.

## Identity cluster (power-map terminology)

The four-entity identity model replaces the P0-skeleton's `Legislator`, `Committee`, and `Filer` standalone tables. Every legislator is a `Person`; every chamber, party, committee, candidate committee, lobbying firm, and PAC is an `Organization`. The relationships between them are `Assignment`s of `Role`s.

### `canonical.persons`

A human. Replaces `Legislator`. Local cache of identity data that will eventually be archived in power-map.

| Column | Type | Notes |
|---|---|---|
| `name_full` | text NOT NULL | Most-canonical full name available at ingest time. |
| `name_first` | text nullable | |
| `name_last` | text nullable | |
| `name_middle` | text nullable | |
| `name_suffix` | text nullable | "Jr.", "III", etc. |
| `name_used` | text nullable | Preferred display when different from legal name. |
| `gender` | text(32) nullable | Source's free-text value; we don't enforce a vocabulary. |
| `birth_year` | int nullable | Privacy-friendly granularity; full DOB not stored. |
| `powermap_person_id` | ULID nullable | Set after a power-map match; null pre-P2 or pre-match. |

Power-map has a sophisticated name i18n stack (BCP-47 + ISO 15924 + deadname downgrade + structured-parts sidecar). usa-wa's local cache stays simple; consumers needing i18n querying go to power-map directly via `powermap_person_id`.

### `canonical.organizations`

Any non-person legal/political entity. Discriminated by `org_type`.

| Column | Type | Notes |
|---|---|---|
| `name` | text NOT NULL | Canonical full name. |
| `short_name` | text nullable | "Senate" for "Washington State Senate". |
| `org_type` | text(32) NOT NULL | One of: `chamber` / `party` / `committee` / `subcommittee` / `caucus` / `candidate_committee` / `lobbying_firm` / `pac` / `government_agency` / `other`. |
| `parent_organization_id` | ULID nullable FK self | A committee's parent is its chamber; a subcommittee's parent is its committee. |
| `powermap_organization_id` | ULID nullable | Set after a power-map match. |

WA-specific examples:
- `(name="Washington State Senate", org_type="chamber")` — top-level, no parent
- `(name="Washington Democratic Party", org_type="party")` — top-level
- `(name="Senate Committee on Health & Long-Term Care", org_type="committee", parent_organization_id=senate.id)`
- `(name="Friends of Jane Doe", org_type="candidate_committee")`
- `(name="Acme Government Affairs LLC", org_type="lobbying_firm")`

### `canonical.roles`

A named slot **within** an Organization. Roles are templates; the time-bounded "who holds the role" is an Assignment.

| Column | Type | Notes |
|---|---|---|
| `organization_id` | ULID NOT NULL FK | The org this role exists within. |
| `name` | text(64) NOT NULL | "Senator", "Representative", "Chair", "Vice Chair", "Ranking Member", "Member", "Speaker", "Majority Leader", "Minority Leader", "President Pro Tempore", "Member" (of a party), etc. |
| `role_type` | text(32) NOT NULL | One of: `elected_member` / `leadership` / `committee_member` / `committee_leadership` / `staff` / `party_member` / `other`. |

**Natural-key UNIQUE:** `(jurisdiction_id, organization_id, name)`. Roles are jurisdiction-internal vocabulary, not source-emitted entities — `source` and `source_id` may carry adapter-synthesized values (e.g., `source="usa_wa_legislature", source_id="role:senate:senator"`) for upsert idempotency.

Examples:
- `(org=Senate, name="Senator", role_type="elected_member")`
- `(org=Senate, name="President Pro Tempore", role_type="leadership")`
- `(org=Senate Health Committee, name="Chair", role_type="committee_leadership")`
- `(org=Senate Health Committee, name="Member", role_type="committee_member")`
- `(org=WA Democratic Party, name="Member", role_type="party_member")`

### `canonical.assignments`

A Person × Role × Period — "Sen. Jane Doe was Chair of Senate Health from 2025-01-13 to 2026-04-15".

| Column | Type | Notes |
|---|---|---|
| `person_id` | ULID NOT NULL FK | |
| `role_id` | ULID NOT NULL FK | |
| `valid_from` | date NOT NULL | |
| `valid_to` | date nullable | Null = currently active. |
| `is_active` | bool NOT NULL default false | Denormalized from valid_to for query speed. Maintained by the adapter on each refresh. |

**Natural-key UNIQUE:** `(jurisdiction_id, person_id, role_id, valid_from)`. A Person can re-take a Role with a new `valid_from` if there's a gap. `source_id` may be adapter-synthesized like `assignment:26142:role:senate-health:chair:2025-01-13` when the source doesn't expose a stable assignment id.

This is the bridge between legislators-as-people and their chamber / party / committee context. WSL SOAP-derived examples for one biennium of Sen. Jane Doe (LD 21, Democrat, Chair of Senate Health):

| Person | Role | Org | valid_from | valid_to |
|---|---|---|---|---|
| Jane Doe | Senator | WA Senate | 2023-01-09 | 2027-01-12 |
| Jane Doe | Member | WA Democratic Party | 2023-01-09 | null |
| Jane Doe | Chair | Senate Health Committee | 2025-01-13 | null |
| Jane Doe | Member | Senate Ways and Means Committee | 2025-01-13 | null |

District is captured via Assignment-to-Role within a district-specific "Senate LD 21" sub-Organization? Or as a Role attribute? **Open question for v1** — see Open issues. For v0, district lives as a denormalized column on Person until transformations show whether OCD/LegiScan represent it differently.

| Column added to `canonical.persons` for v0 | Type | Notes |
|---|---|---|
| `current_district` | text(32) nullable | LD number for state legislators, district for federal. Denormalized for query convenience pending v1 decision. |

## `canonical.legislative_sessions`

A bounded period during which a legislature meets and acts on bills. Replaces `Bill.biennium`.

| Column | Type | Notes |
|---|---|---|
| `slug` | text(64) NOT NULL | OpenStates-style: `<jurisdiction_id>-<year>[-<session_suffix>]`. Examples: `usa-wa-2025`, `usa-wa-2025-special-1`, `usa-fed-119`. |
| `name` | text NOT NULL | Human-readable: "2025 Regular Session", "2025 First Special Session". |
| `classification` | text(32) NOT NULL | One of: `regular` / `special` / `sine_die` / `extraordinary` / `other`. |
| `start_date` | date nullable | |
| `end_date` | date nullable | |
| `is_active` | bool NOT NULL default false | Denormalized from dates; adapter maintains. |
| `biennium_label` | text(16) nullable | WA-flavored — preserved for round-tripping ("2025-26"). For other jurisdictions this may be null. |

**Natural-key UNIQUE:** `(jurisdiction_id, slug)`. The slug doubles as a stable cross-source mapping target (transformation specs map OpenStates' `wa-2025` ↔ our `usa-wa-2025`).

## Bill cluster

### `canonical.bills`

| Column | Type | Notes |
|---|---|---|
| `legislative_session_id` | ULID NOT NULL FK | Replaces `biennium` text. |
| `chamber` | text(16) NOT NULL | `house` / `senate` / `unicameral`. |
| `number` | int NOT NULL | The numeric portion. |
| `bill_type` | text(32) nullable | HB / SB / HJR / SJR / HCR / SCR / HJM / SJM / etc. |
| `title` | text NOT NULL | The short form — convention-aligned with OCD / LegiScan / uscongress. |
| `short_description` | text nullable | The long form (full descriptive title from the source). |
| `current_status` | text(128) nullable | Source-vocabulary text; vocab alignment to be addressed per transformation. |
| `current_step` | text(128) nullable | E.g., `senate_rules`, `house_floor`, `governor_desk`. |
| `introduced_at` | timestamptz nullable | |
| `current_text` | text nullable | Current bill text; full version history is `BillVersion`. |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`. WSL's source_id is conventionally `<bill_type>-<number>-<biennium>` (e.g., `HB-1234-2025-26`); v1 may revise once OCD/LegiScan transformations clarify normalization.

### `canonical.bill_sponsorships`

Polymorphic: a sponsor is either a Person (legislator) **or** an Organization (committee, when a jurisdiction allows committee-sponsored bills). WA does not allow committee sponsorship (OQ1 resolved 2026-05-27) so the WA adapter never emits `organization_id`-bearing rows, but Layer 2 supports it for federal / multi-state reusability.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `person_id` | ULID nullable FK | Exactly one of person_id / organization_id is non-null. |
| `organization_id` | ULID nullable FK | |
| `role` | text(32) NOT NULL | One of: `primary` / `co` / `joint` / `generic` (4-value vocab; OCD-aligned). |
| `sponsor_order` | int nullable | 1-indexed; preserves source's ordering. |
| `withdrawn_at` | timestamptz nullable | For co-sponsor withdrawals. |

**CHECK constraint:** `(person_id IS NOT NULL AND organization_id IS NULL) OR (person_id IS NULL AND organization_id IS NOT NULL)`.

**Natural-key UNIQUE:** `(bill_id, person_id, role)` for person-sponsored; `(bill_id, organization_id, role)` for committee-sponsored — implemented as two partial indexes.

### `canonical.bill_actions`

Append-only lifecycle log. Mostly unchanged from P0.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `action_at` | timestamptz NOT NULL | |
| `chamber` | text(16) nullable | `house` / `senate` / null for executive / governor actions. |
| `acting_organization_id` | ULID nullable FK | The body that took the action — chamber, committee, or null. |
| `action_type` | text(64) NOT NULL | Source-vocab text; v1 transformations will normalize via OCD's `BILL_ACTION_CLASSIFICATIONS`. |
| `description` | text NOT NULL | Free-text description as the source provided. |

**Natural-key UNIQUE:** `(bill_id, source, source_action_id)`. Where the source provides a stable action id, we use it; otherwise the adapter synthesizes one from `(action_at, action_type, brief_hash)`.

### `canonical.bill_versions`

Version metadata only in MVP. Full version text deferred to P3 (large blobs).

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `version_type` | text(64) NOT NULL | `original` / `substitute` / `engrossed` / `first_engrossed` / `enrolled` / etc. |
| `version_at` | timestamptz nullable | When the version was introduced/adopted. |
| `is_current` | bool NOT NULL default false | At most one current per bill — adapter maintains. |

### `canonical.amendments`

Proposed changes to a bill (new in v0 per OQ3). Amendments are voted on, so the Vote cluster references them.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `label` | text(64) NOT NULL | "Amendment 1", "Striking Amendment 21", etc. |
| `amendment_text` | text nullable | Full text of the amendment. |
| `sponsor_person_id` | ULID nullable FK | |
| `sponsor_organization_id` | ULID nullable FK | For committee-offered amendments. |
| `status` | text(32) NOT NULL | One of: `offered` / `adopted` / `rejected` / `withdrawn` / `pending` / `tabled`. |
| `offered_at` | timestamptz nullable | |
| `adopted_at` | timestamptz nullable | |
| `rejected_at` | timestamptz nullable | |
| `withdrawn_at` | timestamptz nullable | |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

## Vote cluster

Flexible enough for committee votes on bills, committee votes on amendments, floor votes on motions, and floor votes on amendments. Three entities:

### `canonical.vote_events`

| Column | Type | Notes |
|---|---|---|
| `subject_type` | text(16) NOT NULL | One of: `bill` / `amendment` / `motion`. |
| `subject_id` | ULID NOT NULL | Polymorphic; no DB-level FK (mirrors the Citation pattern). |
| `bill_id` | ULID nullable FK | Denormalized for query speed: set when subject is a bill, *or* when the subject is an amendment whose parent bill we know. |
| `amendment_id` | ULID nullable FK | Set when subject_type=amendment. |
| `motion_description` | text nullable | Free text when subject_type=motion; no `Motion` entity for MVP. |
| `context_type` | text(16) NOT NULL | `floor` / `committee`. |
| `context_organization_id` | ULID NOT NULL FK | The body that voted — chamber for floor votes, committee for committee votes. |
| `chamber` | text(16) nullable | Denormalized from context_organization → org_type=chamber: `house` / `senate` / `unicameral`; null for joint sessions. |
| `event_at` | timestamptz NOT NULL | |
| `outcome` | text(32) NOT NULL | One of: `passed` / `failed` / `tabled` / `withdrawn` / `inconclusive` / `other`. |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`. WSL SOAP provides roll-call IDs; for committee votes that don't have stable IDs, the adapter synthesizes via `(subject_id, context_organization_id, event_at)` hash.

### `canonical.vote_counts`

Aggregate counts per VoteEvent. One row per outcome category.

| Column | Type | Notes |
|---|---|---|
| `vote_event_id` | ULID NOT NULL FK | |
| `count_type` | text(16) NOT NULL | One of: `yea` / `nay` / `excused` / `absent` / `present_not_voting` / `other`. |
| `value` | int NOT NULL | |

**Natural-key UNIQUE:** `(vote_event_id, count_type)`. No `source_id` on this table — it's a derived aggregate.

### `canonical.person_votes`

Per-legislator detail. Materialized in P1a (OQ3 resolved 2026-05-27 — votes are a fundamental measure).

| Column | Type | Notes |
|---|---|---|
| `vote_event_id` | ULID NOT NULL FK | |
| `person_id` | ULID NOT NULL FK | |
| `vote` | text(16) NOT NULL | One of: `yea` / `nay` / `abstain` / `excused` / `absent` / `present_not_voting`. |

**Natural-key UNIQUE:** `(vote_event_id, person_id)`. No standalone `source_id` — the natural key is sufficient.

**Scale note:** ~150 legislators × ~3 final-passage votes per bill × ~5000 bills/biennium = ~2.25M rows/biennium, plus committee votes. Materially larger than other tables but well within Postgres limits and the per-bill query is indexed.

## Statute cluster (unchanged from P0)

The five statute-cluster tables from P0 remain as-designed: `StatuteCode`, `StatuteTitle`, `StatuteChapter`, `StatuteSection`, `BillStatuteChange`. See [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md) §Statute corpus cluster. The natural keys are unchanged; only the references to Bill remain valid because `Bill` itself is preserved (with revised columns).

## PDC cluster (reshaped)

PDC's notion of "Filer" disappears — what PDC tracks as filers map onto either `Person` (individual lobbyists, individual contributors) or `Organization` (lobbying firms, PACs, candidate committees) depending on filer type.

### `canonical.lobbying_activities`

One disclosure period of one lobbyist's activity. The reporting subject is either a Person (individual lobbyist) or an Organization (lobby firm).

| Column | Type | Notes |
|---|---|---|
| `person_id` | ULID nullable FK | Individual lobbyist. |
| `organization_id` | ULID nullable FK | Lobby firm. |
| `employer_organization_id` | ULID nullable FK | The org that hired the lobbyist. |
| `period_start` | date NOT NULL | |
| `period_end` | date NOT NULL | |
| `compensation` | numeric(14,2) nullable | |
| `expenses` | numeric(14,2) nullable | |

**CHECK constraint:** `person_id IS NOT NULL OR organization_id IS NOT NULL`.

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

### `canonical.lobbying_positions`

A position taken on a bill within a lobbying activity. Unchanged from P0 except for the Filer rename — now `lobbying_activity_id` is the only FK back to the activity (no separate filer column needed).

| Column | Type | Notes |
|---|---|---|
| `lobbying_activity_id` | ULID NOT NULL FK | |
| `bill_id` | ULID nullable FK | Null if the bill-reference resolver couldn't find a match. |
| `bill_reference_raw` | text(128) nullable | Raw text from PDC for debugging unresolved matches. |
| `position` | text(16) NOT NULL | `support` / `oppose` / `neutral`. |

**Natural-key UNIQUE:** `(lobbying_activity_id, bill_id)`. Null bill_id is allowed but only one null per activity.

### `canonical.contributions`

| Column | Type | Notes |
|---|---|---|
| `recipient_organization_id` | ULID NOT NULL FK | Candidate committees and PACs are Organizations. |
| `contributor_person_id` | ULID nullable FK | Individual contributor. |
| `contributor_organization_id` | ULID nullable FK | Org contributor (PAC, party, etc.). |
| `contributor_name_raw` | text(512) nullable | Raw name from PDC when the contributor isn't resolved to a Person/Org. |
| `amount` | numeric(14,2) NOT NULL | |
| `contributed_at` | timestamptz NOT NULL | |

**CHECK constraint:** at most one of `contributor_person_id` / `contributor_organization_id` is non-null. Both null is allowed (anonymous contributions; the raw name lives in `contributor_name_raw`).

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

## Provenance integration

Every entity in this spec writes Citation rows through the standard `clearinghouse_core.runner.AdapterRunner` mechanism. Polymorphic Citation references the entity by `(entity_type, entity_id)`:

- `entity_type` is the table name in snake_case: `person`, `organization`, `role`, `assignment`, `bill`, `bill_sponsorship`, `bill_action`, `amendment`, `vote_event`, `vote_count`, `person_vote`, `lobbying_activity`, `lobbying_position`, `contribution`, `statute_section`, `bill_statute_change`, `legislative_session`.
- `entity_id` is the ULID PK.
- Default confidence = source's intrinsic reliability; field-level citations attach to the specific column via `field_path`.

The denormalized `primary_source_id`, `last_fetched_at`, `last_fetch_event_id` columns on every entity (per the Universal entity shape) let MCP/REST responses render single-row citations without joining the Citation table — explicit field-level provenance only when meaningfully needed.

## Vocabulary status

This is a **v0 draft**. Several vocabularies are listed with WA-realistic values but transformations in step 2 may revise them:

- `Bill.current_status` and `Bill.current_step` — source-vocab text; OCD-normalized values land in v1.
- `BillAction.action_type` — source-vocab text; v1 introduces a normalized classification via OCD's `BILL_ACTION_CLASSIFICATIONS`.
- `VoteEvent.outcome`, `PersonVote.vote`, `VoteCount.count_type` — listed values are OCD-aligned; v1 may add edge cases.
- `Organization.org_type`, `Role.role_type` — drafted to cover WA + obvious federal cases; v1 confirms against the three transformation specs.

## Open issues for v1

Transformation specs in step 2 should evaluate and feed back on:

1. **District as a Role attribute vs. a sub-Organization vs. a denormalized column on Person.** v0 denormalizes (`Person.current_district`). OCD models it as a `MembershipRole.role` value (e.g., `"Representative for District 21"`). Decide which produces cleaner queries.
2. **`Caucus` vs. `Party` modeling.** v0 has both `caucus` and `party` as `org_type` values. Some jurisdictions blur the distinction (informal caucuses vs. official party labels). Transformations may collapse or split.
3. **Joint sessions.** A vote in a joint session has no single `chamber`. v0 allows null chamber on VoteEvent; v1 confirms whether OCD/LegiScan model joint sessions as a separate Organization.
4. **Per-jurisdiction action-type vocab normalization.** Whether to store both source-vocab and normalized OCD class on `BillAction`, or just normalized.
5. **Vote outcome edge cases.** Recommittal motions, motions to table — does `outcome: tabled` capture them adequately, or do we need finer-grained verbs?
6. **Anonymous contribution rules.** PDC permits some anonymous contributions; transformations should clarify whether `contributor_name_raw` is the right escape hatch or whether a dedicated `is_anonymous` flag is cleaner.

## Cross-references

- Multi-state IA delta (input): [`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`](../research/2026-05-26-multi-state-legislative-ia-delta.md)
- Power-map research note (input for identity adoption): [`docs/research/2026-05-26-power-map-integration-contract.md`](../research/2026-05-26-power-map-integration-contract.md)
- MVP architecture spec (parent): [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md)
- P0.5 plan: [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md)
- Upstream feature request to power-map: [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156)
- OpenStates schema reference: <https://docs.openstates.org/data/>
- Tracking issue: [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3)
