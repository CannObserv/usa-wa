# Multi-state legislative IA — delta against `clearinghouse-domain-legislative`

- **Date:** 2026-05-26
- **Phase:** P0 discovery (feeds P1a Layer 2 schema)
- **Audience:** engineers about to write the first WSL normalization code
- **Status:** finding — schema revisions proposed at bottom; not yet applied

## Why this exists

`clearinghouse-domain-legislative` is being designed against WA only but is meant to be reused across all US state legislatures (and possibly federal). The MVP spec calls out the failure mode:

> "Multi-state IA risk: designing Layer 2 against only WA is the failure mode."

This note surveys four reference sources, deltas their entities against ours, surfaces edge cases we can't currently represent, and proposes concrete schema revisions before P1a normalization code is written. Each finding cites the exact source artifact so revisits are cheap.

Sources surveyed:

| Source | What it is | Access |
|---|---|---|
| **OpenStates / OCD** | The de-facto standard multi-state legislative model. Django ORM, 50-state coverage, 10+ years of pressure-testing. | Read direct from `openstates/openstates-core` (GitHub) — `openstates/data/models/*.py` and `openstates/data/common.py`. |
| **LegiScan** | Commercial multi-state legislative tracker. Documents an explicit JSON wire format with normalized status/event/role/sponsor-type vocabularies. | Read direct from `LegiScan_API_User_Manual.pdf` Rev 2025-03-17 (downloaded via WebFetch, extracted with `pdftotext`). |
| **unitedstates/congress** | Federal-government bill scraper. Confirms the stable-field shape used by GovTrack, ProPublica, etc. | Read direct from `unitedstates/congress` GitHub repo — `congress/tasks/bill_info.py` plus wiki summary. |
| **NCSL** | Higher-level taxonomy from the National Conference of State Legislatures. | **Unavailable.** ncsl.org returns 403 to all programmatic requests (including via `web.archive.org`). Their glossary is widely cited but cannot be fetched from this environment. The OpenStates `BILL_ACTION_CLASSIFICATIONS` enum is the closest reproducible substitute and is treated as such below. |

## Our Layer 2 entities (as designed in the MVP spec)

Re-summarized so the deltas have something to point at.

**Provenance spine** (`clearinghouse-core`, applies to all canonical entities): `jurisdictions`, `sources`, `fetch_events`, `raw_payloads`, `citations`. Every canonical entity carries `jurisdiction_id`, `source`, `source_id`, ULID PK, `primary_source_id`, `last_fetched_at`, `last_fetch_event_id`.

**Bill cluster:**

| Entity | Key fields |
|---|---|
| `Bill` | biennium, chamber, number, title, short_description, current_status, current_step, introduced_at, current_text |
| `BillSponsorship` | bill_id, legislator_id, role (`prime`/`co`) |
| `BillAction` | bill_id, action_at, chamber, action_type (vocab), description; UNIQUE `(bill_id, source_action_id)` |
| `Legislator` | name, chamber, district, party, biennium |
| `Committee`, `Hearing` | skeletal in P1a |
| `BillVersion` | substitute/engrossed flags, action_at (text deferred to P3) |

**Statute cluster:** `StatuteCode`, `StatuteTitle`, `StatuteChapter`, `StatuteSection`, `BillStatuteChange (creates/amends/repeals/recodifies)`.

**PDC-shaped cluster:** `Filer`, `LobbyingActivity`, `LobbyingPosition (support/oppose/neutral)`, `Contribution`.

---

## Source 1 — OpenStates (Open Civic Data)

### What they model

The reference implementation for "what does a multi-state legislative data model look like." OCD models a graph of `Jurisdiction → Organization → Person/Post/Membership` for actors and `Jurisdiction → LegislativeSession → Bill → (BillAction | BillSponsorship | BillVersion | BillDocument | VoteEvent | Event)` for legislative work product. Heavy use of polymorphic `RelatedEntityBase` so any "entity who did a thing" can be a Person, an Organization, or an unresolved string name. Each entity carries an `OCDIDField` URL-style natural ID (`ocd-bill/...`). Vocabularies are centralized in `openstates/data/common.py` as hard-enforced enums.

### Delta table

| Our entity / field | OpenStates equivalent | Gap / mismatch |
|---|---|---|
| `Bill` | `Bill` (model `opencivicdata_bill`) | OCD `from_organization` is an FK to a chamber `Organization`, not a `chamber` enum. **Their `classification` is an array** (e.g., `["bill"]`, `["resolution"]`) covering 24 distinct bill types — far richer than our implied "Bill" assumption. They have `subject` as `ArrayField`, `citations` as JSONField, plus computed denormalized fields `first_action_date`, `latest_action_date`, `latest_action_description`, `latest_passage_date`. |
| `Bill.biennium` | `Bill.legislative_session_id` (FK to `LegislativeSession`) | **Critical gap.** OCD treats `LegislativeSession` as a first-class entity with `identifier`, `name`, `classification (primary/special)`, `start_date`, `end_date`, `active`. WA's "biennium" is just one specialization. We currently model biennium as a text column on Bill, with no notion of *which* session within a biennium (WA has the regular 105/60-day session plus possible special sessions). |
| `Bill.title` | `Bill.title` + `BillAbstract` + `BillTitle` (multiple alternate/short titles) | They model the fact that a single bill has many titles over its life (official, popular, short, alternate) as a 1:N table. We collapse to a single `title` + `short_description`. WA Legislature SOAP returns multiple titles. |
| `Bill.current_status` | Computed from `BillAction.classification[]` + `BillStatusComputed` logic | **OCD has no `current_status` column.** Status is derived from the action log. We denormalize it. (LegiScan also denormalizes; see below.) |
| `Bill.current_text` | `BillVersion` (with `note`, `date`, `classification`) + `BillVersionLink` (with `mimetype`, `url`) + separate `SearchableBill` for full text | **Critical gap.** OCD treats every version as a distinct row with timeline + mimetype-tagged links. Our single `current_text` field on Bill cannot represent: (a) version history during a session, (b) multiple representations of the same version (PDF + HTML), (c) the search-indexed version vs. citation-display version. |
| `BillSponsorship` | `BillSponsorship` (model `opencivicdata_billsponsorship`) extends `RelatedEntityBase` | **Critical gap.** OCD `RelatedEntityBase` allows a sponsorship to point at either a `Person` *or* an `Organization` *or* an unresolved `name + entity_type` string. They also carry `primary: bool` + `classification: str` (free-text role, e.g., "primary", "cosponsor", "joint author"). Our model only links to `Legislator`. WA does have committee-sponsored bills ("committee bills"), which we currently cannot represent. |
| `BillAction` | `BillAction` (FK to `Organization`, not chamber enum) + `BillActionRelatedEntity` (polymorphic — committees, persons, other bills) | OCD links each action to the `Organization` that took it (chamber or committee). A single action can cite multiple related entities (e.g., "Referred to House Rules and House Appropriations" — two related orgs). Our `chamber` column is a flat enum, and we have no concept of action-related entities. They have `order: PositiveIntegerField` — explicit display ordering separate from timestamp (important when two actions share a date). |
| `BillAction.action_type` | `BillAction.classification: ArrayField[str]` — **40 normalized values** (see vocab below) | **A single action can carry multiple classifications.** Example: a final-passage action that also reports out of committee. Our shape implies one type per action. |
| `Legislator` | `Person` (model `opencivicdata_person`) + `Membership` + `Post` + `Organization` | **Critical structural gap.** OCD splits identity (`Person`) from role (`Membership` over `Organization` with `start_date`/`end_date`). A legislator who switches chambers between sessions, or who serves in multiple capacities, is naturally modeled. Our `Legislator` collapses identity + role + session into one row, and we'd duplicate the same person across bienniums. |
| `Legislator` external IDs | `PersonIdentifier` (1:N over scheme + value) | OCD lets a person carry many external IDs (bioguide, ftm_eid, votesmart, etc.) as a related table. We have only `powermap_person_id`. We will want to attach `wa_leg_member_id`, `pdc_filer_id`, `vsmart_id`, etc. — and they evolve. |
| `Committee`, `Hearing` | `Organization (classification='committee')` + `Event` (with `EventAgendaItem`, `EventRelatedEntity`, `EventLocation`, `EventDocument`, `EventMedia`) | OCD doesn't have "Hearing" as a distinct type — hearings are `Event`s with `classification='committee-hearing'` (or similar). The richer model: a hearing has agenda items, each item references bills/votes, with attached documents and media. Our planned `Hearing` is one row per hearing. |
| (no equivalent) | `VoteEvent` + `VoteCount` + `PersonVote` | **Critical gap.** We have no vote model. WA Legislature SOAP returns roll-call votes; the MVP question shape ("describe HB-1234") almost certainly should include them. OCD's shape: VoteEvent (motion + start_date + result + chamber + bill_action FK) → VoteCount (yes/no/absent/abstain/not voting/paired/excused) → PersonVote (per-legislator). |
| (no equivalent) | `RelatedBill` — links between bills with `relation_type` enum (`companion`, `prior-session`, `replaced-by`, `replaces`, `related`) | **Critical gap.** WA has bills with both House and Senate versions ("companion bills"). When SHB-1234 supersedes HB-1234, that's `replaced-by`. Carryover from prior biennium is `prior-session`. Our model has no way to express any of these. |
| (no equivalent) | `BillIdentifier` (alternate identifiers) | Same bill picked up by another source (e.g., LegiScan) under a different ID. We rely on `(jurisdiction_id, source, source_id)` uniqueness but have no place to attach "this is also known as LegiScan bill_id 1234567" for cross-reference. |
| `StatuteSection` (and statute cluster) | (no equivalent) | OCD doesn't model statute corpora at all. This is a usa-wa-specific extension; no IA pressure from OCD. |
| `Filer`, `LobbyingActivity`, etc. | (no equivalent) | OCD doesn't model campaign finance / lobbying. PDC cluster is independent. |
| `BillVersion` (substitute/engrossed flags) | `BillVersion.classification` ∈ {`filed`, `introduced`, `amendment`, `substituted`, `enrolled`, `became-law`} | Their enum is broader and standard across states. We use boolean flags. |

### Vocabularies — OpenStates as candidate standard

These are the enforced enums in `openstates/data/common.py`:

- **`BILL_CLASSIFICATIONS`** (24 values): `bill`, `resolution`, `concurrent resolution`, `joint resolution`, `memorial`, `commemoration`, `concurrent memorial`, `joint memorial`, `proposed bill`, `proclamation`, `nomination`, `contract`, `claim`, `appointment`, `constitutional amendment`, `petition`, `order`, `concurrent order`, `appropriation`, `ordinance`, `motion`, `study request`, `concurrent study request`, `bill of address`.
- **`BILL_ACTION_CLASSIFICATIONS`** (40 values): `filing`, `introduction`, `enrolled`, `reading-1`, `reading-2`, `reading-3`, `passage`, `informal-passage`, `failure`, `withdrawal`, `substitution`, `amendment-introduction`, `amendment-passage`, `amendment-withdrawal`, `amendment-failure`, `amendment-amendment`, `amendment-deferral`, `committee-passage`, `committee-passage-favorable`, `committee-passage-unfavorable`, `committee-failure`, `executive-receipt`, `executive-signature`, `executive-veto`, `executive-veto-line-item`, `became-law`, `veto-override-passage`, `veto-override-failure`, `deferral`, `receipt`, `referral`, `referral-committee`, `hearing-held`, `work-session`, `sponsorship`, `carried-over`, `reported-out-of-committee`, `concurrence`.
- **`BILL_RELATION_TYPES`**: `companion`, `prior-session`, `replaced-by`, `replaces`, `related`.
- **`BILL_VERSION_CLASSIFICATIONS`**: `''`, `filed`, `introduced`, `amendment`, `substituted`, `enrolled`, `became-law`.
- **`BILL_DOCUMENT_CLASSIFICATIONS`**: `fiscal-note`, `committee-report`, `summary`, `digest`, `veto-message`, `analysis`, `law`.
- **`VOTE_OPTIONS`**: `yes`, `no`, `absent`, `abstain`, `not voting`, `paired`, `excused`, `other`.
- **`VOTE_RESULTS`**: `pass`, `fail`.
- **`VOTE_CLASSIFICATIONS`**: `passage`, `amendment`, `committee-passage`, `reading-1`, `reading-3`, `veto`, `veto-override`.
- **`SESSION_CLASSIFICATIONS`**: `primary`, `special`.
- **`ORGANIZATION_CLASSIFICATIONS`**: `legislature`, `executive`, `upper`, `lower`, `party`, `committee`, `government`.

### Edge cases we cannot currently represent

1. **Multi-classification actions.** A single bill action that is both "third reading" and "passage" gets two classifications.
2. **Committee-sponsored bills.** Bills introduced by a committee, not a legislator. Our `bill_sponsorships.legislator_id` is presumably non-null.
3. **Sponsor entity disambiguation at ingest time.** When the SOAP feed lists "Smith" and we can't yet resolve to a `Legislator` row, OCD's `RelatedEntityBase` lets us store the raw name + entity_type and resolve later. We currently have no holding pattern.
4. **A legislator's session-bound role.** Same person can be `Rep-District-1` in 2023-24 and `Sen-District-1` in 2025-26. Our model has chamber/district on `Legislator` itself, forcing duplication or losing one role.
5. **Vote modeling at all.** Roll-call votes from WA Legislature SOAP have nowhere to land in our current schema.
6. **Inter-bill relationships.** Companion bills, prior-session reintroductions, substitutions, replacements.
7. **Multiple titles per bill.** Long title, short title, popular title, alternate titles.
8. **Multiple versions of the same document, in multiple file formats.** WA's bill PDFs and HTML versions of the same engrossment.

---

## Source 2 — LegiScan

### What they model

A commercial multi-state legislative tracker exposing JSON over an API key, with a defined wire-format schema and explicit normalized vocabularies. The shape is tighter and more denormalized than OCD — built for bill-tracking dashboards, not graph queries. The notable contribution: a *flat, integer-keyed* normalized vocabulary for status, event types, sponsor types, roles, SAST (same-as/similar-to) relations, and votes. These are the closest thing to an industry interchange standard for state legislative data.

### Delta table

| Our entity / field | LegiScan equivalent | Gap / mismatch |
|---|---|---|
| `Bill.current_status` | `bill.status` (integer) | LegiScan has 12 normalized status values: `N/A`, `Introduced`, `Engrossed`, `Enrolled`, `Passed`, `Vetoed`, `Failed`, `Override`, `Chaptered`, `Refer`, `Report Pass`, `Report DNP`, `Draft`. They denormalize current status onto the bill itself, like we plan to. **Their `progress[]` is a separate "milestone log" — date+event pairs for the *significant* steps only**, distinct from full `history[]`. Useful: clear "current step" reporting without scanning all actions. |
| `Bill.biennium` + `Bill.chamber` | `bill.session{}` (object with `session_id`, `year_start`, `year_end`, `prefile`, `sine_die`, `prior`, `special`) + `bill.body` (originating chamber) + `bill.current_body` (current chamber) | **They track originating vs. current chamber separately.** A bill that crosses chambers has different `body` vs. `current_body`. Their session object has explicit `sine_die` and `prior` (archived) flags. We model biennium as a string and chamber as a single column. |
| `Bill.title` | `bill.title` + `bill.description` | LegiScan has `title` (short, headline) and `description` (long, the actual statement of effect). Our `title` + `short_description` is the same shape but with **inverted naming** — our `title` is the *long* one. Renaming for industry alignment is cheap before P1a. |
| `BillAction` | `bill.history[]` — array of `{date, action, chamber, chamber_id, importance: bool}` | They flag *major* steps with an `importance` boolean. The `progress[]` array is "history filtered to importance=true". Useful denormalization. We have no equivalent. |
| `BillSponsorship` | `bill.sponsors[]` with `sponsor_type_id` ∈ {0: Sponsor (Generic), 1: Primary, 2: Co, 3: Joint} + `sponsor_order: integer` + `committee_sponsor: bool` + `committee_id` (when sponsor is a committee) | **Critical confirmation.** LegiScan explicitly models committee-as-sponsor as a flag + FK *on the sponsorship row*, not as a separate sponsorship type. Sponsor_type has 4 values, not 2 (we have `prime`/`co`). They preserve `sponsor_order` — the legislator's *position* on the sponsorship list, which matters for display. |
| `Legislator` | `bill.sponsors[].person_hash` + many external IDs: `ftm_eid` (FollowTheMoney), `votesmart_id`, `opensecrets_id`, `knowwho_pid`, `ballotpedia` | They embed sponsor identity inline rather than referencing a separate Person, but their *external-IDs surface* is broad. We currently anticipate only `powermap_person_id`. |
| (no equivalent) | `bill.sasts[]` — Same-As/Similar-To records with 9 relation types: `Same As`, `Similar To`, `Replaced By`, `Replaces`, `Cross-filed`, `Enabling For`, `Enabled By`, `Related`, `Carry Over` | Same gap as OCD's `RelatedBill`; LegiScan's vocab is broader (adds `Cross-filed`, `Enabling For`/`Enabled By`). |
| (no equivalent) | `bill.subjects[]` — subject_id + subject_name | Multi-state issue tagging. WA Legislature SOAP returns subjects. We don't model them. |
| `BillVersion` | `bill.texts[]` — array of `{doc_id, date, type, type_id, mime, mime_id, url, state_link, text_size, text_hash}` | **They track 14 distinct text types**: Introduced, Committee Substitute, Amended, Engrossed, Enrolled, Chaptered, Fiscal Note, Analysis, Draft, Conference Substitute, Prefiled, Veto Message, Veto Response, Substitute. They also separate `texts[]` (versions of the bill itself) from `supplements[]` (8 types: Fiscal Note, Analysis, Fiscal Note/Analysis, Vote Image, Local Mandate, Corrections Impact, Miscellaneous, Veto Letter). |
| (no equivalent) | `bill.amendments[]` — first-class entities with `adopted` flag, chamber, date, title, description, document hash | **We have no amendment model.** Amendments aren't `BillVersion`s — they're proposed modifications, may or may not be adopted, and can themselves have texts and votes. |
| (no equivalent) | `bill.votes[]` (roll-call summaries: yea/nay/nv/absent/total/passed/chamber) + separate `getRollCall` op for per-legislator | Same vote gap noted in OCD section. |
| (no equivalent) | `bill.calendar[]` — `{type_id, type, date, time, location, description}` for hearings/markup/executive sessions | LegiScan models hearings inline on the bill, with 3 event types (Hearing, Executive Session, Markup Session). We have a planned `Hearing` table but no link from bill ↔ scheduled event. |
| `BillAction.chamber` | `bill.history[].chamber` + `chamber_id` | LegiScan resolves chamber as both the human text and a `body_id` (integer). Their `Roles` vocab is just 3: `Representative / Lower Chamber`, `Senator / Upper Chamber`, `Joint Conference`. |
| `LobbyingPosition (support/oppose/neutral)` | `bill.stance` ∈ {0: Watch, 1: Support, 2: Oppose} | **Vocabulary mismatch.** LegiScan uses Watch/Support/Oppose (no "neutral"). PDC uses Support/Oppose/Other. Worth noting that "neutral" is rare in practice — most positions are stated as support or oppose. |
| `Filer` | (no direct equivalent) | LegiScan doesn't model lobbying. |

### Vocabularies — LegiScan as candidate standard

- **Status (12)**: as above.
- **Sponsor Types (4)**: Generic, Primary, Co, Joint.
- **Roles (3)**: Representative/Lower, Senator/Upper, Joint Conference.
- **SAST Types (9)**: Same As, Similar To, Replaced By, Replaces, Cross-filed, Enabling For, Enabled By, Related, Carry Over.
- **Event Types (3)**: Hearing, Executive Session, Markup Session.
- **Text Types (14)**: as above.
- **Supplement Types (8)**: Fiscal Note, Analysis, Fiscal Note/Analysis, Vote Image, Local Mandate, Corrections Impact, Miscellaneous, Veto Letter.
- **Votes (4)**: Yea, Nay, Not Voting/Abstain, Absent/Excused — *coarser than OCD's 7-value option set*.
- **Bill Types (23)**: B, R, CR, JR, JRCA, EO, CA, M, CL, C, CSR, JM, P, SR, A, CM, I, PET, SB, IP, RB, RM, CB. Most overlap OCD; LegiScan adds Executive Order, Joint Resolution Constitutional Amendment, Initiative Petition, Repeal Bill, Remonstration.
- **Stance (3)**: Watch, Support, Oppose.

### Edge cases we cannot currently represent

1. **Major-step filtering.** Distinguishing the 6-event lifecycle ("intro → engrossed → enrolled → passed → signed → law") from the 80-step raw action history. LegiScan precomputes this.
2. **Amendments as a first-class entity.** Proposed, adopted-or-not, with their own document hash. (Distinct from `BillVersion`.)
3. **Originating chamber vs. current chamber.** A House bill that's now in Senate Rules.
4. **Cross-filed companion bills.** A specific SAST relation distinct from OCD's generic `companion`.
5. **Subject tagging.** Multi-subject issue tags per bill.
6. **Sine die / prefile / archived session flags.** WA's biennium has prefile periods. LegiScan models them explicitly on the session.
7. **Per-bill scheduled events.** Hearings/markup/exec sessions linked to a bill via calendar, not the inverse.
8. **The `progress[]` denormalization.** A small, curated milestone log distinct from the full action history.

---

## Source 3 — unitedstates/congress (federal)

### What they model

Federal-government bill scraper that's the upstream of GovTrack, ProPublica, and a generation of civic-tech projects. Output is JSON files per (Congress, bill_type, bill_number). Their schema is *less* normalized than OCD but covers some federal-specific concepts (committee reports, conference reports, amendments with their own numbering schemes, "enacted as" public/private law numbering). Useful here as confirmation of which fields are stable across the OCD / LegiScan / GovTrack triangle, and to surface anything federal-specific that might still appear at the state level.

### Delta table

| Our entity / field | unitedstates/congress equivalent | Gap / mismatch |
|---|---|---|
| `Bill` | top-level JSON object with `bill_id`, `bill_type`, `number`, `congress`, `introduced_at`, `updated_at` | Their `congress` is a one-integer biennial session marker (e.g., `118`). State equivalents vary — WA uses "2025-26" or "68th Legislature". Confirms that *some* session-identifier field is universal. |
| `Bill.title` | `official_title`, `popular_title`, `short_title`, `titles[]` | Same OCD finding — bills have multiple titles. Federal has explicit `popular_title` ("Affordable Care Act"). State may or may not. Worth keeping `titles[]` as 1:N. |
| `Bill.current_status` | `status` + `status_at` (text + datetime) | Same denormalize-current-status pattern. `status_at` separately captures the timestamp of the current status — useful. |
| (no equivalent) | `enacted_as` (object with public-law-number, congress, etc.) | When a bill becomes law, the resulting law has its own identifier ("Public Law 117-2"). WA's analog is "Chapter X, Laws of YYYY". We should plan for an "enacted as" cross-reference. |
| `BillAction` | `actions[]` with `type`, `acted_at`, `text`, `references[]`, plus type-specific extras | Their `type` is a single string from a smaller fixed set: `referral`, `reported`, `hearings`, `discharged`, `vote`, `vote-aux`, `calendar`, `topresident`, `signed`, `vetoed`, `enacted`. Vote actions carry `how`, `roll`, `where`, `result`, `suspension`. They explicitly model `references[]` — links from action text to outside identifiers (committee names, report numbers). |
| `BillSponsorship` | `sponsor` (single object) + `cosponsors[]` (array) | They split prime vs. co into separate fields, not a "role" enum on a unified row. Cosponsor records carry `original_cosponsor: bool`, `sponsored_at`, `withdrawn_at` — **cosponsorship has a lifecycle** (a cosponsor can withdraw). Our shape can't represent withdrawal. |
| `Legislator` | sponsor object carries `bioguide_id`, `name`, `state`, `district`, `title`, `type` | Identity-by-bioguide-id is the federal standard. Confirms the external-ID-attachment pattern. |
| (no equivalent) | `committees[]` — each entry has `committee`, `committee_id`, `activity[]`, `subcommittee`, `subcommittee_id` | Bills carry committee-activity history inline (which committees handled them and what they did). We plan separate `Committee` + bills-through-committees via actions. |
| (no equivalent) | `amendments[]` (referenced by amendment_id and chamber prefix) | Federal amendments are independent entities with their own number space, sponsors, votes, text. Same gap as LegiScan. |
| (no equivalent) | `related_bills[]` with `bill_id`, `reason`, `type` | Same as OCD `RelatedBill` and LegiScan `sasts[]`. |
| `BillVersion` | (text version handling done by separate `bill-text` task; bill JSON has links only) | Federal explicitly punts text storage to a sibling system. Reinforces the "version is a row with link, text is somewhere else" pattern. |
| (no equivalent) | `committee_reports[]` | Federal-specific publication artifact. State analog is uncommon but not zero (committee staff reports). |
| (no equivalent) | `history{}` object — pre-computed booleans like `active`, `house_passage_result`, `senate_passage_result`, `vetoed`, `enacted`, `awaiting_signature` | Coarse denormalization for "where is the bill now". State analog: WA's `current_status` text. |
| `StatuteSection` | not modeled (only `enacted_as` pointer at result) | Federal doesn't keep USC in this repo. |

### Edge cases we cannot currently represent

1. **Cosponsor withdrawal.** A cosponsor who later withdraws their support — historical record matters.
2. **Public Law / "enacted as" cross-reference.** Bill → resulting law citation when passed.
3. **Action references.** Actions contain inline references to committee names, report numbers, related bills — structured data hiding inside action text.

---

## Source 4 — NCSL

### What they model

National Conference of State Legislatures publishes a glossary of legislative terms intended as a cross-state vocabulary. **The site is unavailable from this environment** — every NCSL URL attempted (direct, alternate, archive.org) returns 403/410.

### What we can substitute

The OpenStates `BILL_ACTION_CLASSIFICATIONS` enum was, per the OCD commit history, derived from a cross-state taxonomy review and serves as the most reproducible proxy for "NCSL-style canonical action vocabulary." Treat the OCD vocab as the standard for now; revisit if NCSL becomes accessible.

### Blocking unknown

If anyone has access to the NCSL glossary (PDF, cached copy, archive.org via browser), drop a copy in `docs/research/references/ncsl-glossary.pdf` and re-run this section. Until then, OCD is our stand-in.

---

## Cross-source agreement on stable fields

These fields appear in three or more of OCD/LegiScan/unitedstates with the same semantics. They're the safest core for our shape:

| Field family | OCD | LegiScan | unitedstates | Recommendation |
|---|---|---|---|---|
| Bill identifier (chamber + number) | `Bill.identifier` | `bill_number` | `bill_type` + `number` | Keep our `chamber + number` split; add bill-type vocab. |
| Session/biennium identifier | `LegislativeSession.identifier` | `session.session_id` | `congress` | Promote our `biennium` string to a first-class `LegislativeSession` entity. |
| Bill title (multi) | `Bill.title` + `BillTitle[]` | `title` + `description` | `official_title` + `popular_title` + `titles[]` | Promote titles to 1:N. |
| Sponsor type | `BillSponsorship.classification` + `primary:bool` | `sponsor_type_id` (4 values) | `sponsor` / `cosponsors[]` | Expand our 2-value role enum to at least 4 (primary, co, joint, generic) and allow committee-as-sponsor. |
| Action type | `classification[]` (40 values) | `event_type` + `importance:bool` | `type` (~12 values) | Adopt OCD's 40-value vocab as ours; add `major:bool` for importance filtering. |
| Vote model | `VoteEvent` + `VoteCount` + `PersonVote` | `votes[]` + roll-call detail | `vote` action with `result` | Add a vote cluster. OCD's shape is the richest. |
| Bill relationships | `RelatedBill` (5 types) | `sasts[]` (9 types) | `related_bills[]` | Add a `BillRelationship` table. Use OCD's vocab; LegiScan adds `cross-filed` and `enabling-for/by` if needed. |
| External IDs on people | `PersonIdentifier` (scheme + value) | inline fields per ID system | `bioguide_id` etc. | Expose external IDs as a 1:N child table on Legislator. |
| Bill text versions | `BillVersion` + `BillVersionLink` | `texts[]` (14 types) | (handled separately) | Keep our `BillVersion`; expand `classification` to 14-value vocab; add 1:N `BillVersionLink` for mimetype variants. |
| Status denorm | (computed) | `status` + `status_date` | `status` + `status_at` | Keep our `current_status` denorm; add `current_status_at`. |
| Subjects | `Bill.subject[]` | `subjects[]` | `subjects[]` + `subjects_top_term` | Add a subject-tag concept (either array column or 1:N). |
| Committee-as-sponsor | OCD polymorphic | `committee_sponsor:bool` + `committee_id` | (handled via committees[]) | Make `BillSponsorship.legislator_id` nullable; add nullable `committee_id`. |

---

## Recommendation

### Entities to add to `clearinghouse-domain-legislative` before P1a

**Tier 1 — write code against P1a will not work without these:**

1. **`LegislativeSession`.** Promote `biennium` from text column to first-class entity with `(jurisdiction_id, identifier, name, classification ∈ {regular, special}, start_date, end_date, active)`. Bills, votes, hearings, legislator memberships all FK to it. WA bienniums are one row each; special sessions get their own row.
2. **`VoteEvent` + `VoteCount` + `PersonVote`.** WA Legislature SOAP returns roll-call votes. The MVP "describe HB-1234" question cannot honestly answer "where is it in the process" without final-passage votes. OCD's shape is the model. Cost: ~3 tables, mostly mechanical.
3. **`BillRelationship`.** `(bill_id, related_bill_id, relation_type)` with vocab from OCD's 5 + LegiScan's `cross-filed`. Companion bills are universal at state level; carryover from prior biennium is universal.
4. **`Amendment`.** First-class entity: `(bill_id, chamber, amendment_number, sponsor_id?, adopted:bool, action_at, title, description)` + a child `AmendmentVersion` for text/mime. Distinct from `BillVersion` because amendments may or may not be adopted into a version.
5. **`LegislatorMembership`.** Split `Legislator` into two tables: `Legislator` (durable identity — name, family_name, given_name, dob, external IDs via 1:N) and `LegislatorMembership` (legislator_id, session_id, chamber, district, party, start_date, end_date). Skip this only if we're willing to lose the ability to track a legislator across multiple sessions or chambers. We are not — power-map integration in P2 will need it.

**Tier 2 — defer if cost is high, add post-P1a:**

6. **`BillTitle`** (1:N, with `type ∈ {official, short, popular, alternate}`).
7. **`BillSubject`** (1:N or `ArrayField[str]`).
8. **`LegislatorIdentifier`** (1:N for external ID systems).
9. **`BillVersionLink`** (1:N children of BillVersion, one per mimetype/URL).
10. **`BillAction.related_entities`** (action ↔ committee/legislator/other-bill polymorphic links, modeled as OCD's `BillActionRelatedEntity`).

### Field additions on existing entities

1. **`Bill.bill_type`** ∈ OCD's 24-value `BILL_CLASSIFICATIONS`. (Currently we implicitly assume "bill" — that's wrong for resolutions, memorials, etc.)
2. **`Bill.originating_chamber`** + **`Bill.current_chamber`** (LegiScan's `body` vs `current_body`). Replace single `chamber` column.
3. **`Bill.current_status_at`** (timestamp of last status change). Cheap.
4. **`Bill.enacted_as`** (text/JSONB) — "Chapter X, Laws of YYYY" for WA. Nullable.
5. **`BillSponsorship.committee_id`** (nullable FK) — committee-as-sponsor.
6. **`BillSponsorship.legislator_id`** — make nullable (committee bills have no legislator sponsor).
7. **`BillSponsorship.sponsor_order`** (integer) — preserve display order.
8. **`BillSponsorship.withdrawn_at`** (nullable timestamp) — for withdrawn cosponsorships.
9. **`BillAction.classification`** as **array** (or a child table) — actions can carry multiple types. At minimum, expand the vocab to OCD's 40 values.
10. **`BillAction.is_major`** (boolean) — for milestone filtering, LegiScan's `importance`.
11. **`BillAction.order`** (integer) — display order independent of timestamp.
12. **`BillVersion.classification`** — adopt LegiScan's 14-value vocab (or OCD's 7-value vocab as a starting subset).

### Renames / vocabulary changes for industry alignment

1. **`Bill.title` vs `Bill.short_description`.** Industry convention (LegiScan, OCD, unitedstates) is `title` = short, `description` = long. We have them inverted. Rename `short_description` → `title`, our current `title` → `description`. Cheap to do before any code is written.
2. **`BillSponsorship.role`** — expand from `{prime, co}` to `{primary, co, joint, generic}` aligned with LegiScan, or rename to `classification` aligned with OCD.
3. **`Bill.current_step`** — drop or fold into `current_status`. Distinct from `current_status` only in vague ways; no source models them separately.
4. **`Legislator`** → keep the name (already correctly distinguishes "Legislator" from OCD's "Person" which conflates roles).

### Explicit defers (surveyed but not adding before P1a)

- **Polymorphic sponsor entity (OCD-style `RelatedEntityBase`).** Useful for unresolved-name ingest, but a nullable `(legislator_id, committee_id)` pair on `BillSponsorship` covers the realistic WA cases. Revisit if WA ingest surfaces unresolved-name problems at scale.
- **Multi-classification actions (array of classifications per action).** Add `classification` as scalar in P1a; promote to array if WA action types map cleanly only with multi-classification.
- **`BillIdentifier` (alternate IDs).** Defer until a second source brings up the need. `(jurisdiction_id, source, source_id)` uniqueness handles the MVP cases.
- **`Bill.subjects`** as array. Subject tagging is question-relevant but not blocking for "describe HB-1234". Add in P1b or P3.
- **OCD's `Organization`/`Membership`/`Post` graph in full.** We collapse this with `LegislatorMembership` (Tier 1 #5). Don't take the full OCD graph.
- **OCD's `Event`/`EventAgendaItem`/`EventRelatedEntity` for hearings.** Our planned skeletal `Hearing` is fine for P1a. Promote post-MVP.
- **Committee reports as first-class.** Federal-specific; rare at state level. Add only if WA emits them in a structured way.
- **NCSL vocab adoption.** Blocked on source access. Treat OCD vocab as proxy.

### Blocking unknowns — need direct user input

1. **WA Legislature SOAP's actual sponsorship payload shape.** Specifically: does it expose committee-as-sponsor (i.e., do we need Tier-1 #4-style `BillSponsorship.committee_id` for the WSL adapter to compile)? Confirm before deciding whether committee-as-sponsor is P1a or post-P1a.
2. **WA biennium identifier convention.** OCD's `LegislativeSession.identifier` is the source-of-truth string. WSL likely uses `"2025-26"` or `"68th"`. Need to pick one and use it everywhere. Recommend `"2025-26"` (matches WSL display + `usa-wa` slug style).
3. **Whether votes are P1a or P3.** Spec lists P1a as "status, sponsors, actions, dates" — explicitly *not* votes. But "describe HB-1234" with no vote info is a degraded answer. **Recommend reclassifying votes as P1a Tier-1 with a `VoteEvent`-only model (no per-legislator detail) and promoting per-legislator detail to P3.** Need user OK on this scope adjustment.
4. **Whether Tier-1 #5 (split `Legislator` from `LegislatorMembership`) is acceptable in P1a.** It's the largest single shape change recommended here and pushes complexity into Layer 2 before power-map (P2) is even building against it. Alternative: keep `Legislator` flat-with-session for MVP, accept duplication across bienniums, refactor at P2 when power-map identity resolution forces the issue. Need user call.
5. **NCSL source access.** Anyone have a downloaded copy of the NCSL glossary? If not, confirm we're OK treating the OCD vocab as the de-facto state-legislative standard.

---

## Appendix — file pointers

- OpenStates model files (read fresh from `github.com/openstates/openstates-core`, branch `main`):
  - `openstates/data/models/bill.py`
  - `openstates/data/models/people_orgs.py`
  - `openstates/data/models/vote.py`
  - `openstates/data/models/event.py`
  - `openstates/data/models/jurisdiction.py`
  - `openstates/data/common.py` (vocabularies)
- LegiScan manual: `LegiScan_API_User_Manual.pdf` Rev 2025-03-17 (Data Dictionary p. 34, Static Values p. 40).
- unitedstates/congress: `congress/tasks/bill_info.py` + wiki summary of bill JSON schema.
- NCSL: **inaccessible from environment.**
