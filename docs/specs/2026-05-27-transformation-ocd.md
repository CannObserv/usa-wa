# Transformation — hybrid legislative IA v0 ↔ Open Civic Data (OpenStates)

- **Date:** 2026-05-27
- **Status:** draft (feeds hybrid IA v1 revisions; see step 3 of P0.5 plan)
- **Scope:** Every canonical entity defined in [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md). Counterpart schema is the OpenStates / Open Civic Data (OCD) Django models as defined in [`openstates/openstates-core`](https://github.com/openstates/openstates-core) `openstates/data/models/*.py` and the controlled vocabularies in `openstates/data/common.py`.
- **Tracks:** [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3). Plan: [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md) step 2.

## Why this exists

Dual purpose. **(a) Completeness check** — exercising hybrid IA v0 against OCD proves the v0 shape can absorb a real, pressure-tested multi-state schema without losing semantically meaningful fields. Where it can't, we get a numbered IA-v1 revision request (§8) rather than a surprise during P1a coding. **(b) Indirect-provider adapter blueprint** — if WSL SOAP is rate-limited, down, or rotates IDs, an `usa_openstates` adapter could populate `canonical.*` from OpenStates' JSON dumps using exactly the inbound `ocd → our` direction of this spec. The lossy directions in §6 are the same gaps that adapter would need primary-source corroboration for.

## Schema-level orientation

The two schemas occupy the same conceptual space but partition responsibilities differently.

- **Identity.** OCD splits identity into a four-entity graph: `Person` (durable individual) ⇄ `Membership` (Person × Organization × Post × Period) ⇄ `Organization` (any group) ⇄ `Post` (named seat within an Organization, optionally tied to a `Division` for district geography). Our IA also runs four entities but with different load-bearing: `Person`, `Organization`, `Role` (named slot within an Organization, no Division), `Assignment` (Person × Role × Period). **OCD's `Post` ≈ our `Role`** with the distinction that `Post.division` is a first-class geography FK and `Post.maximum_memberships` constrains seat count. **OCD's `Membership` ≈ our `Assignment`**, both Person × seat × period. Both schemas allow unresolved-name memberships/sponsorships (OCD via `RelatedEntityBase.name + entity_type`; we via raw text columns on PDC entities — but **not** on identity entities, which is a v1 revision candidate).
- **Sessions.** Direct correspondence. Both treat `LegislativeSession` as a first-class entity. OCD identifier ≈ our slug. OCD `classification` ∈ {`primary`, `special`}; ours adds `sine_die` / `extraordinary` / `other`. Our `biennium_label` and `is_active` denorms have no OCD analog (OCD has `active` bool; no biennium concept).
- **Bills.** Direct correspondence on identifier/session/title shape. OCD's classification array (24-value `BILL_CLASSIFICATIONS`) is **richer than our scalar `bill_type`** — we lose the ability to mark a bill as simultaneously "resolution" + "concurrent resolution" + "appropriation". OCD has no `current_status` / `current_step` (computed from actions); we denormalize. OCD models multiple titles (`BillTitle`, `BillAbstract`) as 1:N children; we collapse to `title` + `short_description`.
- **Votes.** Direct correspondence in shape: both have a `VoteEvent` + `VoteCount` + `PersonVote` triple. **Polymorphic subject diverges:** our `subject_type` ∈ {bill, amendment, motion} lets a vote be on an amendment directly; OCD ties `VoteEvent` to a single nullable `Bill` FK plus a free-text `motion_text`, and votes on amendments are modeled by VoteEvent.bill = the parent bill with `motion_text` describing the amendment. Our shape is finer-grained at the cost of a polymorphic FK pattern OCD avoids.
- **Amendments.** **Asymmetric.** Our v0 makes `Amendment` first-class (own table, own sponsor, own status). OCD does **not** model amendments as a table — they appear only as `BillAction` rows with `classification` in the `amendment-*` family. This is the largest lossy direction in `our → ocd`.
- **Statutes & PDC.** OCD models neither. These clusters are usa-wa-specific and round-trip vacuously through OCD (any inbound `ocd → our` adapter contributes zero data here).

The high-level finding: shapes are equivalent at the bill/session/vote/identity level, modestly divergent in granularity (OCD richer at sponsorship-polymorphism and action-classification; we richer at amendments and vote-subject polymorphism), and orthogonal at the statute/PDC level.

## Per-entity correspondence

### Identity cluster

#### `canonical.persons` ↔ OCD `Person` (+ `PersonName`, `PersonIdentifier`)

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` (ULID) | `id` (OCDID; e.g., `ocd-person/uuid`) | ↔ | derive | OCD ID is content-addressed UUID with `ocd-person/` prefix; ours is ULID. Adapters maintain bidirectional mapping table. |
| `jurisdiction_id` | (derived from `current_jurisdiction_id` and most-recent `Membership.organization.jurisdiction`) | ↔ | derive | OCD Person has no direct jurisdiction FK; computed via `Person.current_jurisdiction` (denorm) and Memberships. **our → ocd**: emit `current_jurisdiction = abbr_to_jid('wa')`. **ocd → our**: read `current_jurisdiction.id`; map `ocd-jurisdiction/country:us/state:wa/government` → `usa-wa`. |
| `source`, `source_id` | `Person.sources` (JSONField list) | ↔ | merge/split | OCD stores `[{url, note}]` array; we project our `(source, source_id, primary_source_id)` into one entry. **lossy ocd → our** when OCD has many source URLs and we keep only one. |
| `name_full` | `Person.name` | ↔ | literal copy | Direct. |
| `name_first` | `Person.given_name` | ↔ | literal copy | |
| `name_last` | `Person.family_name` | ↔ | literal copy | |
| `name_middle` | (no field) | our → ocd | **lossy** | OCD has no middle-name column. **our → ocd**: append to `given_name` or drop. |
| `name_suffix` | (no field) | our → ocd | **lossy** | Same. Could be folded into `name`. |
| `name_used` | `PersonName` row with `note='preferred'` | ↔ | split/merge | OCD models alternate names as 1:N `PersonName(name, note, start_date, end_date)`. **our → ocd**: create a `PersonName` when `name_used` differs from `name_full`. **ocd → our**: pick `PersonName` where note contains "preferred" / "used" / "stage". |
| `gender` | `Person.gender` (CharField, free-text) | ↔ | literal copy | Both unenforced free-text. |
| `birth_year` | `Person.birth_date` (`YYYY[-MM[-DD]]`) | ↔ | derive | **our → ocd**: emit `f"{birth_year}"`. **ocd → our**: parse year prefix. **lossy ocd → our** when OCD has month/day — we discard for privacy. |
| `powermap_person_id` | (no equivalent) | our → ocd | **lossy** | OCD has no power-map awareness. Could be projected into a `PersonIdentifier(scheme='powermap', identifier=ulid)` row, but OCD doesn't define `powermap` as a recognized scheme. |
| `current_district` | (derived from `Person.current_role['district']` JSONField or `Membership.post.label`) | ↔ | derive | OCD's denormalization is `Person.current_role` JSONB (e.g., `{"chamber": "upper", "district": "21"}`). **our → ocd**: emit `current_role['district']`. **ocd → our**: read same. |
| (no field) | `Person.image` (URL) | ocd → our | **lossy** | We don't store profile images. |
| (no field) | `Person.email` | ocd → our | **lossy** | No contact-info storage. |
| (no field) | `Person.biography` (TextField) | ocd → our | **lossy** | No bio column. |
| (no field) | `Person.death_date` | ocd → our | **lossy** | |
| (no field) | `Person.primary_party` (denorm) | ocd → our | derive | We compute from Assignment-to-party-org rather than denormalize. |
| (no field) | `PersonIdentifier(scheme, identifier)` 1:N | ocd → our | **lossy** | OCD lets a Person carry N external IDs (bioguide, ftm_eid, votesmart…). We carry only `powermap_person_id` plus `(source, source_id)`. **Revision candidate** — see §8 #3. |
| (no field) | `PersonOffice` 1:N | ocd → our | **lossy** | District / capitol office addresses. Not modeled. |
| (no field) | `PersonLink`, `PersonSource` 1:N | ocd → our | **lossy** | URL bags. |
| `primary_source_id`, `last_fetched_at`, `last_fetch_event_id` | (no direct equivalents; partial via `OCDBase.created_at`/`updated_at` + `Person.sources`) | our → ocd | **lossy** | Provenance pointers don't survive a round-trip through OCD's flat `sources` JSON. |

#### `canonical.organizations` ↔ OCD `Organization`

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | `id` (`ocd-organization/uuid`) | ↔ | derive | |
| `name` | `name` | ↔ | literal copy | |
| `short_name` | (no direct field; OCD `other_names` JSONB list) | ↔ | merge/split | **our → ocd**: append `{name: short_name, note: 'short'}` to `other_names`. |
| `org_type` | `classification` ∈ `ORGANIZATION_CLASSIFICATIONS` | ↔ | value-mapping | See vocab §5. Our 10-value enum maps to OCD's 7-value enum imperfectly (see §6). |
| `parent_organization_id` | `parent` FK self | ↔ | literal copy | Identical pattern. |
| `powermap_organization_id` | (no equivalent) | our → ocd | **lossy** | Same situation as `powermap_person_id`. |
| `jurisdiction_id` | `jurisdiction` FK | ↔ | derive | Map `usa-wa` ↔ `ocd-jurisdiction/country:us/state:wa/government`. |
| `source`, `source_id` | `Organization.sources` JSONB list | ↔ | merge/split | Same pattern as Person. |
| (no field) | `Organization.links` JSONB list | ocd → our | **lossy** | URL bag. |

#### `canonical.roles` ↔ OCD `Post`

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | `id` (`ocd-post/uuid`) | ↔ | derive | |
| `organization_id` | `organization` FK | ↔ | literal copy | |
| `name` | `label` | ↔ | literal copy | OCD `label` is the human-readable seat name (e.g., "Senator, District 21"); our `name` is the slot name. Where OCD encodes district into the label, we currently don't (district lives on Person). **Revision candidate** — see §8 #1. |
| `role_type` | `Post.role` (free-text CharField, e.g., "Senator", "Chair") | ↔ | value-mapping | OCD's `Post.role` is freetext describing the function; our `role_type` is a 7-value enum. Loose mapping. |
| (no field) | `Post.division` FK → `Division` | ocd → our | **lossy** | OCD ties seats to geographic divisions (`ocd-division/country:us/state:wa/sldu:21`). We have `Person.current_district` instead — losing the per-seat geography. |
| (no field) | `Post.maximum_memberships` | ocd → our | **lossy** | Seat-count cap. We don't enforce. |

#### `canonical.assignments` ↔ OCD `Membership`

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | `id` (`ocd-membership/uuid`) | ↔ | derive | |
| `person_id` | `person` FK | ↔ | literal copy | |
| `role_id` | `post` FK (+ `organization` FK, denormalized) | ↔ | derive | OCD has both `post` and `organization` on `Membership`; ours derives org from `role.organization_id`. |
| `valid_from` | `start_date` (`YYYY[-MM[-DD]]` string) | ↔ | literal copy | OCD permits partial dates; we require full date — **lossy ocd → our** when OCD emits `"2025"` and we need `2025-01-01`. |
| `valid_to` | `end_date` | ↔ | literal copy | OCD uses `""` for "current"; we use `NULL`. |
| `is_active` | (computed; OCD compares dates at query time) | our → ocd | derive | OCD has no denormalized active flag on Membership — only on Person via `current_role`. |
| (no field) | `Membership.role` (CharField, free-text role description) | ocd → our | **lossy** | OCD's `Membership.role` ("Chair", "Vice Chair") is a *per-assignment* role label distinct from the Post. We collapse this onto `Role.name`. |
| (no field) | `Membership.person_name` (fallback when `person` is unresolved) | ocd → our | **lossy** | We require `person_id NOT NULL`; OCD allows unresolved-name memberships. **Revision candidate** — see §8 #2. |

### Session cluster

#### `canonical.legislative_sessions` ↔ OCD `LegislativeSession`

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` (ULID) | `id` (UUID; `RelatedBase`) | ↔ | derive | OCD `LegislativeSession` is a `RelatedBase` (UUID PK), not an `OCDBase` — no `ocd-…` prefix. |
| `slug` | `identifier` | ↔ | value-mapping | OCD uses `"2025"`, `"2025s1"`, etc.; we prefix jurisdiction (`usa-wa-2025`, `usa-wa-2025-special-1`). **our → ocd**: strip `usa-wa-` prefix and normalize special-N → s-N. **ocd → our**: prepend `usa-wa-` and normalize `s1` → `special-1`. |
| `name` | `name` | ↔ | literal copy | |
| `classification` | `classification` ∈ {`primary`, `special`} | ↔ | value-mapping | We have 5 values, OCD has 2. **our → ocd**: collapse `regular` → `primary`; `extraordinary` / `sine_die` / `other` → `special`. **lossy ocd → our**: we re-enrich from naming heuristics or default to `regular`. |
| `start_date` | `start_date` (`YYYY[-MM[-DD]]`) | ↔ | literal copy | |
| `end_date` | `end_date` | ↔ | literal copy | |
| `is_active` | `active` | ↔ | literal copy | |
| `biennium_label` | (no equivalent) | our → ocd | **lossy** | WA-specific ("2025-26"). OCD has no biennium concept. |
| `jurisdiction_id` | `jurisdiction` FK | ↔ | derive | |

### Bill cluster

#### `canonical.bills` ↔ OCD `Bill` (+ `BillAbstract`, `BillTitle`, `BillIdentifier`)

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | `id` (`ocd-bill/uuid`) | ↔ | derive | |
| `legislative_session_id` | `legislative_session` FK | ↔ | literal copy | |
| `chamber` | `from_organization` FK → Organization with classification ∈ {`upper`, `lower`} | ↔ | value-mapping | OCD models chamber as an FK to a chamber-Organization, not an enum. **our → ocd**: resolve `chamber='senate'` → the `upper`-classified Organization for `usa-wa`. **ocd → our**: read `from_organization.classification` and map `upper`→`senate`, `lower`→`house`, `legislature`→`unicameral`. |
| `number` | (parsed from `identifier`, e.g., `"HB 1234"` → 1234) | ↔ | split | OCD stores the full identifier as one string; we split `bill_type` + `number`. |
| `bill_type` | (parsed from `identifier` prefix) **AND** `classification` (ArrayField of `BILL_CLASSIFICATIONS`) | ↔ | merge/split | OCD has two complementary fields: the textual prefix in `identifier` ("HB") **and** the semantic array in `classification` (`["bill"]`). Our scalar `bill_type` collapses both. **lossy ocd → our** when OCD's array has 2+ values (e.g., `["resolution", "concurrent resolution"]`). |
| `title` | `title` (short form) | ↔ | literal copy | **Aligned** post-v0 swap. |
| `short_description` | `BillAbstract.abstract` (1:N) — pick the canonical one | ↔ | merge/split | OCD models full descriptions / abstracts as a child table. **our → ocd**: create one `BillAbstract` row with `note=''`. **ocd → our**: select the first `BillAbstract` (or concatenate if multiple). **lossy ocd → our** when OCD has many abstracts (e.g., introduced version + amended version). |
| `current_status` | (no field; computed from latest `BillAction.classification`) | ↔ | derive | OCD denorms only `latest_action_description` (free text). **our → ocd**: emit our status as a value in `latest_action_description`. **ocd → our**: rebuild from action log (see §5.1). |
| `current_step` | (no equivalent) | our → ocd | **lossy** | OCD has no step concept. |
| `introduced_at` | (derived from `BillAction` with classification=`introduction`; also denorm `first_action_date`) | ↔ | derive | |
| `current_text` | (derived from `BillVersion`+`BillVersionLink` chain marked current) | ↔ | derive | OCD doesn't denormalize current text. **our → ocd** is **lossy** — current_text is a snapshot blob, OCD wants typed versions. |
| `source`, `source_id` | `Bill.sources` JSONB list | ↔ | merge/split | |
| (no field) | `BillTitle` (1:N alternate titles) | ocd → our | **lossy** | We collapse to one title. **Revision candidate** — §8 #5. |
| (no field) | `BillIdentifier` (1:N alternate IDs) | ocd → our | **lossy** | Our `(jurisdiction_id, source, source_id)` unique handles primary ID only. **Revision candidate** — §8 #6. |
| (no field) | `Bill.subject` (ArrayField) | ocd → our | **lossy** | No subject tagging. **Revision candidate** — §8 #7. |
| (no field) | `Bill.citations` (JSONField) | ocd → our | **lossy** | Inline citation refs. |
| (no field) | `Bill.first_action_date`, `latest_action_date`, `latest_action_description`, `latest_passage_date` (computed denorms) | ocd → our | derive | We rebuild via queries. |
| (no field) | `BillSource` (1:N source URLs) | ocd → our | **lossy** | URL bag; flattened to single `(source, source_id)`. |

#### `canonical.bill_sponsorships` ↔ OCD `BillSponsorship`

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | (UUID) | ↔ | derive | OCD uses `RelatedBase` UUID. |
| `bill_id` | `bill` FK | ↔ | literal copy | |
| `person_id` | `RelatedEntityBase.person` FK (nullable) | ↔ | literal copy | OCD's `BillSponsorship` extends `RelatedEntityBase`; if `entity_type='person'`, `person_id` is set. |
| `organization_id` | `RelatedEntityBase.organization` FK (nullable) | ↔ | literal copy | Same pattern for committee-sponsored. |
| `role` | `classification` (free CharField) **AND** `primary` (bool) | ↔ | value-mapping | OCD denorms primary-vs-not as a bool. **our → ocd**: `role='primary'` → `(primary=True, classification='primary')`; `role='co'` → `(primary=False, classification='cosponsor')`; `role='joint'` → `(primary=False, classification='joint')`; `role='generic'` → `(primary=False, classification='sponsor')`. **ocd → our**: invert (free-text `classification` may need fuzzy normalization). See §5.2. |
| `sponsor_order` | (no equivalent) | our → ocd | **lossy** | OCD does not preserve ordering on sponsorships. Display order would be lost on round-trip through OCD. |
| `withdrawn_at` | (no equivalent) | our → ocd | **lossy** | OCD has no withdrawal lifecycle on `BillSponsorship`. |
| (no field) | `RelatedEntityBase.name` (free-text fallback) | ocd → our | **lossy** | OCD permits unresolved-name sponsorships; we require either `person_id` or `organization_id` non-null. **Revision candidate** — §8 #2. |
| (no field) | `RelatedEntityBase.entity_type` | ocd → our | derive | Derived from which of `person_id`/`organization_id` is set. |

#### `canonical.bill_actions` ↔ OCD `BillAction` (+ `BillActionRelatedEntity`)

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | (UUID) | ↔ | derive | |
| `bill_id` | `bill` FK | ↔ | literal copy | |
| `action_at` | `date` (`YYYY-MM-DD HH:MM:SS+HH:MM` string) | ↔ | literal copy | OCD uses string; we use `timestamptz`. Parse / format. |
| `chamber` | (derived from `organization.classification`) | ↔ | derive | OCD links to `organization`, not a chamber enum. |
| `acting_organization_id` | `organization` FK | ↔ | literal copy | |
| `action_type` (source-vocab text) | `classification` (ArrayField of `BILL_ACTION_CLASSIFICATIONS`, 40 values) | ↔ | value-mapping | See §5.1. **lossy ocd → our** when OCD's array has 2+ values — we'd need to pick a primary classification or promote our column to an array. **Revision candidate** — §8 #4. |
| `description` | `description` | ↔ | literal copy | |
| (no field) | `BillAction.order` (PositiveIntegerField) | ocd → our | **lossy** | OCD has explicit display order independent of timestamp. We rely on `action_at` ordering. **Revision candidate** — §8 #8. |
| (no field) | `BillActionRelatedEntity` (1:N polymorphic links to Person/Org/other-Bill cited by the action) | ocd → our | **lossy** | OCD records "Referred to House Rules and Appropriations" as two related-entity rows on the action. We have only `acting_organization_id`, missing the *targets* of an action. **Revision candidate** — §8 #9. |

#### `canonical.bill_versions` ↔ OCD `BillVersion` (+ `BillVersionLink`)

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | (UUID) | ↔ | derive | |
| `bill_id` | `bill` FK | ↔ | literal copy | |
| `version_type` | `BillVersion.classification` ∈ `BILL_VERSION_CLASSIFICATIONS` (7-value enum: `''`, `filed`, `introduced`, `amendment`, `substituted`, `enrolled`, `became-law`) | ↔ | value-mapping | Our enum is broader (`original` / `substitute` / `engrossed` / `first_engrossed` / `enrolled` / etc.). Mapping is mostly clean but **lossy our → ocd** for engrossment variants (OCD lacks `engrossed` distinction). |
| `version_at` | `BillVersion.date` (`YYYY[-MM[-DD]]`) | ↔ | literal copy | |
| `is_current` | (no equivalent) | our → ocd | **lossy** | OCD has no per-version current flag. Current version is inferred from latest by date. |
| (no field) | `BillVersionLink` (1:N: `media_type` + `url`) | ocd → our | **lossy** | OCD models PDF vs HTML vs DOCX of the same version as siblings. We have one text blob. **Revision candidate** — §8 #10. |
| (no field) | `BillVersion.note` (free text) | ocd → our | **lossy** | |
| (no field) | `SearchableBill.raw_text` (full-text-search blob) | ocd → our | **lossy** | OCD's FTS extract — not part of our v0. |

#### `canonical.amendments` ↔ (no direct OCD entity)

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | (synthetic — no OCD entity) | our → ocd | **lossy** | OCD does not model amendments as first-class. |
| `bill_id` | (projected onto `BillAction.bill` with `classification=['amendment-introduction']`) | our → ocd | derive | An Amendment becomes ≥1 BillAction rows. |
| `label` | `BillAction.description` (free text) | our → ocd | merge | Label lives inside description string. |
| `amendment_text` | `BillVersion` with `classification='amendment'` (loosely) | our → ocd | merge | OCD can carry an amendment's text via a `BillVersion` row, **but** that conflates with bill-version semantics. The mapping is awkward. |
| `sponsor_person_id` | (no equivalent) | our → ocd | **lossy** | Amendment sponsorship has no OCD representation. |
| `sponsor_organization_id` | (no equivalent) | our → ocd | **lossy** | Same. |
| `status` ∈ {offered/adopted/rejected/withdrawn/pending/tabled} | (projected onto `BillAction.classification` ∈ {`amendment-introduction`, `amendment-passage`, `amendment-failure`, `amendment-withdrawal`, `amendment-deferral`}) | our → ocd | value-mapping | See §5.1 for the action-class mapping. **status=pending has no OCD action class** — must be omitted. |
| `offered_at` / `adopted_at` / `rejected_at` / `withdrawn_at` | (projected onto the corresponding `BillAction.date` rows) | our → ocd | split | Each lifecycle timestamp becomes a separate BillAction. |

**`ocd → our` is severely degraded** for this entity: OCD adapter reading an OCD bill can construct partial Amendments by scanning `BillAction.classification` for amendment-family entries, but it can't recover sponsorship or full text reliably. The right rendering for an indirect-provider adapter is: synthesize an Amendment row per `amendment-introduction` action; mark `status` from the latest matching lifecycle action; leave sponsor fields NULL.

### Vote cluster

#### `canonical.vote_events` ↔ OCD `VoteEvent`

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | `id` (`ocd-vote/uuid`) | ↔ | derive | |
| `subject_type` | (derived: `bill` if `bill_id` set else `motion`) | ↔ | derive | **OCD cannot represent `subject_type=amendment`** — it only has nullable `bill` FK. **lossy our → ocd**: an amendment-vote in our model becomes an OCD vote with `bill = amendment.bill_id` and `motion_text = amendment.label + " vote"`. |
| `subject_id` | (derived from `bill_id`) | ↔ | derive | |
| `bill_id` | `bill` FK (nullable) | ↔ | literal copy | |
| `amendment_id` | (no field) | our → ocd | **lossy** | See subject_type note. |
| `motion_description` | `motion_text` (TextField, NOT NULL but `''` allowed) | ↔ | literal copy | |
| `context_type` | (derived from `organization.classification` — committee → `committee`, upper/lower → `floor`) | ↔ | derive | |
| `context_organization_id` | `organization` FK | ↔ | literal copy | |
| `chamber` | (derived from `organization.classification`) | ↔ | derive | |
| `event_at` | `start_date` | ↔ | literal copy | |
| `outcome` ∈ {passed/failed/tabled/withdrawn/inconclusive/other} | `result` ∈ {`pass`, `fail`} | ↔ | value-mapping | OCD has only 2 outcomes. **lossy our → ocd**: `tabled` / `withdrawn` / `inconclusive` / `other` all collapse to either `fail` or are dropped. **lossy ocd → our** is non-lossy because we only widen the range. See §5.3. |
| (no field) | `VoteEvent.motion_classification` (ArrayField, `VOTE_CLASSIFICATIONS`) | ocd → our | **lossy** | OCD classifies the *motion* (passage / amendment / committee-passage / veto / veto-override / reading-1 / reading-3); we infer from subject + context. **Revision candidate** — §8 #11. |
| (no field) | `VoteEvent.bill_action` FK (links a vote to the originating action) | ocd → our | **lossy** | OCD lets a `VoteEvent` cite the specific `BillAction` it resulted from. We have no such link. |
| (no field) | `VoteEvent.legislative_session` FK | ocd → our | derive | OCD denormalizes session on the vote. We derive via `bill.legislative_session_id`. |
| (no field) | `VoteEvent.order` (display order) | ocd → our | **lossy** | |
| (no field) | `VoteSource` (1:N URLs) | ocd → our | **lossy** | |
| `source`, `source_id` | `VoteEvent.dedupe_key` + `VoteEvent.identifier` | ↔ | merge/split | OCD uses `dedupe_key` for upsert (~ our `source_id`) and `identifier` for human-readable label. |

#### `canonical.vote_counts` ↔ OCD `VoteCount`

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | (UUID) | ↔ | derive | |
| `vote_event_id` | `vote_event` FK | ↔ | literal copy | |
| `count_type` ∈ {yea/nay/excused/absent/present_not_voting/other} | `option` ∈ `VOTE_OPTIONS` (8: `yes`/`no`/`absent`/`abstain`/`not voting`/`paired`/`excused`/`other`) | ↔ | value-mapping | See §5.3. `present_not_voting` ↔ `not voting`. No `paired` in our vocab — **lossy ocd → our**. |
| `value` | `value` | ↔ | literal copy | |

#### `canonical.person_votes` ↔ OCD `PersonVote`

| our field | OCD field | direction | transform | notes |
|---|---|---|---|---|
| `id` | (UUID) | ↔ | derive | |
| `vote_event_id` | `vote_event` FK | ↔ | literal copy | |
| `person_id` | `voter` FK (nullable) | ↔ | literal copy | OCD allows unresolved voter (`voter` NULL + `voter_name` set); we require `person_id NOT NULL`. **lossy ocd → our** when OCD has unresolved voters. **Revision candidate** — §8 #2. |
| `vote` ∈ {yea/nay/abstain/excused/absent/present_not_voting} | `option` ∈ `VOTE_OPTIONS` | ↔ | value-mapping | See §5.3. |
| (no field) | `PersonVote.voter_name` (fallback name) | ocd → our | **lossy** | |
| (no field) | `PersonVote.note` | ocd → our | **lossy** | |

### Statute cluster

OCD does not model statute corpora. All five statute tables (`StatuteCode`, `StatuteTitle`, `StatuteChapter`, `StatuteSection`, `BillStatuteChange`) have **no OCD correspondent**. An OCD-based indirect-provider adapter contributes zero data here. `our → ocd` is non-applicable; `ocd → our` is non-applicable.

The one near-miss is OCD's `BILL_DOCUMENT_CLASSIFICATIONS` value `law`, which marks an enacted-law document attached to a passed bill — this is closer to "the bill became a law" than to a statute section. No round-trip.

### PDC cluster

OCD does not model campaign finance or lobbying. `canonical.lobbying_activities`, `canonical.lobbying_positions`, `canonical.contributions` have **no OCD correspondent**. Same outcome as statutes — orthogonal scope.

## Vocabulary alignment

### 5.1 Bill action types

Our `BillAction.action_type` is currently a free-text column carrying WSL's source vocabulary. OCD's `BillAction.classification` is an array of 40 enforced values from `BILL_ACTION_CLASSIFICATIONS`. WSL's exact vocabulary isn't yet documented in this repo (P1a will produce it), so the table below uses likely WSL action labels inferred from the WA Legislature SOAP / `app.leg.wa.gov` action-history language and proposes the OCD class each maps to. Where multiple OCD classes apply, the array allows multiplicity; **our scalar column does not, which is a key revision candidate (§8 #4).**

| WSL-flavored action label (anticipated) | OCD classification(s) | Direction notes |
|---|---|---|
| "Prefiled for introduction" | `filing` | ↔ |
| "First reading, referred to [Committee]" | `reading-1`, `referral-committee` | ↔ multi-class |
| "Read first time" | `reading-1` | ↔ |
| "Public hearing in [Committee]" | `hearing-held` | ↔ |
| "Executive action taken in [Committee]" | `work-session` | OCD's `work-session` is the closest; semantic match imperfect |
| "Reported by [Committee] / Do pass" | `committee-passage-favorable`, `reported-out-of-committee` | ↔ multi-class |
| "Reported by [Committee] / Without recommendation" | `committee-passage`, `reported-out-of-committee` | ↔ multi-class |
| "Reported by [Committee] / Do not pass" | `committee-passage-unfavorable`, `reported-out-of-committee` | ↔ multi-class |
| "Referred to Rules" | `referral-committee` | ↔ |
| "Placed on second reading" | (no clean class; `reading-2` is closest) | ↔ |
| "Second reading" / "Floor amendments offered" | `reading-2`, possibly `amendment-introduction` | ↔ multi-class |
| "Floor amendment adopted" | `amendment-passage` | ↔ |
| "Floor amendment rejected" | `amendment-failure` | ↔ |
| "Floor amendment withdrawn" | `amendment-withdrawal` | ↔ |
| "Third reading, final passage" | `reading-3`, `passage` | ↔ multi-class |
| "Failed final passage" | `failure` | ↔ |
| "Speaker / President signed" | (no class; OCD has `enrolled` for the chamber-acceptance step) | **lossy** — closest is `enrolled` but semantically the chamber-signing step is more specific |
| "Delivered to Governor" | `executive-receipt` | ↔ |
| "Governor signed" | `executive-signature` | ↔ |
| "Governor vetoed" | `executive-veto` | ↔ |
| "Partial veto" | `executive-veto-line-item` | ↔ |
| "Veto overridden" | `veto-override-passage` | ↔ |
| "Effective date" / "Became Chapter X, Laws of YYYY" | `became-law` | ↔ |
| "By resolution, returned to [Chamber] / By resolution, reintroduced" | `carried-over` | ↔ |
| "Withdrawn from committee" / "Withdrawn from further consideration" | `withdrawal` | ↔ |
| "Substitute adopted" | `substitution` | ↔ |
| "Concurrence requested" / "Senate concurred in House amendments" | `concurrence` | ↔ |

**WSL labels with no clean OCD class** (anticipated, requires WSL-vocab confirmation in P1a):

- WSL's per-chamber engrossment markers ("Engrossed", "First engrossed substitute") — OCD's `BillVersion.classification` handles this on the version row, not the action. **Our `BillVersion.version_type` covers it on our side.**
- Calendar-only events ("Placed on third reading by suspension of rules"). OCD's `BILL_ACTION_CLASSIFICATIONS` has no calendar/queue verb; closest is `deferral` (semantically off).
- "Filed with Secretary of State" — distinct from `enrolled` and `became-law`; no clean OCD class.

### 5.2 Sponsor role vocabulary

Our `BillSponsorship.role` ∈ {`primary`, `co`, `joint`, `generic`} ↔ OCD's `BillSponsorship.(primary: bool, classification: str-free)`:

| our role | OCD `primary` | OCD `classification` (free text; convention) | notes |
|---|---|---|---|
| `primary` | `True` | `"primary"` | Canonical. |
| `co` | `False` | `"cosponsor"` | Some OCD adapters emit `"co"` instead — both should be accepted on `ocd → our`. |
| `joint` | `False` | `"joint"` | Used for joint-author models (some Southern states). |
| `generic` | `False` | `"sponsor"` | OCD's catch-all; the empty/unspecified case. |

**`ocd → our` normalization rule:** match `classification` case-insensitively against the candidate list `{primary, prime, lead, main}` → our `primary`; `{cosponsor, co-sponsor, co}` → `co`; `{joint, jointauthor}` → `joint`; everything else (including empty) → `generic`. If `primary=True` but classification doesn't fit, force our `role='primary'` (OCD's bool is authoritative).

**Lossy round-trip note:** OCD permits arbitrary free-text classifications (e.g., `"floor sponsor"`, `"requestor"`). On `ocd → our` we collapse these to `generic`, losing the distinction. **Revision candidate — §8 #12.**

### 5.3 Vote outcome and vote choice

`VoteEvent.outcome` ↔ `VoteEvent.result`:

| our outcome | OCD result | direction notes |
|---|---|---|
| `passed` | `pass` | ↔ |
| `failed` | `fail` | ↔ |
| `tabled` | `fail` | **lossy our → ocd**: tabled is procedural, not strictly a failure; OCD collapses. |
| `withdrawn` | `fail` (typically) or omit the VoteEvent entirely | **lossy our → ocd** |
| `inconclusive` | `fail` (default) | **lossy our → ocd** |
| `other` | `fail` | **lossy our → ocd** |

`PersonVote.vote` ↔ `PersonVote.option` and `VoteCount.count_type` ↔ `VoteCount.option`:

| our (PersonVote) | our (VoteCount) | OCD `VOTE_OPTIONS` | direction notes |
|---|---|---|---|
| `yea` | `yea` | `yes` | ↔ |
| `nay` | `nay` | `no` | ↔ |
| `abstain` | — | `abstain` | OCD has `abstain` for PersonVote; we don't list it on VoteCount. **Revision candidate — §8 #13.** |
| `excused` | `excused` | `excused` | ↔ |
| `absent` | `absent` | `absent` | ↔ |
| `present_not_voting` | `present_not_voting` | `not voting` | ↔ |
| — | `other` | `other` | We expose `other` on VoteCount but not PersonVote. |
| — | — | `paired` | **lossy ocd → our** — no analog. |

**Asymmetric vocab on PersonVote vs VoteCount in our v0:** PersonVote includes `abstain` but VoteCount does not; VoteCount includes `other` but PersonVote does not. This asymmetry is suspect — see §8 #13.

## Lossy directions

Explicit list of fields where a round-trip drops information, grouped by direction. **(L)** = lossy.

**our → ocd (data we hold that OCD cannot represent):**

1. **(L) Amendment as a first-class entity.** Sponsor, status, text are lost. Only lifecycle action timestamps survive via projection onto `BillAction.classification` ∈ `amendment-*`. **Severe.**
2. **(L) `Amendment.status='pending'`.** No OCD action class. Dropped on emit.
3. **(L) `Amendment.sponsor_person_id` / `sponsor_organization_id`.** Lost entirely.
4. **(L) `BillSponsorship.sponsor_order`.** Display ordering is dropped.
5. **(L) `BillSponsorship.withdrawn_at`.** OCD has no withdrawal-lifecycle for sponsorships.
6. **(L) `BillVersion.is_current`.** OCD has no per-version current flag.
7. **(L) `Bill.current_step`.** Source-vocab-text step lost; OCD has no step concept.
8. **(L) `VoteEvent.subject_type='amendment'`.** Collapsed to `bill` FK + free-text motion description.
9. **(L) `VoteEvent.outcome ∈ {tabled, withdrawn, inconclusive, other}`.** Collapsed to `fail`.
10. **(L) `LegislativeSession.classification ∈ {sine_die, extraordinary, other}`.** Collapsed to `special`.
11. **(L) `LegislativeSession.biennium_label`.** WA-specific, no analog.
12. **(L) `powermap_person_id` / `powermap_organization_id`.** No analog in OCD; could be projected into `PersonIdentifier(scheme='powermap')` but not a recognized scheme.
13. **(L) Provenance trio (`primary_source_id`, `last_fetched_at`, `last_fetch_event_id`).** Flattened into OCD's `sources` JSONB list with loss.

**ocd → our (data OCD holds that our v0 cannot represent):**

1. **(L) Multi-classification actions.** `BillAction.classification` array → our scalar `action_type`. **Major** — single passage actions routinely carry 2 classifications. **Revision candidate §8 #4.**
2. **(L) Multi-classification bills.** `Bill.classification` array → our scalar `bill_type`.
3. **(L) `BillAction.order` (explicit display order independent of date).** Lost. **§8 #8.**
4. **(L) `BillActionRelatedEntity` (committee/person/bill targets cited by an action).** Lost. **§8 #9.**
5. **(L) `BillTitle` / `BillAbstract` 1:N children.** We collapse to single `title` + `short_description`. **§8 #5.**
6. **(L) `BillIdentifier` (alternate identifiers).** **§8 #6.**
7. **(L) `Bill.subject` ArrayField.** **§8 #7.**
8. **(L) `BillVersionLink` (mimetype-tagged URL siblings of a version).** **§8 #10.**
9. **(L) `BillVersion.note`.**
10. **(L) `VoteEvent.motion_classification` array.** **§8 #11.**
11. **(L) `VoteEvent.bill_action` FK linking a vote to the originating action.**
12. **(L) `RelatedBill` (companion/replaces/prior-session/related/replaced-by).** We have no inter-bill relationship table. **§8 #14.**
13. **(L) `PersonIdentifier` (1:N external IDs per person — bioguide_id, ftm_eid, votesmart…).** **§8 #3.**
14. **(L) `PersonOffice`, `PersonLink`, `PersonSource`.** Address / URL bags.
15. **(L) `Person.image`, `Person.email`, `Person.biography`, `Person.death_date`.** Profile attributes.
16. **(L) `Post.division` FK** (geographic seat anchor). We have `Person.current_district` denorm. **§8 #1.**
17. **(L) `Post.maximum_memberships`.**
18. **(L) `Membership.person_name` (unresolved-name fallback).** **§8 #2.**
19. **(L) `Membership.role` (per-assignment role label distinct from Post).**
20. **(L) Unresolved-sponsorship name (`RelatedEntityBase.name` with `entity_type=person` but no FK).** **§8 #2.**
21. **(L) Unresolved voter name (`PersonVote.voter_name` without `voter` FK).** **§8 #2.**
22. **(L) `VOTE_OPTION='paired'`.** Lost on round-trip.
23. **(L) `SearchableBill.raw_text` (full-text-search blob).**
24. **(L) `Bill.citations` JSONField.**
25. **(L) OCD's `Event` cluster** (hearings, agenda items, related entities) — we have no Hearing entity yet.

## Indirect-provider adapter notes

A `usa_openstates` adapter — i.e., an adapter that populates our `canonical.*` from OpenStates JSON dumps — is **mechanically buildable** with the inbound (`ocd → our`) direction of this spec, with the following caveats:

**What works cleanly.** Identity (Person/Organization/Membership/Post → Person/Organization/Role/Assignment), Sessions, Bills (core fields), BillSponsorships, BillActions (with vocab normalization), BillVersions (metadata only), VoteEvents, VoteCounts, PersonVotes. The full "describe HB-1234" MVP query is answerable from OpenStates data alone.

**Where OpenStates' WA coverage hurts.** OpenStates' WA scraper coverage of *committee* votes is historically incomplete — they prioritize floor votes. Our `VoteEvent.context_type='committee'` rows would be sparse on round-trip. OpenStates also doesn't reliably normalize action-related entities for WA (the `BillActionRelatedEntity` table is mostly empty for WA), so even fields we *could* populate via §4 would often be unpopulated upstream.

**Entities that still need primary-source corroboration.** Anything in the lossy `ocd → our` list (§6) cannot be reconstructed from OpenStates and must come from WSL primary source:

- **Amendments** as first-class entities — OpenStates doesn't track them. The adapter would synthesize stub Amendments from `amendment-*` action classifications but without sponsor or text.
- **PDC cluster** (lobbying, contributions) — entirely orthogonal to OCD. PDC stays primary-source only.
- **Statute cluster** (RCW) — same orthogonality. RCW stays primary-source only.
- **`BillSponsorship.sponsor_order` and `.withdrawn_at`** — display-ordering and lifecycle would be lost.
- **`VoteEvent` linkage to `BillAction`** — OpenStates carries `bill_action` FK on votes; we don't. We'd be losing the OpenStates linkage too unless we add the field.

**Recommended posture.** Build the adapter as a *fallback* not a *primary* — wire it into the runner with lower confidence (in the citation sense). Use it when WSL SOAP returns errors for ≥N minutes, or in scheduled corroboration runs that diff WSL-derived rows against OpenStates-derived rows to flag inconsistencies. The §8 v1 revisions that matter most for this adapter are #4 (action-class array), #14 (RelatedBill), #5/#6 (multi-title/identifier), and #2 (unresolved-name fallback) — without those, the adapter is significantly degraded.

## Open revisions for hybrid IA v1

Synthesis of every revision candidate surfaced by this transformation. **This is the load-bearing output of step 2.** Each item has a brief rationale, the IA v0 entity affected, and a recommended disposition (`apply` / `defer` / `defer-with-rationale`).

1. **Promote `current_district` from `Person` to `Role`** (or attach `Role` to a sub-Organization with district encoded). OCD's `Post.division` FK is cleaner: the *seat* has a geography, not the person. A senator who changes districts keeps the same `Person`, gets a new `Role` (or `Post`). Currently our shape forces re-emitting `Person.current_district` on every district change, which loses historical district context. **Disposition: apply in v1 — change `Role` to carry `district` (text, nullable) and drop `Person.current_district` (or keep as denorm for query speed but mark derived).** Most consequential identity-cluster fix.

2. **Add unresolved-name fallback columns.** `BillSponsorship.unresolved_name` (text, nullable, only set when both `person_id` and `organization_id` are NULL), `PersonVote.unresolved_voter_name` (text, nullable, only set when `person_id` is NULL), `Assignment.unresolved_person_name` (text, nullable). Mirrors OCD's `RelatedEntityBase` and `PersonVote.voter_name` pattern. Without this, the inbound OpenStates adapter must either skip unresolved rows (data loss) or fail (brittle). **Disposition: apply in v1.** Relax existing NOT-NULL constraints; add CHECK constraints `(person_id IS NOT NULL) OR (unresolved_*_name IS NOT NULL)`.

3. **Promote `powermap_person_id` / `powermap_organization_id` to a generalized external-identifier child table.** New entity `canonical.person_external_ids(person_id, scheme, identifier)` and `canonical.organization_external_ids(org_id, scheme, identifier)`. The `scheme='powermap'` row replaces today's column; new rows can hold `bioguide_id`, `ftm_eid`, `votesmart_id`, `wsl_member_id`, etc. Mirrors OCD `PersonIdentifier`. **Disposition: apply in v1 — but keep the `powermap_*_id` columns as *denormalized* convenience fields for the hot-path FK use case** (they're how power-map writes back; keeping them avoids a join on every read).

4. **Make `BillAction.action_type` a multi-value column.** Two options: (a) promote to `text[]` ArrayField in Postgres, (b) extract to a child table `bill_action_classifications(bill_action_id, classification)`. Either way, the v0 single-string assumption breaks under WA's real action vocabulary — "Third reading, final passage" is multi-class. **Disposition: apply in v1 (recommend option (b) — child table — for cleaner per-class indexing and JOIN ergonomics).** Add a denormalized `primary_classification: text NOT NULL` on `BillAction` for fast `current_status` derivation.

5. **Promote `Bill.title` + `short_description` to a `BillTitle` 1:N child.** OCD models multiple titles (official, short, popular, alternate). WSL emits ≥2 titles for some bills. The collapse loses popular-title ("Climate Commitment Act") on bills that have one. **Disposition: defer to v1.5 / P1b — single title covers 95% of WA bills; promote only when WSL emits the alternate-title field reliably.** Mark in v1 as known constraint.

6. **Add `BillIdentifier` for alternate IDs.** `(bill_id, scheme, identifier)`. Needed if an indirect-provider adapter populates the same Bill (currently blocked by our `(jurisdiction_id, source, source_id)` UNIQUE which forces a different source). **Disposition: defer — `(jurisdiction_id, source, source_id)` uniqueness handles MVP; revisit when multi-source corroboration becomes real.**

7. **Add `Bill.subjects` (text[]).** Subject tagging is universal across OCD/LegiScan/uscongress and present in WSL SOAP. Currently zero support. **Disposition: apply in v1.** Cheap (one column). 1:N table can wait.

8. **Add `BillAction.display_order` (PositiveIntegerField).** Independent of timestamp for the tie-breaker case (two actions same minute). OCD's `BillAction.order` is non-null. **Disposition: apply in v1.** Cheap.

9. **Add `BillActionRelatedEntity` 1:N table.** `(bill_action_id, entity_type, entity_id, name_raw)`. Lets an action cite its target committee(s) / referenced bills / referenced persons. Without this, "Referred to Health and Ways and Means" loses the *which committees* information. **Disposition: defer — the action description already carries the names as free text; structured extraction is a P1b enrichment, not v1.**

10. **Add `BillVersionLink` 1:N table.** `(bill_version_id, media_type, url)`. Today `BillVersion` doesn't carry text at all (we have only metadata + a hypothetical `current_text` on `Bill`). When we promote text to `BillVersion` in P3, multiple-format links per version is the right shape. **Disposition: defer to P3** (BillVersion text storage is already P3-deferred).

11. **Add `VoteEvent.motion_classification` (text[]).** OCD's 7-value enum classifies the *motion* (passage / amendment / committee-passage / reading-1 / reading-3 / veto / veto-override). Today we infer from subject+context, which is brittle (e.g., a reading-3 vote on an amendment is ambiguous). **Disposition: apply in v1.** Add as scalar `motion_classification: text(32) nullable` for now; promote to array if WA forces multi-class.

12. **Drop or widen `BillSponsorship.role` vocab.** OCD's `classification` is free-text — adapters emit `"floor sponsor"`, `"requestor"`, `"author"`, etc. Our 4-value enum can't round-trip those. Two options: (a) keep enum, accept the loss, (b) make `role` a free-text column with a recommended-values convention. **Disposition: defer with rationale — keep the 4-value enum.** The freedom OCD offers is a known data-quality liability; collapsing to 4 normalized values is *correct* for our use case. Document the lossy normalization rule (§5.2) and move on.

13. **Align PersonVote and VoteCount option vocabularies.** Today `PersonVote.vote` has `abstain` but `VoteCount.count_type` doesn't; `VoteCount.count_type` has `other` but `PersonVote.vote` doesn't. **Disposition: apply in v1 — add `abstain` to `VoteCount.count_type`, add `other` to `PersonVote.vote`.** Make them identical 7-value enums: `{yea, nay, abstain, excused, absent, present_not_voting, other}`. Cheap.

14. **Add `canonical.bill_relationships`.** `(bill_id, related_bill_id, relation_type)` with vocab from OCD's `BILL_RELATION_TYPES` (`companion`, `prior-session`, `replaced-by`, `replaces`, `related`). Universal across all three reference schemas in the delta note. Companion bills are real in WA (House/Senate paired bills). **Disposition: apply in v1.** New entity; mechanical to add.

15. **Add `Bill.enacted_as` (text, nullable).** WA's "Chapter X, Laws of YYYY" terminal identifier. OCD has no clean column; uscongress has `enacted_as`. **Disposition: apply in v1.** Cheap.

16. **Add `VoteEvent.originating_bill_action_id` (nullable FK to BillAction).** Lets a vote cite the action that produced it (OCD has `VoteEvent.bill_action` for this). Useful for "the vote on third reading" traceability. **Disposition: defer with rationale — derivable from `(bill_id, event_at)` proximity for the MVP question shape; promote in P1b if traceability becomes a stated requirement.**

17. **Clarify `LegislativeSession.classification` mapping rules.** Document the `regular`→`primary` and `{special,extraordinary,sine_die,other}`→`special` collapse explicitly in the IA spec so consumers know what an OCD adapter would lose. **Disposition: apply in v1 — narrative only, no schema change.**

18. **Remove or downgrade `Bill.current_step`.** OCD has no analog; the field has no clear semantics distinct from `current_status`. Per the delta note's recommendation (already documented but not yet applied). **Disposition: apply in v1 — drop the column.** Reduces ambiguity.

19. **Add a `Bill.current_status_at` (timestamptz, nullable).** When the current status was last updated. OCD has no analog, but uscongress and LegiScan both have it; useful for "is this stale?" answers. **Disposition: apply in v1.** Cheap.

20. **Acknowledge OCD's `Event` (hearing) gap.** We have no Hearing entity in v0; OCD's `Event` cluster is rich (location, agenda items, related entities, media, documents). **Disposition: defer to post-MVP — Hearing was already a planned P3 entity per the MVP spec; no v1 revision needed.** Document the gap in §6 lossy list.

## References

- **OpenStates / Open Civic Data source** (read direct from GitHub `openstates/openstates-core@main`):
  - `openstates/data/common.py` — controlled vocabularies (`BILL_ACTION_CLASSIFICATIONS`, `BILL_CLASSIFICATIONS`, `BILL_RELATION_TYPES`, `BILL_VERSION_CLASSIFICATIONS`, `VOTE_OPTIONS`, `VOTE_RESULTS`, `VOTE_CLASSIFICATIONS`, `SESSION_CLASSIFICATIONS`, `ORGANIZATION_CLASSIFICATIONS`).
  - `openstates/data/models/bill.py` — `Bill`, `BillAbstract`, `BillTitle`, `BillIdentifier`, `BillAction`, `BillActionRelatedEntity`, `RelatedBill`, `BillSponsorship`, `BillDocument`, `BillVersion`, `BillDocumentLink`, `BillVersionLink`, `BillSource`, `SearchableBill`.
  - `openstates/data/models/people_orgs.py` — `Organization`, `Post`, `Person`, `PersonIdentifier`, `PersonName`, `PersonOffice`, `PersonLink`, `PersonSource`, `Membership`.
  - `openstates/data/models/vote.py` — `VoteEvent`, `VoteCount`, `PersonVote`, `VoteSource`.
  - `openstates/data/models/jurisdiction.py` — `Jurisdiction`, `LegislativeSession`.
  - `openstates/data/models/base.py` — `OCDBase`, `OCDIDField`, `RelatedBase`, `LinkBase`, `MimetypeLinkBase`, `IdentifierBase`, `RelatedEntityBase`.
  - `openstates/data/models/event.py` — `Event`, `EventLocation` (referenced but not mapped in v0).
- **Narrative docs:** <https://docs.openstates.org/data/> (where docs and source disagree, source prevails — none observed in this spec).
- **Hybrid IA v0** (this transformation's source-of-truth side): [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md).
- **Multi-state IA delta** (prior research that informed v0): [`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`](../research/2026-05-26-multi-state-legislative-ia-delta.md).
- **P0.5 plan** (this spec is step 2 deliverable): [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md).
- **Tracking issue:** [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3).
