# Transformation: unitedstates/congress (federal) → usa-wa hybrid legislative IA

- **Date:** 2026-05-27 (review update 2026-05-28)
- **Status:** final (transformation #3 of 3; feeds hybrid IA v1; v1.1 landed)
- **Direction:** **uscongress → ours, only.** A future `usa-fed-api` sibling deployment would consume `unitedstates/congress` + `congress-legislators` as primary sources; we never publish back. The `our → uscongress` columns preserved below remain useful as schema-completeness diagnostics but are not adapter behavior.
- **Scope:** Field-level mapping between [`canonical.*` entities](2026-05-27-hybrid-legislative-ia.md) and the JSON/YAML wire shapes produced by [`unitedstates/congress`](https://github.com/unitedstates/congress) (bills, amendments, votes, committee meetings) plus [`unitedstates/congress-legislators`](https://github.com/unitedstates/congress-legislators) (members, committees, committee membership). Federal House + Senate, Congress 93rd → present.

## 2026-05-28 review update

- **Unidirectional** (see Direction above). All `→` / `↔` arrows in per-entity tables that imply emit-to-federal are documentation-only.
- **Bill titles are 1:N in the hybrid IA (v1.1).** Federal bills carry an explicit `titles` array with `title`, `type` (`official_title`, `short_title`, `popular_title`, `display_title`, etc.), `chamber` (which chamber introduced the title), and `as` (when in the lifecycle — `introduced` / `passed_house` / `passed_senate` / etc.). Mapping:
  - Each entry → one `canonical.bill_titles` row with `title_text` + `title_type` (map `official_title` → `official`, `short_title` → `short`, etc.) + `chamber` + `as_of_action`.
  - The current canonical title (latest `official_title` in the array, or `display_title` if explicitly flagged current) → also denormalized to `canonical.bills.title`, with `is_current=true` on its `bill_titles` row.
  - Federal bills have no concept of "amendment-changed title" the way WA does, so `bill_titles.amendment_id` stays null.
- **Person rich attributes defer to Power Map.** `congress-legislators` `legislators-current.yaml` exposes ~15 ID schemes per legislator (bioguide, lis, fec, govtrack, opensecrets, votesmart, cspan, wikipedia, ballotpedia, maplight, icpsr, thomas, house_history, etc.). All flow to `canonical.person_identifiers` (1:N, v1). Birth date (per-legislator YAML) defers to Power Map's planned `lifecycle_events` ([power-map#165](https://github.com/CannObserv/power-map/issues/165)). Photo URLs, official websites, contact addresses, etc. defer to Power Map's `locations` / `contact_methods` / `links` primitives — usa-wa carries identity essentials only.
- **Inputs:** hybrid IA v0 (this repo, `docs/specs/2026-05-27-hybrid-legislative-ia.md`); P0 IA delta (`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`).
- **Outputs:** per-entity correspondence tables; vocabulary mappings; lossy-direction inventory; numbered revision proposals for hybrid IA v1; sketch of a `usa-fed-adapter-legislature` package.
- **Non-goals:** USC statute-corpus modeling (deferred to a `usa-fed-adapter-statute` spec); GovInfo bill-text mimetype handling (P3); presidential nominations.

## Why this exists

Federal Congress is the **stress test that matters**. WA's hybrid IA was designed to be reusable across all 50 US state legislatures; the federal case adds an order-of-magnitude richer entity space — explicit amendment-IDs, conference reports, committee-sponsored amendments, chamber-distinct vote vocabularies, term-by-term member assignments going back to 1789, multiple competing external-ID schemes for the same person, House-vs-Senate session conventions, and "becomes law" cross-references. The premise of our `Person`/`Organization`/`Role`/`Assignment` identity model and our polymorphic `BillSponsorship`/`VoteEvent` is precisely that they handle this case without bespoke columns. If the federal mapping comes out clean, the multi-state claim is credible. If it doesn't, the IA needs structural revisions before WA code locks shapes in.

Secondary purpose: this spec is the blueprint for a future `usa-fed-api` sibling deployment that consumes `unitedstates/congress` and `unitedstates/congress-legislators` as its primary sources. The mapping table below is half-design-doc, half-adapter-spec.

## Schema-level orientation

`unitedstates/congress` produces a tree of public-domain **JSON files on disk**, one file per entity, paths like:

| Entity | Path convention |
|---|---|
| Bill | `data/<congress>/bills/<bill_type>/<bill_type><number>/data.json` (e.g., `data/119/bills/hr/hr1/data.json`) |
| Amendment | `data/<congress>/amendments/<amdt_type>/<amdt_type><number>/data.json` |
| Vote | `data/<congress>/votes/<session_year>/<chamber><number>/data.json` (e.g., `data/119/votes/2025/h142/data.json`) |
| Bill version (text) | `data/<congress>/bills/<bill_type>/<bill_type><number>/text-versions/<version_code>/data.json` (text proper deferred to `bill_text` task; this JSON holds the URL and mimetype) |

Identifier conventions: `bill_id = <bill_type><number>-<congress>` (e.g., `hr1-119`); `amendment_id = <amdt_type><number>-<congress>` (e.g., `samdt712-119`); `vote_id = <chamber><number>-<congress>.<session>` (e.g., `h142-119.2025`). All lowercase. **There is no relational schema** — joins are implicit via these IDs and are the consumer's responsibility.

`unitedstates/congress-legislators` is **YAML files in a separate repo** with downstream JSON/CSV mirrors on the `gh-pages` branch. The canonical files are `legislators-current.yaml`, `legislators-historical.yaml`, `committees-current.yaml`, `committees-historical.yaml`, `committee-membership-current.yaml`, `legislators-social-media.yaml`, `legislators-district-offices.yaml`, `executive.yaml`. **The two repos partition the data**: `congress` owns work-product (bills/amendments/votes), `congress-legislators` owns identity (members/committees). Our `Person` + `Organization` + `Assignment` cluster maps almost entirely to `congress-legislators`; our `Bill`/`Amendment`/`Vote` cluster maps to `congress`. **Cross-reference key** is the `bioguide_id` (a 7-character alphanumeric like `P000197` for Nancy Pelosi) — that is the federal-stable Person identifier and the join glue between the two repos.

Throughout this spec "uscongress" refers to the work-product repo, "congress-legislators" refers to the identity repo, and "federal source" refers to either/both.

## Per-entity correspondence

### `canonical.legislative_sessions`

Maps to the `congress` integer in uscongress JSON, plus the implicit session-year subdivision used in vote paths.

| usa-wa field | uscongress / congress-legislators path | Direction | Transform | Notes |
|---|---|---|---|---|
| `slug` | (derived) | → adapter | `f"usa-fed-{congress}"` (e.g., `usa-fed-119`) | Slug convention is our invention; uscongress has no session slug per se. |
| `name` | (derived) | → adapter | `f"{ordinal(congress)} Congress"` (e.g., "119th Congress") | |
| `classification` | (always `regular`) | → adapter | Federal Congress has no special sessions in the state sense; sessions-within-a-congress are calendar years, not classified separately. | See **Lossy** §1. |
| `start_date` | First Jan 3 of the odd-numbered year of the congress | → adapter | Computed from `congress` integer (Congress N starts Jan 3 of year `1789 + (N-1)*2`). | congress-legislators `terms[].start` provides this empirically. |
| `end_date` | Jan 3 of the next odd year | → adapter | Same. | |
| `biennium_label` | (null) | → adapter | WA-specific; null for federal. | |
| `jurisdiction_id` | (constant `usa-fed`) | → adapter | | |

**Direction note:** uscongress JSON only carries `"congress": "119"` as a string field on bills/amendments/votes; the `LegislativeSession` row is a pure derived artifact on our side.

**Senate-is-continuing wrinkle.** The Senate is a continuing body (only 1/3 turns over each cycle); the House isn't. This shows up in `legislators-current.yaml` as senator `terms` that span multiple Congresses with a single `start`/`end`, while representative `terms` line up two-yearly. Our `LegislativeSession` is congress-scoped and our `Assignment` is date-range-scoped, so a senator's single 6-year term naturally spans three `LegislativeSession` rows via overlapping `valid_from`/`valid_to`. **This works.** No structural change needed; just documented behavior. See **Lossy** §4 for the converse direction.

### `canonical.persons` ↔ `legislators-current.yaml[*]` + `legislators-historical.yaml[*]`

Identity is owned by congress-legislators. uscongress sponsor/cosponsor/voter records carry `bioguide_id` and a minimal denormalized name slice — the canonical Person row should be populated from congress-legislators YAML, not from work-product JSON.

| usa-wa field | congress-legislators path | Direction | Transform | Notes |
|---|---|---|---|---|
| `name_full` | `name.official_full` | ↔ | direct | When absent (rare, mostly historical), derive from `name.first + ' ' + name.last`. |
| `name_first` | `name.first` | ↔ | direct | |
| `name_last` | `name.last` | ↔ | direct | |
| `name_middle` | `name.middle` | ↔ | direct | |
| `name_suffix` | `name.suffix` | ↔ | direct | |
| `name_used` | `name.nickname` | ↔ | direct | Federal uses `nickname` for the "goes-by" name; semantically equivalent to power-map's `name_used`. |
| `gender` | `bio.gender` | ↔ | `M` ↔ `male`, `F` ↔ `female` | congress-legislators only emits M/F. Our column is free-text; round-trip is lossy in the M/F → free-text → M/F direction only if a producer ever writes a non-M/F value. |
| `birth_year` | `bio.birthday` (YYYY-MM-DD) | ← only | `int(birthday.split('-')[0])` | We don't store full DOB by design; round-trip back to congress-legislators loses month/day. **Privacy-by-design loss.** |
| `current_district` | `terms[-1].district` | ← only | direct | Only meaningful for House members; senators have no district. **Open question:** §**Lossy** §3. |
| `source_id` | `id.bioguide` | ↔ | direct | Primary federal ID; the join key. |
| `powermap_person_id` | (n/a) | ↔ power-map only | Set when power-map confirms a match. | |

**External IDs.** congress-legislators carries ~15 alternate ID schemes per legislator: `thomas`, `lis`, `govtrack`, `opensecrets`, `votesmart`, `fec` (list), `cspan`, `wikipedia`, `ballotpedia`, `maplight`, `icpsr`, `wikidata`, `google_entity_id`, `house_history`, `pictorial`, plus `bioguide_previous` (a list of prior bioguides when a member's record was re-keyed). Our universal-shape only carries `source_id` + `powermap_person_id`. **This is a Tier-1 gap** (already flagged in P0 delta §"Tier 2 — `LegislatorIdentifier`"). For federal we cannot do without an external-ID side table or we lose all the cross-reference value of using congress-legislators in the first place. See **Revision §3** below.

**`other_names`.** congress-legislators has an `other_names: [{first, middle, last, suffix, start, end}]` array for name-change history (marriage, deadname downgrade, etc.). Our `Person` is single-row; the prior names are gone. Power-map's name-i18n stack handles this — for federal, we should populate `powermap_person_id` early and let power-map carry the history. Locally lossy by design.

### `canonical.organizations` ↔ `committees-current.yaml[*]` + chambers (synthesized) + parties (synthesized)

congress-legislators only models committees explicitly. Chambers and parties are implicit; the adapter synthesizes rows.

| usa-wa field | congress-legislators path | Direction | Transform | Notes |
|---|---|---|---|---|
| **Synthesized chamber rows** | — | → adapter | Adapter emits `(name="U.S. House of Representatives", short_name="House", org_type="chamber", parent_organization_id=null)` and `(name="United States Senate", short_name="Senate", org_type="chamber", parent_organization_id=null)`. | Two top-level rows per `usa-fed` jurisdiction. |
| **Synthesized party rows** | derived from `terms[].party` values | → adapter | Distinct values: `Democrat`, `Republican`, `Independent`, plus historical `Whig`, `Anti-Federalist`, etc. Adapter emits one Organization per distinct party value. | `org_type="party"`, no parent. |
| `name` (committee) | `name` | ↔ | direct | "Committee on Appropriations". |
| `short_name` | (none in source) | → adapter | Adapter derives by stripping "Committee on" prefix. | |
| `org_type` (committee, joint) | `type` ∈ `house`/`senate`/`joint` | ↔ | `house`/`senate`/`joint` committees → `committee`; subcommittees → `subcommittee`. | See **Joint committees** below. |
| `parent_organization_id` | (committee has chamber via `type`; subcommittee is nested under committee) | ↔ | Top-level committee → parent is chamber. Subcommittee → parent is committee. Joint committees → parent is null (joint committees have no single chamber parent). | Our self-referential FK handles this cleanly. **Verified.** |
| `source_id` (committee) | `thomas_id` | ↔ | 4-letter code (e.g., `SSAP` for Senate Appropriations). | Stable across decades; the federal-canonical committee ID. |
| `source_id` (subcommittee) | parent `thomas_id` + subcommittee `thomas_id` | ↔ | Concatenation per uscongress convention (e.g., `SSAP01` for Senate Appropriations Subcommittee on Agriculture). | |

**Joint committees.** Joint Committee on Taxation, Joint Committee on the Library, etc. — `type: joint` in `committees-current.yaml`. They have `senate_committee_id` but no `house_committee_id`. Membership in `committee-membership-current.yaml` carries a per-member `chamber: house|senate` discriminator. **Our `Organization.parent_organization_id` self-FK handles this** by setting `parent_organization_id=null` on the joint committee and letting both-chamber memberships hang off it via `Assignment` (the member's *home* chamber is captured in their separate Assignment to the chamber Organization). **No revision needed.**

**Subcommittees.** Nested as `subcommittees[]` inside each committee in `committees-current.yaml`. Our `org_type="subcommittee"` with `parent_organization_id=<committee>.id` handles this. **Verified.**

**PACs, lobbying firms, candidate committees, government agencies.** Not present in federal data sources surveyed here. Our `org_type` discriminator carries them for WA's PDC integration; for the federal-legislature scope they're irrelevant. (A future federal-FEC adapter would populate these.)

### `canonical.roles` (synthesized, not source-emitted)

Roles are jurisdiction-internal vocabulary. The adapter synthesizes them per Organization with `source="usa_fed_congress_legislators"` and `source_id="role:<scope>:<name>"` for upsert idempotency. Expected federal Roles:

| Organization | Role name | role_type | Source signal |
|---|---|---|---|
| U.S. House of Representatives | Representative | `elected_member` | `terms[].type == "rep"` |
| U.S. House of Representatives | Delegate | `elected_member` | `terms[].type == "rep"` + state in non-state territories (DC, PR, AS, GU, MP, VI) |
| U.S. House of Representatives | Resident Commissioner | `elected_member` | `terms[].type == "rep"` + state == "PR" |
| U.S. House of Representatives | Speaker | `leadership` | `leadership_roles[].title == "Speaker of the House"` |
| U.S. House of Representatives | Majority Leader | `leadership` | `leadership_roles[].title` regex |
| U.S. House of Representatives | Minority Leader | `leadership` | same |
| U.S. House of Representatives | Majority Whip / Minority Whip | `leadership` | same |
| United States Senate | Senator | `elected_member` | `terms[].type == "sen"` |
| United States Senate | President Pro Tempore | `leadership` | `leadership_roles[].title` |
| United States Senate | Majority Leader / Minority Leader | `leadership` | same |
| `<committee>` | Chair | `committee_leadership` | `committee-membership-current.yaml[thomas_id][*].title == "Chair"` |
| `<committee>` | Ranking Member | `committee_leadership` | `title == "Ranking Member"` |
| `<committee>` | Ex Officio | `committee_member` | `title == "Ex Officio"` |
| `<committee>` | Member | `committee_member` | members with no `title` |
| `<party>` | Member | `party_member` | `terms[].party` value |

**Open question.** Federal `Delegate` and `Resident Commissioner` are non-voting representatives. Whether to model them as distinct Roles vs. flag-on-Role is unclear; the OCD vocab has no separate value. **Recommend distinct Roles** for query convenience. See **Revision §4**.

### `canonical.assignments` ↔ `legislators-current.yaml[*].terms[]` + `committee-membership-current.yaml`

This is the cluster where the federal-stress-test bites hardest. congress-legislators expresses term-by-term membership as a `terms[]` array of `{type, start, end, state, district?, class?, state_rank?, party, caucus?, party_affiliations?}`. **Each term is essentially an Assignment.**

| usa-wa field | congress-legislators path | Direction | Transform | Notes |
|---|---|---|---|---|
| `person_id` | (FK to `canonical.persons` keyed by `id.bioguide`) | ↔ | resolved via Person source_id | |
| `role_id` | (resolved per `terms[].type`) | ↔ | `type=="rep"` → Representative Role on House; `type=="sen"` → Senator Role on Senate. Delegates/Resident Commissioners use distinct Roles per state. | |
| `valid_from` | `terms[].start` | ↔ | direct (YYYY-MM-DD) | |
| `valid_to` | `terms[].end` | ↔ | direct | |
| `is_active` | (derived) | → adapter | `today < terms[].end and today >= terms[].start` | |
| `source_id` | (synthesized) | → adapter | `f"assignment:{bioguide}:role:{role_slug}:{valid_from}"` per universal-shape pattern | congress-legislators has no per-term ID. |

**Mid-term party switches.** `terms[].party_affiliations: [{start, end, party, caucus?}]` appears when a member switches parties mid-term (e.g., Joe Lieberman 2006). We need **one Assignment to the original party + one Assignment to the new party** with their respective date ranges. The `terms[].party` field carries the *most recent* affiliation. **Adapter logic:** if `party_affiliations` is present, emit one Assignment per entry; if not, emit a single Assignment for the term's full duration to `terms[].party`. **Our schema handles this.** No revision needed.

**Committee memberships.** From `committee-membership-current.yaml`, which is keyed by committee `thomas_id` → `[{bioguide, party, rank, title?, chamber?}]`. Each entry produces an Assignment from `Person(bioguide)` to `Role(committee, title or "Member")`. **No date ranges in this YAML** — it's a current-state snapshot. `committees-historical.yaml` carries committee existence ranges but **per-member historical committee assignments are not in the canonical files**. See **Lossy** §5.

**Leadership roles.** `legislators-current.yaml[*].leadership_roles: [{title, chamber, start, end}]` carries Speaker, Whip, Pro Tempore, etc. Each entry produces an Assignment from Person to Role(chamber, title). **Date ranges are present** — clean mapping.

### `canonical.bills` ↔ `data/<congress>/bills/.../data.json`

The work-product spine.

| usa-wa field | uscongress JSON path | Direction | Transform | Notes |
|---|---|---|---|---|
| `legislative_session_id` | `congress` | ↔ | Look up `LegislativeSession` by `slug == f"usa-fed-{congress}"` | |
| `chamber` | derived from `bill_type` | ↔ | `hr`/`hres`/`hjres`/`hconres` → `house`; `s`/`sres`/`sjres`/`sconres` → `senate` | uscongress has no explicit `originating_chamber` field — it's encoded in `bill_type`'s first letter. |
| `number` | `number` | ↔ | direct | |
| `bill_type` | `bill_type` | ↔ | direct (`hr`, `hres`, `hjres`, `hconres`, `s`, `sres`, `sjres`, `sconres`) | See **Vocabulary §1** below. |
| `title` | `short_title` (preferred) else `popular_title` else `official_title` | ← derive | uscongress carries all three on the bill JSON. **No 1:N titles table** in our v0 (P0 delta §"Tier 2 BillTitle" defers it). | **Lossy.** See **Lossy** §6. |
| `short_description` | `official_title` | ← | direct when not used for `title` | Inverted from WA convention — federal's `official_title` is the long form. |
| `current_status` | `status` | ↔ | uscongress emits one of ~30 enum values (e.g., `INTRODUCED`, `PASSED:BILL`, `ENACTED:SIGNED`, `VETOED:OVERRIDE_PASS_OVER:HOUSE`). | Our column is `text(128)`; values fit. See **Vocabulary §3**. |
| `current_step` | (none) | → derive | Federal has no separate `step` field; collapse into `current_status`. **This confirms P0 delta recommendation to drop `current_step`.** See **Revision §1**. | |
| `introduced_at` | `introduced_at` | ↔ | direct (date) | |
| `current_text` | (separate `bill_text` task; not in `data.json`) | → defer | Federal punts text to GovInfo URLs in `text-versions/<code>/data.json`. **Aligns with v0's P3-defer of full text.** | |
| `source_id` | `bill_id` | ↔ | `<bill_type><number>-<congress>` lowercase | |

**Fields uscongress carries that we drop on the floor:**

- `updated_at` (last update from GovInfo). We capture this via universal-shape `last_fetched_at`.
- `enacted_as: {congress, law_type, number}` — when a bill becomes law, the resulting Public Law / Private Law citation. **Not modeled in v0.** P0 delta flagged this as "Field addition §4: `Bill.enacted_as`". See **Revision §5**.
- `history: {active, awaiting_signature, enacted, vetoed, house_passage_result, senate_passage_result, house_override_result, senate_override_result, ...}` — denormalized lifecycle flags. We can recompute these from `bill_actions` and `vote_events`. Acceptable loss.
- `subjects: [str]` and `subjects_top_term`. Subject tags. **Not modeled in v0.** P0 delta §"Tier 2 BillSubject" defers. See **Revision §7**.
- `summary: {text, date, as}` — the CRS summary. **Not modeled.** Defer to P3.
- `committee_reports: [str]` — report numbers. Federal-specific; rare at state level. Skip.
- `related_bills: [{bill_id, reason, type}]` — companion bills, related bills. **Not modeled in v0.** P0 delta §"Tier 1 #3 BillRelationship" flagged. See **Revision §6**.

### `canonical.bill_sponsorships` ↔ `data.json:sponsor` + `data.json:cosponsors[]`

| usa-wa field | uscongress JSON path | Direction | Transform | Notes |
|---|---|---|---|---|
| `bill_id` | (FK by `Bill.source_id == bill_id`) | ↔ | | |
| `person_id` | `sponsor.bioguide_id` / `cosponsors[].bioguide_id` | ↔ | Resolve via Person. | |
| `organization_id` | (committee-sponsored bills surface in `bill_type` of `hres`/`sres`/`hconres`/`sconres` reported from committee, but the `sponsor` field is still a person in `data.json`) | ↔ | **Federal bills are never committee-sponsored at the data-shape level** — the `sponsor` field always points to a person. **Committee-sponsored amendments DO exist** — see Amendment cluster below. | See **Vocabulary §4** below. **Important finding.** |
| `role` | `sponsor` vs `cosponsors[]` distinction | ↔ | `sponsor` → `primary`; `cosponsors[]` entries → `co` | The 4-value OCD vocab (`primary`/`co`/`joint`/`generic`) overcovers; federal uses only `primary` and `co`. **Acceptable.** |
| `sponsor_order` | implicit array order for cosponsors; 1 for primary | ↔ | 1-indexed | |
| `withdrawn_at` | `cosponsors[].withdrawn_at` | ↔ | direct (date, nullable) | **This is the column that survives the federal case.** Cosponsors can and do withdraw. Our v0 has the column; P0 delta flagged. |

**Original cosponsor flag.** uscongress emits `cosponsors[].original_cosponsor: bool` — whether they signed on the day of introduction vs. later. We don't have a column for this. **Minor information loss.** Recommend not adding the column; "original cosponsor" can be derived by comparing `Bill.introduced_at` to `BillSponsorship.created_at`-equivalent (we don't store the `sponsored_at` date on sponsorship; see below).

**`sponsored_at` date on cosponsors.** uscongress emits `cosponsors[].sponsored_at` — the date the cosponsor signed on. We don't store this. **Loss.** Could be folded into `created_at` if the adapter writes that field deterministically from source. See **Revision §8**.

### `canonical.bill_actions` ↔ `data.json:actions[]`

| usa-wa field | uscongress JSON path | Direction | Transform | Notes |
|---|---|---|---|---|
| `bill_id` | (FK) | ↔ | | |
| `action_at` | `actions[].acted_at` | ↔ | direct (ISO 8601) | |
| `chamber` | `actions[].where` (when type is `vote`/`vote-aux`); else derived | ← | `h` → `house`, `s` → `senate`; for non-vote actions, infer from action text | uscongress doesn't tag every action with a chamber — actions originating in committee inherit committee's chamber via `in_committee`. |
| `acting_organization_id` | `actions[].in_committee` (when present) | ← | Resolve to Organization by committee name regex; fall back to chamber Organization | uscongress emits committee *name* string, not `committee_id`. **Resolution risk.** |
| `action_type` | `actions[].type` | ↔ | uscongress vocab: `referral`, `reported`, `hearings`, `discharged`, `calendar`, `topresident`, `signed`, `vetoed`, `enacted`, `vote`, `vote-aux`, `ordered-reported`, `action` (default). | See **Vocabulary §2**. |
| `description` | `actions[].text` | ↔ | direct | |

**Fields uscongress carries on actions that we drop:**

- `actions[].references: [{reference, type}]` — inline citations like "CR H3862", "Roll Call No. 142". Useful for surfacing committee report numbers, Congressional Record citations. **Not modeled in v0.** Acceptable loss for MVP; revisit if action-text search becomes a query target.
- `actions[].vote_type` (`vote`, `vote2`, `pingpong`, `conference`, `override`, `cloture`) — vote-flavor subclassification on vote actions. Maps onto VoteEvent more naturally; see below.
- `actions[].roll` (roll-call number) — the join key to the corresponding `data/<congress>/votes/<session>/h<roll>/data.json`. **Adapter must hold this in a transient lookup table** to link the action's resulting VoteEvent.
- `actions[].suspension` — "passed under suspension of the rules" flag. House-specific. Drop.
- `actions[].pocket` — pocket-veto flag. Drop or fold into `description`.

### `canonical.bill_versions` ↔ `data/<congress>/bills/.../text-versions/<code>/data.json`

Federal text-version codes (the `<code>` segment): `ih` (Introduced in House), `is` (Introduced in Senate), `rh` (Reported in House), `rs` (Reported in Senate), `eh` (Engrossed in House), `es` (Engrossed in Senate), `enr` (Enrolled Bill), `pcs` (Placed on Calendar Senate), `cps` (Conference Report Senate), and ~25 others.

| usa-wa field | uscongress JSON path | Direction | Transform | Notes |
|---|---|---|---|---|
| `bill_id` | (FK) | ↔ | | |
| `version_type` | `<code>` segment in path | ↔ | Map per a static table. `ih`/`is` → `original`; `rh`/`rs` → `reported`; `eh`/`es` → `engrossed`; `enr` → `enrolled`. **~30 codes collapse to ~6 of our values.** | **Lossy** §7. |
| `version_at` | text-version `data.json:issued_on` or equivalent | ← | (would need to fetch text-version JSON) | |
| `is_current` | (derived: most recent text-version) | → adapter | adapter sets the latest `version_at` to true | |
| `source_id` | bill_version_id (`<bill_type><number>-<congress>-<code>`) | ↔ | direct | |

### `canonical.amendments` ↔ `data/<congress>/amendments/.../data.json`

**The federal case where polymorphic `sponsor_organization_id` earns its keep.** uscongress confirms in `amendment_info.py:sponsor_for()`:

```python
if sponsor.get('bioguideId') is None:
    # A committee can sponsor an amendment!
    return {"type": "committee", "name": name, ...}
```

| usa-wa field | uscongress JSON path | Direction | Transform | Notes |
|---|---|---|---|---|
| `bill_id` | `amends_bill.bill_id` | ↔ | Resolve via Bill.source_id. Null when amendment amends another amendment (rare: `amends_amendment` present) or a treaty. | See **Lossy** §8. |
| `label` | `f"Amendment {number}"` or richer from `description` | → adapter | Federal carries `number`, `purpose`, `description`. Our `label` is text(64) — pack as `f"{amendment_type.upper()}{number}"` (e.g., "SAMDT712"). | |
| `amendment_text` | (separate; not in `data.json`) | → defer | Same text-vs-metadata split as bills. | |
| `sponsor_person_id` | `sponsor.bioguide_id` when `sponsor.type == "person"` | ↔ | Resolve | |
| `sponsor_organization_id` | `sponsor.name` when `sponsor.type == "committee"` | ↔ | Resolve to Organization by committee name regex (uscongress emits "House Rules" / "Senate Rules" style after their internal cleanup) | **Polymorphism earns its keep.** This is the case the v0 spec was preparing for. |
| `status` | `status` | ↔ | uscongress emits `offered` / `pass` / `fail` / `withdrawn`. Our vocab: `offered` / `adopted` / `rejected` / `withdrawn` / `pending` / `tabled`. **Map `pass` → `adopted`, `fail` → `rejected`**. | Lossless if `pending` and `tabled` never appear in uscongress (they don't — they get folded into action history). |
| `offered_at` | `introduced_at` | ↔ | direct | |
| `adopted_at` / `rejected_at` / `withdrawn_at` | `status_at` (conditional on `status`) | ↔ | branch on status | |
| `source_id` | `amendment_id` | ↔ | `<amdt_type><number>-<congress>` | |

**Field that doesn't fit anywhere.** `amends_amendment: {amendment_id, ...}` — amendments to amendments (substitute amendments, perfecting amendments). We have **no `Amendment → Amendment` relation** in v0. Either model as a self-FK or drop. See **Revision §9**.

**Treaty amendments.** `amends_treaty: {treaty_id, ...}` exists for ~hundreds of historical amendments. Treaties are a federal-only entity type we don't model. **Acceptable loss for MVP**; if federal scope expands to treaties, we'd add a `Treaty` entity.

### Vote cluster ↔ `data/<congress>/votes/<session>/<chamber><number>/data.json`

The richest test of our polymorphic `VoteEvent` shape.

#### `canonical.vote_events`

| usa-wa field | uscongress JSON path | Direction | Transform | Notes |
|---|---|---|---|---|
| `subject_type` | conditional on `vote.bill` / `vote.amendment` / `vote.nomination` / `vote.treaty` presence | ↔ | `vote.bill` present → `bill`; `vote.amendment` present → `amendment`; else → `motion` | Federal also has `nomination` and `treaty` subjects — neither maps cleanly onto our 3-value `subject_type` enum. See **Revision §10**. |
| `subject_id` | resolved from `vote.bill.{type, number, congress}` or `vote.amendment.{type, number}` | ↔ | | |
| `bill_id` (denorm) | `vote.bill` direct, or `vote.amendment` → look up amendment → its `amends_bill.bill_id` | ↔ | | |
| `amendment_id` | `vote.amendment` resolved | ↔ | | |
| `motion_description` | `vote.question` when no bill/amendment subject | ↔ | direct | |
| `context_type` | (always `floor` for federal) | → adapter | Federal data only covers floor votes; committee votes are not in `unitedstates/congress` votes corpus. **Confirmed lossy direction.** See **Lossy** §2. |
| `context_organization_id` | derived from `vote.chamber` | ↔ | `h` → House, `s` → Senate | |
| `chamber` | `vote.chamber` | ↔ | `h` → `house`, `s` → `senate` | |
| `event_at` | `vote.date` | ↔ | ISO 8601 | |
| `outcome` | `vote.result` and `vote.result_text` | ↔ | `result == "Passed"` / `"Agreed to"` → `passed`; `result == "Failed"` / `"Rejected"` → `failed`; certain motion types ("Motion to Table" succeeding) → `tabled` | **Recommittal, cloture-failed, etc. complications.** See **Vocabulary §3** and **Revision §11**. |
| `source_id` | `vote.vote_id` | ↔ | `<chamber><number>-<congress>.<session>` | |

**`vote.category`** in uscongress is one of: `passage`, `passage-suspension`, `amendment`, `cloture`, `nomination`, `treaty`, `recommit`, `quorum`, `leadership`, `conviction`, `veto-override`, `procedural`, `unknown`. **We don't have a `vote_event.category` column.** This is structured data we'd lose. See **Revision §12**.

**`vote.vote_type`** (`vote`, `vote2`, `pingpong`, `conference`, `override`, `cloture`) is similar information at a different grain. Same issue.

#### `canonical.vote_counts`

`vote.votes` is keyed by vote choice; uscongress writes per-vote-choice arrays of voter dicts. **No pre-aggregated counts in uscongress JSON** — counts must be computed by `len(vote.votes[choice])`.

| usa-wa field | uscongress derivation | Direction | Transform | Notes |
|---|---|---|---|---|
| `vote_event_id` | (FK) | ↔ | | |
| `count_type` | enum | ↔ | `Yea`/`Aye` → `yea`; `Nay`/`No` → `nay`; `Present` → `present_not_voting`; `Not Voting` → `absent`; `Guilty` (impeachment) → ?; `Not Guilty` → ? | **House vote-vocabulary distinction loss.** See §**Vocabulary §3** and **Revision §13**. |
| `value` | `len(vote.votes[choice])` | ↔ | computed | |

#### `canonical.person_votes`

| usa-wa field | uscongress derivation | Direction | Transform | Notes |
|---|---|---|---|---|
| `vote_event_id` | (FK) | ↔ | | |
| `person_id` | `vote.votes[choice][].id` (bioguide for House, LIS for Senate; the code normalizes to `bioguide` for House and `lis` for Senate) | ↔ | Resolve via Person. **Senate uses LIS IDs, not bioguide.** | See **Revision §3** and **Lossy** §9. |
| `vote` | enum | ↔ | Same as `count_type` above. | |

**`PersonVote.vote` vocabulary.** v0 lists: `yea` / `nay` / `abstain` / `excused` / `absent` / `present_not_voting`. uscongress emits **`Yea`** / **`Nay`** / **`Aye`** / **`No`** / **`Present`** / **`Not Voting`** / **`Guilty`** / **`Not Guilty`**. House uses Yea/Nay for recorded floor votes but Aye/No for "other recorded vote" types (division votes, quorum calls). Senate uses Yea/Nay (or Guilty/Not Guilty for impeachment conviction). **The Aye/No vs. Yea/Nay distinction encodes chamber procedure** — recorded vs. on-the-floor, partly. If we collapse to `yea`/`nay`, round-tripping back to source loses that distinction. **Open call:** see **Revision §13**.

### Statute cluster ↔ (not in uscongress; federal-USC mapping is a separate spec)

The `BillStatuteChange` link lives in our schema as `(bill_id, statute_section_id, change_type)`. uscongress's `enacted_as: {congress, law_type, number}` is the federal analog of WA's "Chapter X, Laws of YYYY", but it points to **Public Law numbering**, not to a USC section. The mapping from Public Law → USC sections is itself a separate downstream artifact (the Office of Law Revision Counsel performs codification). **Out of scope** for this transformation spec; a `usa-fed-adapter-statute` spec would address it.

What we *can* say: `Bill.enacted_as` should be added (see **Revision §5**) so that the eventual statute-side join has something to join on.

### Lobbying / contribution clusters ↔ (no federal source in this transformation)

uscongress doesn't model lobbying or campaign finance. The federal-FEC adapter (a future `usa-fed-adapter-fec`) would map to our `lobbying_activities` / `contributions` cluster. **Out of scope** here.

## Vocabulary alignment

### §1 — Bill numbers and types

Federal bill types and our mapping:

| Federal `bill_type` | Long name | usa-wa `bill_type` | usa-wa `chamber` |
|---|---|---|---|
| `hr` | House Bill | `HR` | `house` |
| `hres` | House Simple Resolution | `HRes` | `house` |
| `hjres` | House Joint Resolution | `HJRes` | `house` |
| `hconres` | House Concurrent Resolution | `HConRes` | `house` |
| `s` | Senate Bill | `S` | `senate` |
| `sres` | Senate Simple Resolution | `SRes` | `senate` |
| `sjres` | Senate Joint Resolution | `SJRes` | `senate` |
| `sconres` | Senate Concurrent Resolution | `SConRes` | `senate` |

**Status:** v0's `Bill.bill_type: text(32) nullable` accommodates all eight values. **No revision needed at the column level.** Vocabulary normalization is the adapter's responsibility.

**OCD comparison.** OCD's `BILL_CLASSIFICATIONS` is a 24-value flat list with semantic types (`bill`, `resolution`, `joint resolution`, `concurrent resolution`). Our column matches federal's prefix-encoded format; an OCD-style `classification` (semantic) is a separate dimension. **Worth considering** for v1 — see **Revision §14**.

### §2 — Bill action types

Federal action `type` values from `bill_info.py`:

| Federal | OCD equivalent | usa-wa `action_type` (v0 is source-vocab text) |
|---|---|---|
| `referral` | `referral`, `referral-committee` | `referral` |
| `reported` | `reported-out-of-committee` | `reported` |
| `ordered-reported` | `committee-passage` | `ordered-reported` |
| `hearings` | `hearing-held` | `hearings` |
| `discharged` | (no direct) | `discharged` |
| `calendar` | (no direct) | `calendar` |
| `vote` | `passage`, `reading-3`, etc. (vote-type-dependent) | `vote` |
| `vote-aux` | `amendment-passage`, etc. | `vote-aux` |
| `topresident` | (no direct) | `topresident` |
| `signed` | `executive-signature` | `signed` |
| `vetoed` | `executive-veto` | `vetoed` |
| `enacted` | `became-law` | `enacted` |
| `action` (default) | (varies) | `action` |

v0 stores `action_type` as source-vocab `text(64)` — **no normalization in v0**, per design. Action types per **Revision §15** below would add a normalized OCD column.

**Federal-specific actions not in OCD:**

- **Conference report submitted / agreed to.** Federal uses bicameral conferences to reconcile competing House/Senate versions. Surfaces as `vote_type: conference` on vote actions and "Conference report submitted" / "Conference report agreed to" in action text. OCD's vocab doesn't carry these.
- **Suspension of the rules.** House-only fast-track. Surfaces as `suspension: true` flag on `vote_type: vote` actions and category `passage-suspension` on votes.
- **Hold at the desk.** Senate-specific procedural action; not normalized anywhere.
- **Pocket veto.** `vetoed` action with `pocket: 1` field.

**Recommendation.** Defer normalization to v1+. Adapter writes raw federal `type` to `action_type`. **No structural revision needed.** P0 delta §"Field addition #9 BillAction.classification as array" is still the right call for v1.

### §3 — Vote outcome / vote choice

The most contentious vocabulary alignment.

**Vote choice** at `PersonVote.vote` and `VoteCount.count_type`:

| Federal context | Federal keys | usa-wa v0 collapse | Lossless? |
|---|---|---|---|
| House recorded floor vote | `Yea`, `Nay`, `Present`, `Not Voting` | `yea`, `nay`, `present_not_voting`, `absent` | ⚠️ `Not Voting` ≠ `absent` precisely (NV includes excused) |
| House division/quorum vote | `Aye`, `No`, `Present`, `Not Voting` | same as above | ❌ **Aye/No vs Yea/Nay distinction lost** |
| Senate non-conviction vote | `Yea`, `Nay`, `Present`, `Not Voting` | `yea`, `nay`, `present_not_voting`, `absent` | ⚠️ same NV concern |
| Senate impeachment | `Guilty`, `Not Guilty`, `Present`, `Not Voting` | **no values** for Guilty/Not Guilty | ❌ **Impeachment verb-pair entirely lost** |

**Three distinct losses:**

1. House Aye/No (division votes, quorum, "other recorded") vs. Yea/Nay (roll call). Collapses to `yea`/`nay` and the procedural distinction (whether it was a recorded roll-call vote vs. a voice/division vote) disappears from `PersonVote`. The procedural distinction *is* recoverable from the parent `VoteEvent` (a division vote has no per-person detail in the first place; only roll-calls do), so **practical loss is small** — but the round-trip yea↔Yea/Aye is ambiguous without `VoteEvent` context.
2. Impeachment verb. `Guilty`/`Not Guilty` carries political meaning beyond pass/fail. Collapsing to `yea`/`nay` is journalistically defensible but semantically lossy. **Recommend adding `guilty` / `not_guilty` to the vocab** — see **Revision §13**.
3. `Not Voting` (absent without leave) vs. `Excused` (absent with leave) vs. our `absent` (lump). uscongress doesn't separate them on `PersonVote.vote`; it normalizes "Present, Giving Live Pair" → "Present" upstream of us. **Loss is upstream of our schema** — we can't recover what wasn't there.

**Note (2026-06-01):** our vocab does carry the distinction — `PersonVote.vote` and `VoteCount.count_type` both have separate `excused` and `absent` values. In WA, legislators can motion to excuse colleagues unavailable to vote, and the Excused vote type is procedurally distinct from a plain Absent. Our schema preserves this; only uscongress-sourced rows lose the distinction (because uscongress doesn't expose it). WSL primary source is authoritative for the Excused/Absent split.

**Outcome** at `VoteEvent.outcome`:

| Federal `vote.result` | usa-wa `outcome` |
|---|---|
| `Passed` | `passed` |
| `Agreed to` | `passed` |
| `Failed` | `failed` |
| `Rejected` | `failed` |
| `Not Agreed to` | `failed` |
| (Motion to Table where the motion succeeded) | `tabled` |

**Edge case: recommittal motions and cloture.** Federal `vote.category` distinguishes `recommit`, `cloture`, `veto-override`, `nomination`, `treaty`, `conviction`, `quorum`, `leadership`, `procedural`. None of these are `outcome` values per se — they're *categories* of the vote. Our v0 has no `vote_event.category` column. **See Revision §12.**

### §4 — Committee-sponsored bills

**Finding.** Federal Congress does **not** routinely surface committee-sponsored bills as a distinct `sponsor.type == "committee"` case in `data.json:sponsor`. The `bill_info.py` code carries a TODO comment ("Sponsored by committee?") but the `sponsor_for()` function regex-matches only `(Rep.|Sen.|Del.|Resident Commissioner|Rescom.)`. Bills *can* be reported by a committee with no individual sponsor (so-called "clean bills"), but the data-shape always identifies an individual member sponsor.

**Where committee-as-actor *does* appear at the data-shape level:**

1. **Amendments.** `amendment_info.py:sponsor_for()` explicitly handles `sponsor.type == "committee"`. House Rules Committee and Senate Rules Committee routinely sponsor amendments (especially en-bloc amendments and manager's amendments). **This is the test case our polymorphic `Amendment.sponsor_organization_id` was designed for. Verified.**
2. **Implicit committee-as-sponsor** for clean bills surfaces via the `committees[]` array with `activity: ["origin"]` — a committee that "originated" the bill. But the explicit `sponsor` field still names an individual.

**Conclusion.** v0's polymorphic `BillSponsorship` with nullable `person_id` / `organization_id` is **correct architecture but over-prepared for federal bills specifically**. The win comes from **`Amendment.sponsor_organization_id`**, which is essential. Both columns earn their keep across the WA + federal scope.

**OCD-aligned 4-value role vocab (`primary` / `co` / `joint` / `generic`).** Federal uses only `primary` and `co`. Joint sponsorship (`joint` in OCD) is a state-legislature concept that doesn't apply federally. `generic` would be an unresolved or ambiguous sponsorship type. **The 4-value vocab covers federal usage with two values to spare.**

### §5 — Subcommittees and joint committees

Both are present in `committees-current.yaml`:

- **Subcommittees** are nested in `subcommittees[]` arrays inside each committee. They have their own `thomas_id` (2-digit) and `name`. Our `org_type="subcommittee"` + `parent_organization_id=<committee>.id` handles them. **Verified.**
- **Joint committees** appear at the top level with `type: joint`. They have `senate_committee_id` but no `house_committee_id`. They have no chamber parent. Our `parent_organization_id=null` + `org_type="committee"` handles them; cross-chamber membership is captured in `committee-membership-current.yaml`'s per-member `chamber` field, which surfaces in our schema as the member's separate Assignment to their home-chamber Organization. **Verified.**

**No revision needed for committee/subcommittee/joint modeling.**

## Lossy directions

### Federal → usa-wa losses

1. **Session subdivision within a Congress.** Federal `vote_id` encodes a session-year (e.g., `h142-119.2025`) — the 119th Congress runs 2025 and 2026, and votes are partitioned by calendar year. Our `LegislativeSession` is congress-scoped (`usa-fed-119`), not session-year-scoped. Recoverable from `event_at.year` on vote events, but the explicit two-session-per-Congress structure (1st Session, 2nd Session) is lost from `LegislativeSession`. **Minor loss; reconstructible.**
2. **Committee votes.** `unitedstates/congress` does **not** collect committee votes — only floor votes. We have an empty `context_type: committee` partition for federal until a separate source feeds it. (House and Senate committees do publish markup vote records, but they're not in this pipeline.) **Structural data gap; not a schema gap.**
3. **District-as-Role question (open from v0 §Open issues #1).** Federal `terms[].district` is null for senators, integer (1+) for representatives, or `"At Large"` / `0` for at-large representatives. Our `Person.current_district: text(32)` is a single-row denormalized column. Federal's term-by-term district changes (a member who moves districts after redistricting, e.g.) are lost. **Power-map archival is the right resolution** — power-map can carry the term-by-term district history as a structured-parts sidecar on the Role/Assignment join.
4. **Senate-as-continuing-body session conventions.** A senator's `terms[].start` and `terms[].end` span 6 years and three Congresses. Our `LegislativeSession` rows are 2-year. The Assignment-spans-multiple-Sessions case is naturally handled (Assignment is date-scoped, not session-FK-scoped), but **we have no way to ask "which Assignments were active during LegislativeSession X"** without a date-range join. Pre-computing an Assignment ↔ LegislativeSession bridge table is one option; computing on the fly is the other. **Recommend on-the-fly** for v0; revisit if query patterns demand it.
5. **Historical committee membership.** `committee-membership-current.yaml` is **current-state-only**. Historical committee assignments are not in any canonical file. For federal, "Who chaired the Senate Health Committee in 1995?" cannot be answered from upstream YAML alone. **Hard upstream gap; not our schema's fault.** Mitigation: query power-map or external sources (CRS, govtrack), or accept the gap.
6. **Multiple titles per bill.** Federal carries `official_title`, `popular_title`, `short_title`, `titles[]`. v0 carries `title` + `short_description`. **Loss is real**; P0 delta §"Tier 2 BillTitle" defers a 1:N table. For federal we lose the `popular_title` ("Affordable Care Act") and the title history. See **Revision §6**.
7. **Bill-version code granularity.** Federal carries ~30 distinct text-version codes (`ih`, `is`, `rh`, `rs`, `eh`, `es`, `enr`, `pcs`, `cps`, `eas`, `eah`, ...); v0 carries ~6 normalized values. **Procedural detail lost** but practical query value of the lost detail is low.
8. **Amendment-amends-amendment.** uscongress's `amends_amendment` field is unmodeled; substitute amendments and perfecting amendments lose their relationship structure. See **Revision §9**.
9. **Senate-uses-LIS-not-Bioguide.** uscongress vote JSON for Senate votes uses LIS member IDs, not bioguide IDs. Our `Person.source_id` is one column. **Without an external-IDs side table, we'd have to pick one and adapter-side translate.** See **Revision §3**.

### usa-wa → federal losses (the round-trip back direction)

10. **Power-map identity.** Once a `Person` is matched to a `powermap_person_id`, exporting back to congress-legislators format loses the power-map mapping (no field in YAML for it). Acceptable; congress-legislators is not a write target for us.
11. **PDC-cluster entities** (lobbying activities, contributions, candidate committees) have no federal-legislative analog. Round-trip to congress-legislators fails entirely — but the design intent isn't to round-trip; it's to be a superset.
12. **Statute-change links** (`BillStatuteChange`) have no federal analog in this corpus (USC mapping is a separate codification pipeline).

## Indirect-provider adapter notes (`usa-fed-adapter-legislature`)

A future sibling-deployment package would mirror the WA adapter shape:

- **Source poll.** `unitedstates/congress` produces files into a local `data/` tree; the adapter clones the repo or pulls from `unitedstates.github.io` mirrors. Update cadence: the upstream `usc-run` is typically scheduled hourly during sessions; freshness for our consumers would be ~1 hour behind GovInfo (the upstream of upstreams).
- **congress-legislators ingestion.** The YAML files update on the order of days to weeks (slower-changing identity data). Adapter fetches `legislators-current.yaml` + `committees-current.yaml` + `committee-membership-current.yaml` once daily.
- **Identity-first ordering.** Pull congress-legislators *before* the work-product. Bills and votes reference bioguide IDs; if the Person isn't in our DB, the FK resolution stalls. **Ingest order matters.**
- **No SOAP / no rate limit.** Compared to WA's SOAP rate-limited adapter, federal is cheap to ingest: it's just files. **Architectural simplification.**
- **What gets sacrificed.**
  - Subject tagging (Revision §7) until we add the column.
  - Multiple titles per bill (Revision §6) until 1:N.
  - Amendment-of-amendment relationships (Revision §9) until self-FK.
  - Committee votes (entirely upstream-missing).
  - `enacted_as` Public Law cross-reference (Revision §5) until we add the column.
- **What's mechanically easy.**
  - Bioguide IDs are stable; `Person` natural-key uniqueness via `(jurisdiction_id="usa-fed", source="usa_fed_congress_legislators", source_id=<bioguide>)` is unproblematic.
  - Federal `bill_id` format already encodes everything our `(jurisdiction_id, source, source_id)` needs.
  - Roles for the federal scope are a small known set (~12 values). Pre-seed at adapter init.
- **Power-map archival fit.** Federal Persons should be among the *first* power-map exports — bioguide IDs are widely cross-referenced and a high-value identity catalog. Once `powermap_person_id` is populated for federal members, downstream cohort services get federal identity resolution "for free".

## Open revisions for hybrid IA v1

Numbered, with severity. **The federal stress test surfaces 5 high-impact revisions and 10 lower-impact ones.**

### High-impact (recommend for v1)

1. **Drop `Bill.current_step`.** Federal has no separate step field; the column is WA-vocabulary that doesn't generalize. Already flagged in P0 delta §"Renames §3". **Cost: trivial.** Replace with v1 column `Bill.current_status_at: timestamptz nullable` (federal `status_at`).

**Status update (v1, 2026-05-27):** ✅ LANDED. `Bill.current_step` was dropped; replaced by `Bill.current_status_class` (normalized vocab) + `Bill.current_status_at` (timestamp). User confirmed (2026-06-01) the conceptual framing: `current_step` would have been denormalized state derivable from the bill_actions / bill_events stream. Removing it eliminates a synchronization burden — readers can query the action log directly or use the denormalized `current_status_class` + `current_status_at` fast path.
2. **Add `vote_event.category: text(32) nullable`.** Federal `vote.category` is structured data (`passage` / `cloture` / `recommit` / `nomination` / etc.) that we currently throw away. Adding this column is cheap (one nullable text column) and unlocks query patterns like "all cloture votes on HB-X" and "all conviction votes in 117th Congress" that are currently impossible. **High value, low cost.**

**Status update (v1, 2026-05-27):** ✅ LANDED. `VoteEvent.category: text(32) nullable` added with vocab `passage / cloture / recommit / tabling / motion_to_proceed / nomination / treaty / conviction / procedural / other`.
3. **Add `canonical.person_identifiers` (1:N).** P0 delta §"Tier 2 LegislatorIdentifier" already flagged this; the federal case makes it Tier-1. Columns: `(person_id, scheme, value)`. Schemes for federal: `bioguide`, `lis`, `thomas`, `govtrack`, `opensecrets`, `votesmart`, `fec`, `cspan`, `wikipedia`, `ballotpedia`, `icpsr`, `wikidata`. Without this, **Senate votes don't resolve** (they're LIS-keyed, not bioguide-keyed) and cross-reference value collapses.

**Status update (v1, 2026-05-27):** ✅ LANDED — confirmed early candidate per user (2026-06-01). `canonical.person_identifiers` + `canonical.organization_identifiers` 1:N child tables both landed in v1 with the uniqueness constraints to support the Senate-LIS-vs-bioguide cross-resolution pattern.
4. **Distinguish Delegate / Resident Commissioner / Representative as separate Roles, not flags.** Federal has 5 voting-rights-distinct elected-member types. Modeling them as 5 Roles on the House Organization is cleaner than a flag on Person/Assignment. Adapter pre-seeds the Roles.

**Status update (2026-06-01):** ✅ Agreed — schema already supports it (Role is a polymorphic concept; the federal usa-fed-api adapter pre-seeds distinct Roles for Representative / Delegate / Resident Commissioner with appropriate `district` values). No usa-wa schema change required; the v1 hybrid IA already covers this in its `Role` examples (see hybrid IA spec's `canonical.roles` section).
5. **Add `Bill.enacted_as: text(64) nullable`.** Federal `enacted_as.law_type + "-" + enacted_as.congress + "-" + enacted_as.number` produces "Public Law 119-12" or "Private Law 119-3". Cheap text column. P0 delta §"Field addition §4" already flagged. **Adds enormous query value** for "what bills became law" without requiring the eventual USC-section integration to be complete.

**Status update (v1, 2026-05-27):** ✅ LANDED. `Bill.enacted_as: text(64) nullable` added.

### Medium-impact (recommend for v1 but not blocking)

6. **Add `canonical.bill_titles` (1:N).** Federal `popular_title` ("Affordable Care Act") and `short_title` distinct from `official_title` are common. Columns: `(bill_id, title, type ∈ {official, short, popular, alternate}, as)`. P0 delta §"Tier 2 BillTitle". Without this, our `bill.title` overwrites itself across the bill's life cycle.

**Status update (v1.1, 2026-05-28):** ✅ LANDED — confirmed early candidate per user (2026-06-01). `canonical.bill_titles` 1:N table with `title_type` / `chamber` / `as_of_action` / `language_code` / `amendment_id` / `effective_at` / `replaced_at` / `is_current` columns. The federal-specific shape (`as` lifecycle anchor) was a direct input to v1.1; the WA-specific shape (`amendment_id` for amendment-driven title changes) was the WA-side addition.
7. **Add `canonical.bill_subjects` (1:N or `text[]` array column).** Federal `subjects` + `subjects_top_term` carries structured policy-area tags. P0 delta §"Tier 2 BillSubject". Cheap; high query value for sibling-deployments tracking policy areas.

**Status update (v1, 2026-05-27):** ✅ LANDED — confirmed early candidate per user (2026-06-01). `canonical.bill_subjects (bill_id, subject, is_primary)` 1:N table. `is_primary` covers federal `subjects_top_term` semantically.
8. **Add `BillSponsorship.sponsored_at: date nullable`.** Federal `cosponsors[].sponsored_at` is a real date that's lost without this column. Recovers original-cosponsor inference (compare to `Bill.introduced_at`). Trivial.

**Status update (v1.3, 2026-06-01):** ✅ LANDED as `timestamptz nullable` (more precise than the original `date` proposal — federal data is date-precision but WSL SOAP carries timestamps). Migration `20260601_bill_class_sponsored_at`. Round-trip test exercises the original-cosponsor inference pattern.

### Low-impact / open call

9. **Amendment-amends-amendment self-FK.** Add `Amendment.amends_amendment_id: ULID nullable FK self`. Rare but federal-real. Decide on cost-vs-value during v1 implementation.

**Status update (2026-06-01):** ✅ Resolved by the existing model for the WA case (no schema change). User noted: Proposed Substitute and Striking Amendments in WA are associated with bill texts — when adopted, an `Amendment` produces a new `BillVersion` (via `BillVersion.amendment_id` FK, v1.2). Subsequent amendments target that new BillVersion, naturally modeling "amendment to amendment" through the BillVersion intermediate. Each Amendment.bill_id continues to point to the underlying Bill; the chain of amendments-producing-versions captures the lineage. ⚠️ **Federal edge case deferred:** when amendments amend each other *while both are pending* (perfecting amendments to substitute amendments, before either is adopted — no new BillVersion has been created yet), the BillVersion-intermediate model doesn't capture the direct relationship. usa-fed-api can add `Amendment.amends_amendment_id` if this becomes load-bearing for federal queries.
10. **`VoteEvent.subject_type` enum expansion.** Add `nomination` and `treaty` values, even if WA never uses them. Federal nominations and treaty ratifications are votes-with-subjects that we have nowhere to put. **Defer until `usa-fed-api` is being built.**

**Status update (v1.3, 2026-06-01):** ✅ LANDED for `nomination` (NOT deferred — user noted WA Senate confirms gubernatorial appointments, structurally identical to federal Senate nominations). `subject_type='nomination'` added to the vocab; when used, `subject_id` points to the nominee Person until a dedicated Nomination / Appointment entity lands in P1b. Adapter writes appointment text (role being filled, appointing executive) to `motion_description`. ⏸️ `treaty` stays deferred to usa-fed-api — WA has no treaty-equivalent.
11. **`VoteEvent.outcome` vocab expansion.** Federal vote categories surface `cloture_failed`, `recommit_passed`, etc. — outcomes that don't cleanly map to `passed`/`failed`. Two options: (a) accept the lossy collapse, lean on `category` to disambiguate; (b) expand `outcome` to include `cloture_passed` / `cloture_failed` / `tabled` / `recommitted` / `motion_succeeded` / `motion_failed`. **Lean toward (a)** — keep `outcome` binary, let `category` carry the procedural verb.

**Status update (2026-06-01):** ✅ User agreed with recommendation (a). `outcome` stays at the 6-value vocab (`passed` / `failed` / `tabled` / `withdrawn` / `inconclusive` / `other`); the new `category` column (OQ2) carries the procedural verb. No schema change.
12. **(Same as 2 — already counted as high-impact.)**
13. **Vote-choice vocab: add `guilty` and `not_guilty`.** Senate impeachment votes are rare but uniquely meaningful. Adding 2 values to `PersonVote.vote` and `VoteCount.count_type` enums is cheap. Optionally add `aye` and `no` distinct from `yea` and `nay` to preserve House Aye/No procedural distinction; **lean against** — collapse is acceptable.

**Status update (2026-06-01):** ⏸️ DEFERRED until federal data ingestion. User noted none of these need to be added until usa-fed-api pulls in federal data. WA doesn't have impeachment votes (no analog) and doesn't use Aye/No distinct from Yea/Nay. Revisit when the federal adapter is built.
14. **Add `Bill.classification: text(32) nullable`** following OCD's 24-value semantic enum (`bill`, `resolution`, `joint resolution`, `concurrent resolution`, `simple resolution`, ...) **separate from** the prefix-encoded `bill_type` we already have. P0 delta §"Field addition §1" flagged. Federal `bill_type` of `hr` is *also* semantically `bill`; `hjres` is *also* `joint resolution`. **Adapter computes** the classification from `bill_type`.

**Status update (v1.3, 2026-06-01):** ✅ LANDED. `Bill.classification: text(32) nullable` added. Migration `20260601_bill_class_sponsored_at`. Round-trip test exercises `HJM` → `memorial` mapping.
15. **Adapter pattern: identity-before-work-product ingestion ordering.** Not a schema revision but worth documenting in the v1 spec.

**Status update (2026-06-01):** ✅ Agreed. Documented in the federal `usa-fed-adapter-legislature` blueprint (see "Indirect-provider adapter notes" above): identity (congress-legislators YAML) ingests *before* work-product (uscongress JSON) so Person FK resolution succeeds when bills/votes reference bioguide IDs. Same pattern applies to WA (WSL members ingest before WSL bills); the principle is universal.

## Cross-references

- **Hybrid IA v0:** [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)
- **P0 multi-state IA delta:** [`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`](../research/2026-05-26-multi-state-legislative-ia-delta.md)
- **MVP architecture spec:** [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md)
- **`unitedstates/congress`:** <https://github.com/unitedstates/congress> — bill/amendment/vote/committee-meeting scrapers, JSON data tree
- **`unitedstates/congress-legislators`:** <https://github.com/unitedstates/congress-legislators> — member/committee/membership YAML
- **Bill JSON wiki:** <https://github.com/unitedstates/congress/wiki/bills>
- **Vote JSON wiki:** <https://github.com/unitedstates/congress/wiki/votes>
- **Amendment JSON wiki:** <https://github.com/unitedstates/congress/wiki/amendments>
- **OCD vocab reference:** `openstates/openstates-core` → `openstates/data/common.py`
- **Bioguide:** <https://bioguide.congress.gov> — Biographical Directory of the U.S. Congress (the canonical ID-issuing authority)
