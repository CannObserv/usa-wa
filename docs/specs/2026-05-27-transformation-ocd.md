# Transformation — Open Civic Data (OpenStates) → hybrid legislative IA

- **Date:** 2026-05-27 (review update 2026-05-28)
- **Status:** final (feeds hybrid IA v1; v1.1 revisions per OCD review are landed)
- **Direction:** **OCD → ours, only.** usa-wa consumes OpenStates / OCD data via indirect-provider adapters; we never emit data back to OCD. The `our → ocd` analysis preserved below remains useful as a completeness check on our schema (where it's lossy, we may be missing a concept) but is **not** an adapter direction.
- **Scope:** Every canonical entity defined in [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md). Counterpart schema is the OpenStates / Open Civic Data (OCD) Django models as defined in [`openstates/openstates-core`](https://github.com/openstates/openstates-core) `openstates/data/models/*.py` and the controlled vocabularies in `openstates/data/common.py`.
- **Tracks:** [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3).

## 2026-05-28 review update

Three things changed after this spec was first written:

1. **Direction is unidirectional, not bidirectional.** Adapters consume from OCD; we never publish to OCD. The original draft over-emphasized `our → ocd` mappings; treat those as schema-completeness diagnostics, not adapter behavior.

2. **Bill titles are 1:N.** OCD's `BillTitle` + `BillOtherTitle` ecosystem maps to our **`canonical.bill_titles`** child table (added in IA v1.1). `Bill.title` becomes the denormalized current canonical title; `Bill.short_description` consumes OCD `BillAbstract` (which is **not** a title — it's the summary/abstract). See §5.x below for the revised bill-title mapping.

3. **Person rich attributes defer to Power Map.** OCD's `Person.image` / `email` / `biography` / `PersonOffice` / `PersonLink` / `PersonSource` (and `Organization.sources` / `Organization.links`) map to Power Map's polymorphic primitives (`locations`, `contact_methods`, `links`, the `note` field). usa-wa **does not** carry these locally; the sidecar pushes them to Power Map. `Person.birth_year` is **removed** from our schema entirely; birth + death lifecycle data defers to Power Map's planned `lifecycle_events` ([power-map#165](https://github.com/CannObserv/power-map/issues/165)). See §5.x below for the per-attribute mapping.

The per-entity tables below are preserved as-written for the analytical content (especially the `our → ocd` lossy column, which is a useful gap analysis), with the direction-column understanding adjusted per the above.

## Why this exists

**Completeness check.** Exercising the hybrid IA against OCD pressure-tests our shape against a real, multi-state schema. Where the mapping is awkward, we get an IA-revision candidate rather than a surprise during P1a coding.

**Indirect-provider adapter blueprint.** If WSL SOAP is rate-limited, down, or rotates IDs, an `usa_openstates` adapter could populate `canonical.*` from OpenStates' JSON dumps using exactly the `OCD → ours` direction documented here. The lossy directions (§6) are the same gaps that adapter would need primary-source corroboration for.

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

## Open revisions for hybrid IA v1 (status as of 2026-05-28, post-review)

This was originally the load-bearing output of step 2. After two review passes (v1 synthesis on 2026-05-27, OCD-review-driven v1.1 + v1.2 on 2026-05-28), most items have landed. The annotations below show the v1 disposition (apply / defer) AND what actually happened.

**Items still open and awaiting decision are listed in §"Items still open" at the end of this section.**


1. ✅ **LANDED in v1.** Promote `current_district` from `Person` to `Role`. `Role.district` (text nullable) added; `Person.current_district` removed entirely. Seat-not-Person semantics preserved.

2. ✅ **LANDED in v1.** Unresolved-name fallback columns added on `BillSponsorship.sponsor_name_raw`, `PersonVote.voter_name_raw`, `Assignment.holder_name_raw`, with CHECK constraints enforcing "person_id OR raw_name".

3. ✅ **LANDED in v1.** `canonical.person_identifiers` and `canonical.organization_identifiers` 1:N child tables added. `powermap_*_id` columns kept as denormalized fast-path on Person + Organization.

4. ✅ **LANDED in v1.** Child table `canonical.bill_action_classifications(bill_action_id, classification)` for OCD-style multi-class; `BillAction.primary_classification` (denormalized, nullable) added for fast display.

5. ✅ **LANDED in v1.1 + v1.2.** `canonical.bill_titles` 1:N table added in v1.1 with `title_type` / `chamber` / `as_of_action` / `language_code` / `amendment_id` for WA's amendment-driven title-change tracking. `Bill.title` retained as denormalized current canonical title. In v1.2: `Bill.short_description` was *moved* to `BillVersion.short_description` (per-version, not per-bill) — OCD's `BillAbstract` is per-version semantically.

6. ⏸️ **DEFERRED (unchanged).** `BillIdentifier` for alternate IDs. Revisit when multi-source bill corroboration becomes a concrete use case.

7. ✅ **LANDED in v1 (as 1:N).** `canonical.bill_subjects` child table added (chosen over `text[]` for query ergonomics).

8. ✅ **LANDED in v1.** `BillAction.display_order` (int nullable) and `BillAction.is_major` (bool default false) both added.

9. ✅ **RESOLVED IN v1.3 by adapter convention (no schema change).** Multi-target referrals are decomposed into **multiple `BillAction` rows**, one per target organization, each with its own `acting_organization_id`. "Referred to Health and Ways and Means" becomes two action rows. `display_order` preserves source-intended sequencing of the decomposed pair. No 1:N child table is needed; the action log stays flat.

10. ✅ **LANDED in v1.2.** `canonical.bill_version_links` (1:N) added, with `kind ∈ {text|html|pdf|xml|image_pdf|processed_text|redline|other}` covering OCR for image-PDFs and the planned git-friendly processed-text representations. `BillVersion.text` is the canonical plain-text view; links table holds the rest.

11. ✅ **LANDED in v1 (as `VoteEvent.category`).** Procedural-vs-substantive distinction column added with `passage` / `cloture` / `recommit` / `tabling` / `motion_to_proceed` / `nomination` / `treaty` / `conviction` / `procedural` / `other` vocab (uscongress-driven naming).

12. ⏸️ **CLOSED WITH RATIONALE (kept).** `BillSponsorship.role` stays as 4-value enum (primary / co / joint / generic). Lossy collapse from free-text OCD values is documented in §5.2; the normalization is correct for our use case.

13. ✅ **LANDED in v1 (with `paired` added).** Both `VoteCount.count_type` and `PersonVote.vote` use the same 7-value vocab: `yea` / `nay` / `abstain` / `excused` / `absent` / `present_not_voting` / `paired` / `other`. `paired` was added for OCD `VOTE_OPTION='paired'` round-trip.

14. ✅ **LANDED in v1.** `canonical.bill_relationships(from_bill_id, to_bill_id, relationship_type, notes)` added with OCD-aligned vocab.

15. ✅ **LANDED in v1.** `Bill.enacted_as` (text nullable) added.

16. ✅ **LANDED in v1.3 (2026-05-29).** `canonical.vote_events.originating_bill_action_id` (ULID nullable FK to `canonical.bill_actions.id`, ON DELETE SET NULL) added. Adapter populates whenever the source surfaces the action ↔ vote linkage (OCD `VoteEvent.bill_action`, uscongress vote→action context). Migration `20260529_vote_action_link`.

17. ✅ **LANDED (and improved) in v1.2.** `LegislativeSession.classification` vocab was tightened to `regular` / `special` / `other` — `extraordinary` (no semantic difference from `special`) and `sine_die` (an adjournment state, not a session type) were dropped. Sine-die-adjournment captured via the new `adjourned_sine_die_at` timestamp column. OCD mapping rules now collapse `{regular}→{regular}` and `{special}→{special}`, with everything else falling through to `other`.

18. ✅ **LANDED in v1.** `Bill.current_step` dropped. Replaced by `Bill.current_status_class` (normalized vocab) + `Bill.current_status_at` (timestamp).

19. ✅ **LANDED in v1.** `Bill.current_status_at` (timestamptz nullable) added.

20. ✅ **LANDED in v1 (partial; richer event detail deferred).** `canonical.bill_events` added with `event_type` ∈ `public_hearing` / `executive_session` / `work_session` / `committee_meeting` / `floor_calendar` / `other`. Agenda items, related entities, media, and documents on events are deferred to P3 enrichment — current shape covers "when and where is the hearing on HB-1234" without the richer detail.

---

## Items added by 2026-05-28 OCD-review-#2 pass

These eight items came in during the second OCD review pass and landed directly in v1.2 of the IA spec. They are not "open" anymore — recorded here for the audit trail.

21. ✅ **LANDED in v1.2.** `LegislativeSession` vocab tightened: drop `extraordinary` + `sine_die` from the classification enum; add `adjourned_sine_die_at: timestamptz nullable`. Sine die is an adjournment state, not a session type.

22. ✅ **LANDED in v1.2.** `Bill.short_description` moved to `BillVersion.short_description`. OCD's `BillAbstract` is per-version, not per-bill; collapsing to a single column on Bill loses resolution.

23. ✅ **LANDED in v1.2.** `Bill.current_text` removed; replaced by `Bill.current_version_id` FK (with `use_alter=True` for the bills↔bill_versions circular FK). Canonical text now lives on `BillVersion.text`.

24. ✅ **LANDED in v1.2.** `BillVersion.text` added (canonical plain-text representation per version). Canonicalization rules — MIME, OCR for image PDFs, styled-vs-plain, pagination/formatting stripping, git-friendly processed text — are an open design discussion documented in the IA spec's Open Issues section. `BillVersionLink` (#25) carries every source form.

25. ✅ **LANDED in v1.2.** `canonical.bill_version_links` (1:N) added with `kind ∈ {text | html | pdf | xml | image_pdf | processed_text | redline | other}`. Promoted from the deferred P3 status in #10.

26. ✅ **LANDED in v1.2.** `Amendment.amendment_kind: text(16) NOT NULL` ∈ `traditional` / `striking` / `substitute`. When a striking or substitute amendment is adopted, the adapter creates a new `BillVersion` row whose `amendment_id` FK points back; traditional amendments don't produce their own BillVersion (consumed into the next engrossed version). Votes always target the Amendment row, not the BillVersion that would result.

27. ✅ **LANDED in v1.2.** `BillVersion.amendment_id` FK added — populated when a version was created by adopting a striker / substitute.

28. ✅ **LANDED in v1.2.** `canonical.bill_statutory_citations` (extracted statutory references from a BillVersion's text, with optional FK to `canonical.statute_sections`). Mirrors OCD's `Bill.citations` concept. Extraction is a P1b enrichment.

29. ✅ **LANDED in v1.2.** `clearinghouse_core.notes` (polymorphic editorial / staff / clarification / provenance notes attached to any canonical entity). Reusable framework primitive — same pattern as `Citation`. Replaces a per-entity `BillVersion.note` column. Most-relevant near-term use case: WA's non-partisan staff-prepared effects descriptions on Amendments, attached as `Note(entity_type='amendment', note_kind='staff_summary', author_organization_id=<senate_committee_services>)`.

---

## Items still open

After the v1.3 follow-up pass (2026-05-29) on the four-plus-one v1.2 queue, three items resolved (#9 by adapter convention, #16 by schema change, BillVersion.text by explicit deferral confirmation), one closed (#12), and one (#6) is **awaiting concrete WA examples** before a decision can be made.

- **#6 `BillIdentifier` for alternate IDs.** Status: open — awaiting concrete WA examples that distinguish *alternate identifier* from existing models (popular title → `BillTitle`; engrossment/substitution prefix like "ESSB 1494" → `BillVersion.version_type`; companion → `BillRelationship`; cross-source ID → already handled by `(jurisdiction_id, source, source_id)` uniqueness). If no example survives those filters, this stays deferred.

- **#9 `BillActionRelatedEntity`.** ✅ Resolved 2026-05-29 by adapter convention — multi-target referrals decompose into multiple `BillAction` rows, one per target, with `acting_organization_id` and `display_order`. No schema change. See item #9 above and the hybrid IA spec's `canonical.bill_actions` section.

- **#12 widen `BillSponsorship.role`.** Status: closed with rationale (kept 4-value enum). Reaffirmed 2026-05-29.

- **#16 `VoteEvent.originating_bill_action_id`.** ✅ Landed in v1.3 (2026-05-29). See item #16 above.

- **BillVersion.text canonicalization rules.** Status: explicit deferral confirmed 2026-05-29. Documented in the hybrid IA spec's "Open issues" section; P1b enrichment will produce a concrete proposal. Until then `BillVersion.text` is non-null when a sensible canonical form exists; alternative source forms land in `bill_version_links`.

---


## Post-review addendum (v1.1 landing)

These mappings reflect the OCD-review revisions to the hybrid IA (v1 → v1.1, 2026-05-28). They supersede the corresponding cells in the per-entity tables above where there's conflict.

### Bill titles (1:N revisited)

Pre-review the spec treated `Bill.title` and `Bill.short_description` as scalar columns. Post-review they decompose:

| OCD source | → usa-wa target | Notes |
|---|---|---|
| `Bill.title` (the canonical title at current state) | (a) `canonical.bills.title` denormalized + (b) `canonical.bill_titles` row with `title_type='canonical'`, `is_current=true` | Update both atomically. |
| `BillTitle` (1:N alternative titles with `note` classification — popular / short / official / etc.) | `canonical.bill_titles` row per OCD row | Map OCD's `note` to our `title_type` vocabulary (`popular` / `short` / `official` / `display` / `alternative` / `long`). When OCD uses an unfamiliar `note`, store as `alternative`. |
| `BillAbstract.abstract` (1:N summaries/abstracts — explicitly **not** titles) | `canonical.bills.short_description` (take the most recent / canonical one) | OCD allows multiple abstracts per bill; we collapse to a single value. The discarded abstracts are not currently captured — accept as lossy for MVP. |
| (no OCD field) — WA's amendment-driven title-change tracking | `canonical.bill_titles.amendment_id` + `effective_at` + `replaced_at` | WA-specific. The OCD inbound adapter leaves these null. Only the WSL primary adapter populates them. |

### Person rich attributes (defer to Power Map)

| OCD field | → Power Map primitive | Notes |
|---|---|---|
| `Person.image` | Power Map `links` row, `kind='image'` | Sidecar push; no local column. |
| `Person.email` | Power Map `contact_methods` row, `kind='email'` | Sidecar push; no local column. |
| `Person.biography` | Power Map `note` field on the Person entity | Sidecar push; no local column. |
| `Person.birth_date` | Power Map `lifecycle_events` row, `event_type='birth'` (planned, [power-map#165](https://github.com/CannObserv/power-map/issues/165)) | Capture year/month/day where available, plus `birth_place` if OCD provides via `extras` JSONB. **Defer to PM #165 ship**; until then, sidecar stages locally. usa-wa schema no longer carries `birth_year`. |
| `Person.death_date` | Power Map `lifecycle_events` row, `event_type='death'` (planned #165) | Same as birth. |
| `PersonOffice` (address, phone, classification) | Power Map `locations` (address) + `contact_methods` (phone) — both polymorphic on entity | Two rows per office; classification rides on the location's metadata. |
| `PersonLink` (homepage, social, etc.) | Power Map `links` row, `kind` set per link semantic | One row per link. |
| `PersonSource` (provenance URL) | Power Map `links` row, `kind='source'` | One row per source citation. |
| `Organization.sources` (provenance URLs) | Power Map `links` row, `kind='source'`, polymorphic on Organization | Same as PersonSource but Organization-attached. |
| `Organization.links` | Power Map `links` row | Same as PersonLink but Organization-attached. |
| `PersonIdentifier(scheme, identifier)` | `canonical.person_identifiers` (1:N child, already added in v1) | usa-wa **does** carry these locally; the v1 child table is purpose-built for the cross-system identifier graph. |

usa-wa adapters that consume OCD data write Person/Organization to `canonical.*` for the identity essentials (name, identifier graph, role/assignment); the sidecar consumes the rich attributes and pushes them to Power Map. The local schema doesn't gain columns for image / email / biography / office / links / sources / lifecycle-events — those live upstream.

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
