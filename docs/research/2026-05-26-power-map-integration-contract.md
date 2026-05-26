---
title: power-map integration contract — research note
date: 2026-05-26
status: research (informs P2+ decision)
sources:
  - https://github.com/CannObserv/power-map (default branch `main`, inspected 2026-05-26)
  - github.com/CannObserv/power-map/blob/main/README.md
  - github.com/CannObserv/power-map/blob/main/AGENTS.md
  - github.com/CannObserv/power-map/blob/main/docs/CONVENTIONS.md
  - github.com/CannObserv/power-map/blob/main/src/core/schema.sql
  - github.com/CannObserv/power-map/blob/main/src/api/public/router.py
  - github.com/CannObserv/power-map/blob/main/src/api/public/orgs.py
  - github.com/CannObserv/power-map/blob/main/src/api/public/schemas.py
  - github.com/CannObserv/power-map/blob/main/src/api/admin/_identifiers_shared.py
  - github.com/CannObserv/power-map/blob/main/src/core/ingestion/pipeline.py
  - github.com/CannObserv/power-map/blob/main/docs/plans/2026-04-15-api-key-management-design.md
related:
  - docs/research/2026-05-26-archiver-integration-contract.md
  - docs/research/2026-05-26-watcher-integration-contract.md
  - docs/specs/2026-05-25-usa-wa-mvp-design.md §"Person/Org/Role/Assignment identity & PDC join"
---

# power-map integration contract — research note

## TL;DR

power-map is a **single-tenant editorial database** of People, Organizations, Roles, and Role Assignments, with a generic polymorphic `identifiers` table that does carry the external-ID semantics we need. The data model is excellent — ULID PKs, archive-not-delete, name i18n (BCP 47 / ISO 15924, deadname visibility, structured `person_name_parts` sidecar), org acronym canonicalization, polymorphic `entity_identifier_types`, temporal `role_assignments` with `is_current` / `start_date` / `end_date`, address normalization via an external `address-validator` service, and per-field `field_confidence` rows tagged with source reliability and validation status. As a **schema reference** for usa-wa's identity layer it is a near-direct fit.

As a **service usa-wa can integrate against today**, it is not ready. The programmatic surface is dramatically narrower than the data model: the public JSON API (`/api/v1/`, `X-API-Key` auth, server-to-server only by design) exposes exactly two endpoints — `GET /api/v1/orgs/search` and `GET /api/v1/orgs/{id}` — and only the org `getOrg` detail response carries identifiers in its payload. There is **no public people endpoint**, **no role / role_assignment / identifier query endpoint**, **no write surface of any kind on the public API**, **no SDK / client library**, **no change-bus** (no Redis Stream, no webhooks, no notifier-style dispatch), and **no observation-ingest endpoint**. All write paths are HTMX admin routes intended for humans, plus a CSV bulk-import pipeline (`scripts/import_cannabis_observer.py`) keyed on lowercased legal name with no identifier-driven match.

The user's framing of power-map as a "general-purpose external ID tracking facility used to push observations to an external primary system of record" is **partly contradicted by the repo**: the *schema* supports the external-ID model cleanly (`identifiers` × `entity_identifier_types`, with type slugs scoped by `entity_type`), but power-map itself is positioned as the *primary editorial system of record*, not a federator. The API-key management design doc (2026-04-15) explicitly calls the public API "programmatic (non-browser) R/W access … from outside the VM" and the proxy is publicized for that purpose — but the implementation is read-only on a single entity type today. There is no observation-resolution / fuzzy-match endpoint and no roadmap issue tracking one (3 open issues, all UI / data-quality polish).

Recent activity (last commit 2026-05-25) is intense but focused on admin-UI polish, name-i18n correctness, duplicate detection / merge ergonomics, and address-validator integration v1→v2. There is no in-flight work on either a wider public API or a write/observation interface. **For usa-wa's P2 horizon, power-map is read-mostly via a thin slice; the rest of the integration is direct DB or manual data-entry.**

---

## (a) Domain model

power-map's `src/core/schema.sql` is a single, well-commented, idempotent DDL applied via `apply_schema(conn)`. ULIDs (`TEXT` PKs) throughout, all timestamps `TIMESTAMPTZ`, archive-via-`archived_at` (NULL = active). Self-healing migration blocks for schema evolution. Postgres 15+ (`NULLS NOT DISTINCT` on partial unique indexes).

**Core entities (all four the user named are first-class):**

| Table | Shape |
|---|---|
| `organizations` | `id`, `active`, `parent_id` (self-ref, with `chk_no_self_parent`), `notes`, `created_at`, `updated_at`, `archived_at`. Hierarchy supported. |
| `organization_names` | 1:N — `(organization_id, name, name_type ∈ {legal, dba, former}, is_canonical)`. Partial unique `uq_org_canonical_name` enforces exactly one canonical name per org. Acronyms live in a **separate** table (`organization_acronyms`), not the names table. Display rendered via the `v_org_display_names` view as "Name (Acronym)". |
| `organization_acronyms` | 1:N — `(organization_id, acronym, is_canonical)`. Independently canonicalized. |
| `people` | `id`, `personal_pronouns`, `notes`, `archived_at`. Stripped-down by design — names live in `person_names`. |
| `person_names` | 1:N — `(name, name_type, is_canonical, locale [BCP 47], script [ISO 15924], sort_as, visibility ∈ {public, legal_only, hidden}, reading_of_id)`. **9 name types** (legal, preferred, alias, former, initials, maiden, religious, stage, deadname, reading, romanization, mrz, variant). Deadname auto-downgraded to `legal_only` by `trg_deadname_visibility` trigger. Display via `v_person_display_names`, which filters to `visibility='public'`. Hard project-wide rule: never log, never search, never expose legal-only / hidden rows outside the explicit "Show legal/historical names" disclosure. |
| `person_name_parts` | 1:0..1 sidecar of `person_names` — `(given_names[], family_names[], additional_names[], honorific_prefix, honorific_suffix, primary_identifier ∈ {family, given, patronymic, mononym})`. **Never auto-written** — populated only by upstream-provided structure or human-confirmed suggestion. `suggest_parts()` exists for triage but does not persist. |
| `roles` | Position definition at an org. `(organization_id, title, established_on, abolished_on, archived_at)`. Title uniqueness is enforced by `uq_role_org_title (organization_id, lower(title))` over non-archived rows. |
| `role_assignments` | "Person occupies a role during a time window." `(person_id, role_id, is_current, start_date, end_date, archived_at)`. `chk_current_no_end_date` enforces is_current ⇒ end_date IS NULL. Partial unique `uq_role_assignment_person_role_start (person_id, role_id, start_date) NULLS NOT DISTINCT` makes "same person + role + start_date" a duplicate even with NULL starts. |

**Supporting / polymorphic tables:**

| Table | Notes |
|---|---|
| `addresses` | Standardized via the external `address-validator` service (v2 since 2026-05-22, #155). Carries lat/lon, JSONB components, normalized line fields. |
| `entity_addresses` | Polymorphic join — `(entity_type ∈ {organization, person}, entity_id, address_id, address_type ∈ {mailing, physical, other}, display_name)`. |
| `contact_methods` | Polymorphic — `(entity_type ∈ {organization, person, role_assignment}, entity_id, contact_type ∈ {email, phone}, value, display_label)`. Phone E.164 via `PhoneNormalizer`, email via `EmailNormalizer`. |
| `links` | Polymorphic — `(entity_type ∈ {organization, person, role, role_assignment}, entity_id, url, link_type_id, is_active)`. Natural-key unique `uq_links_entity_url(entity_type, entity_id, url, link_type_id)` (issue #142, May 2026). Types live in `link_types` (slug, display_name, `is_social`); 25+ seeded slugs (twitter, bluesky, linkedin, mastodon, wikipedia, github, …). |
| `link_types` | Lookup. Seeded list includes `wikipedia`, `linkedin`, `twitter`, `bluesky`, `mastodon`, `instagram`, `youtube`, `facebook`, `github`, `homepage`, `profile`, `directory`, `press`, etc. `is_social` flag for distinguishing social-vs-other. |
| `bcp47_locales`, `iso15924_scripts` | FK-validated lookup tables for `person_names.locale` / `.script`, seeded from `langcodes` + `pycountry` via `scripts/seed_locales_scripts.py`. pg_trgm GIN indexes power typeahead. |
| `duplicate_dismissals` | Operator-curated "these two are not duplicates" — keyed `(entity_type, entity_a_id, entity_b_id, dismissed_by, dismissed_at)`. Used by the duplicate-detection page to keep dismissed pairs from re-surfacing. |
| `app_users` | Thin identity anchor keyed by `X-ExeDev-UserID`, lazy-upserted on first admin login. |
| `api_keys` | Static, SHA-256-hashed credentials with prefix-for-display (`pm_a3f8c2…`). `last_used_at` touched on every authenticated request. |
| `import_batches`, `import_provenance` | CSV ingestion audit trail. Batches keyed by hash of input files (`ON CONFLICT (file_hash)` makes re-runs idempotent). Provenance is per-row, action ∈ {created, matched, error}, with the raw input dict stored as JSONB. |
| `field_confidence` | Per-field per-entity confidence record: `(entity_type, entity_id, field_name, value_hash, source_reliability, validation_status ∈ {confirmed, unconfirmed, failed, not_attempted}, validation_detail, assessed_by)`. Designed for layered ingestion confidence; not currently surfaced via API. |

**Display-rendering rules (load-bearing for any consumer):**

- Always use `v_org_display_names` / `v_person_display_names` for display. The view definitions carry the "Name (Acronym)" formatting and the visibility filter; bypassing them re-introduces deadname leaks and acronym-omission bugs. The CONVENTIONS doc is forceful on this point.
- Raw `person_names` access must AND-append `visibility='public'` or call `visible_names_filter()` from `src.core.db`. A unit test (`tests/core/test_visible_names_filter.py::test_no_unguarded_person_names_queries`) greps the codebase for unguarded access; new direct-access call sites must register in `ALLOWED_DIRECT_ACCESS` with a justification comment. usa-wa adapter code MUST honor this rule when pulling names.

**Mapping to usa-wa's current schema sketch:**

| usa-wa concept | power-map analog | Notes |
|---|---|---|
| Legislator (WSL member) | `people` + `person_names` (legal) + `role_assignments` referencing `roles` at "WA State Senate" / "WA State House" orgs | Multi-term legislators get multiple `role_assignments` rows (open-ended on `is_current=TRUE` + `end_date=NULL`); the i18n layer trivially supports e.g. Sen. Bob Hasegawa's name handling. |
| Filer (PDC) | `organizations` (PAC, candidate committee, …) OR `people` (individual filers) | `entity_identifier_types.slug` distinguishes via `org_wa_pdc` vs. `person_wa_pdc` — already seeded. |
| Sponsor of a bill | `people` → `role_assignment` at sponsoring chamber | Bill linkage stays in usa-wa; the person identity hops over to power-map. |
| Funded-by relationship | Not modeled in power-map | power-map does not carry contribution amounts, donation events, or money flow. That belongs in usa-wa or a new sibling. |
| Committee membership | `role_assignment` at the committee org (committee modeled as an organization, the legislator's seat as a role) | Works cleanly but requires usa-wa to author committee orgs + role definitions in power-map. |

**What power-map does NOT model:**

- Bills, ballots, statutes, agency actions, rule-making — out of scope. usa-wa owns these.
- Money flow / contributions / expenditures — out of scope. The PDC data lives in usa-wa.
- Events / meetings / hearings — out of scope. Watcher / archiver / usa-wa territory.
- Citizenship, immigration status, race, religion, gender (beyond `personal_pronouns` as a free-text field) — intentionally not modeled.

---

## (b) External ID tracking

This is the load-bearing question for the integration, and the answer is: **the schema supports it cleanly; the API does not expose it cleanly.**

### Schema

Two tables:

```sql
CREATE TABLE entity_identifier_types (
    id           TEXT PRIMARY KEY,
    entity_type  TEXT CHECK (entity_type IN ('organization', 'person', 'role_assignment')),
    slug         TEXT UNIQUE,           -- 'person_wa_pdc', 'org_ubi', …
    display_name TEXT,                  -- "WA PDC"
    full_name    TEXT,                  -- "Washington State Public Disclosure Commission"
    …
);

CREATE TABLE identifiers (
    id                        TEXT PRIMARY KEY,
    entity_id                 TEXT,
    entity_identifier_type_id TEXT REFERENCES entity_identifier_types(id),
    value                     TEXT,
    created_at                TIMESTAMPTZ
);
CREATE INDEX idx_identifiers_entity ON identifiers(entity_identifier_type_id, entity_id);
```

`entity_type` is encoded in the *type* row, not the *identifier* row — so `org_wa_pdc` (entity_type=organization) and `person_wa_pdc` (entity_type=person) are distinct type-slugs that can carry the same PDC ID string but resolve to different entity tables.

**Seeded slugs (`src/core/schema.sql`):**

| slug | entity_type | full_name |
|---|---|---|
| `org_ubi` | organization | Washington Unified Business Identifier |
| `org_wslcb` | organization | WA State Liquor and Cannabis Board License |
| `org_wa_pdc` | organization | Washington State Public Disclosure Commission |
| `person_wa_pdc` | person | Washington State Public Disclosure Commission |
| `person_ssn` | person | United States Social Security Number |
| `role_wa_pdc` | role_assignment | Washington State Public Disclosure Commission |

**Identifier types not seeded but expected by usa-wa:**

- `person_wsl_member_id` (WA Legislature member ID — the join key from usa-wa Legislator → power-map Person)
- `person_wa_voter_id` (potentially)
- `org_wa_secretary_of_state_ubi` (alias of `org_ubi`? or separate?)
- `org_wsl_committee_id` (for legislative committees if modeled as orgs)

These would be added by the dashboard at `/admin/settings/identifier-types/` (admin-UI managed, not migration-managed). Adding them is trivial — the table is fully dynamic. **Action for P2:** confirm with power-map owner that we can add `person_wsl_member_id`, and that he is comfortable with usa-wa being the issuing authority for that slug (vs. owning it editorially in power-map).

### Query path: "find the power-map Person whose WSL member_id is 42"

The data is there; the API is not.

**What works programmatically (today):**

- `GET /api/v1/orgs/{id}` returns the org's `identifiers` array in its body. So if you have a power-map org_id, you can read identifiers back.
- That's it. There is no `GET /api/v1/identifiers?slug=person_wsl_member_id&value=42`, no `GET /api/v1/people`, no `GET /api/v1/people/by-identifier`, no batched lookup.

**What works at the dashboard layer (humans only, behind exe.dev SSO):**

- HTMX admin routes on `/admin/people/{person_id}/identifiers/...` perform full CRUD on identifiers, but the response bodies are HTML partials destined for HTMX swaps, not JSON. Useful for editorial work; useless for service-to-service queries.

**What works at the DB layer:**

- A trivial SQL query:
  ```sql
  SELECT i.entity_id
  FROM identifiers i
  JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
  WHERE t.slug = 'person_wsl_member_id' AND i.value = '42';
  ```
  This is fast (`idx_identifiers_entity` covers it; or add `(entity_identifier_type_id, value)` if needed). usa-wa cannot run this from outside the VM unless we either (a) talk Postgres directly (cross-service DB access, not how this cohort is structured), or (b) get a JSON endpoint added.

**Inferred uniqueness semantics:** the `identifiers` table has **no** uniqueness constraint on `(entity_identifier_type_id, value)` — i.e., the same `wsl_member_id=42` could in principle be attached to two power-map people. That's a deliberate-or-accidental looseness; it suggests power-map is designed to tolerate noisy ingestion (CSV imports may produce conflicts) and resolve them via the dup-detect/merge flow. usa-wa should query for *all* matches and treat >1 result as an editorial-attention signal, not crash.

---

## (c) API / SDK surface

### Public REST API (`/api/v1/`)

- **Auth:** `X-API-Key: pm_<32 hex chars>` header. 403 on missing, 401 on invalid. SHA-256 hash matched against `api_keys.key_hash`; `last_used_at` touched on success. Keys created in the admin UI under Settings → API Keys; shown once in a modal, never retrievable thereafter.
- **Versioning:** path-based (`/api/v1/`). Convention is "bump the prefix when introducing breaking changes."
- **Response envelope (lists):** `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`. Fetch `limit+1` for `has_more` flag. Default `limit=10`, capped at 50.
- **Response model:** Pydantic `response_model` + explicit `operation_id` required on every route — OpenAPI schema is fully typed. No `dict[str, Any]` returns. ISO 8601 timestamps with `Z` suffix via `_fmt_ts()`.
- **CORS:** explicitly disabled — "the public API is server-to-server only (no browser callers)."

**Endpoints (every public route, exhaustive):**

| Verb + path | operation_id | Response | Notes |
|---|---|---|---|
| `GET /api/v1/` | (none) | `{"status": "ok", "version": "v1"}` | Health check; requires valid API key. |
| `GET /api/v1/orgs/search?q=&limit=&offset=&include_archived=` | `searchOrgs` | `OrgSearchResponse` | ILIKE substring search across `organization_names.name`, `organization_acronyms.acronym`, and `organization_names` variants. Empty `q` returns empty `data`. Limit hard-capped at 50. Ordered by match quality then canonical name. |
| `GET /api/v1/orgs/{org_id}` | `getOrg` | `OrgDetail` | Returns base fields + arrays: `names[]`, `acronyms[]`, `identifiers[]` (with `type_slug` and `value`). |

`OrgDetail.identifiers` is `list[OrgIdentifier]`:
```python
class OrgIdentifier(BaseModel):
    id: str          # ULID of the identifier row
    type_id: str     # ULID of the entity_identifier_type
    type_slug: str   # e.g. 'org_wa_pdc'
    value: str       # e.g. '12345'
```

**Endpoints that DO NOT EXIST today (load-bearing for usa-wa P2):**

- `GET /api/v1/people/{id}` — no public people detail
- `GET /api/v1/people/search` — no public people search
- `GET /api/v1/people/by-identifier?slug=&value=` — no identifier-keyed lookup
- `GET /api/v1/orgs/by-identifier?slug=&value=` — same
- `GET /api/v1/identifiers?slug=&value=` — no global identifier search
- `GET /api/v1/role-assignments` — no public role-assignment access
- `POST /api/v1/observations` (or anything in this shape) — no client-write surface
- Any DELETE / PATCH / POST — public API is read-only

### SDK / client library

**None.** Unlike archiver (`/clients/python/` → `archiver-client` v3.2.x, path-installed via `[tool.uv.sources]`) or notifier (consumed by watcher as `notifier-client` SDK), power-map ships **no** generated or hand-rolled client. The OpenAPI doc is available at `/openapi.json` once the server is running (FastAPI default), and `openapi-python-client` could generate a thin client — but doing so for ~2 endpoints is barely worth the build complexity.

For P2, usa-wa would talk to power-map with `httpx.AsyncClient` directly. Two endpoints + a hash-able auth header. Add it as `src/adapters/power_map/client.py` in usa-wa, ~80 LOC.

### CLI / scripts

`scripts/import_cannabis_observer.py` — operator-run, CSV-driven bulk import. Not a service-callable API.

### Admin HTMX dashboard

Substantial — ~40 admin route modules covering CRUD for people, orgs, names, acronyms, addresses, contacts, links, identifiers, roles, role assignments, name suggestions, locale/script typeaheads, merge flows for both org and person duplicate detection, archive/unarchive, hard delete, and settings (link types, identifier types, API keys). Auth: exe.dev proxy headers (`AdminUser = Depends(get_admin_user)`), redirect to login if absent. **All HTML responses; not callable from another service.** Useful for humans curating the data; irrelevant to programmatic integration.

### Change-bus / events / webhooks

**None.** No Redis Stream, no Kafka, no AMQP, no webhook dispatch, no notifier-style outbound. power-map does not emit anything when a Person or Org is created/updated/merged. Consumers who need fresh data must poll, and "poll what?" is currently constrained to the two GET endpoints above.

---

## (d) Read flow integration with usa-wa

usa-wa's existing nullable columns `Legislator.powermap_person_id`, `Filer.powermap_org_id`, `Filer.powermap_person_id` are the integration plane. The read flow has three stages:

1. **Discovery / first link.** Resolve a usa-wa Legislator (or PDC Filer) row to its power-map UUID. This happens once per legislator, once per filer.
2. **Hydration.** Pull the power-map record for display / cross-referencing.
3. **Refresh.** Detect when power-map has changed (a name correction, a new role assignment, an archive) and re-pull.

**Stage 1 — discovery:** No clean API path today.

- **Org-only happy path** (for PDC Filers that are orgs): `GET /api/v1/orgs/search?q=<filer_name>` → for each candidate, `GET /api/v1/orgs/{id}` → scan `identifiers[]` for `type_slug='org_wa_pdc'`, `value=<filer_id>`. Works but is N+1 (search returns up to 50, detail call per candidate to inspect identifiers).
- **Person path** (for individual filers, and for legislators): no programmatic path. Operator workflow only.
- **Performance note:** the search endpoint runs `ILIKE '%q%'` (substring), no trigram pre-filter, no `gist_trgm_ops` GIN on names (though `pg_trgm` *is* enabled — the index just doesn't exist on `organization_names.name`). Cheap at current data volumes (~hundreds of orgs from the cannabis-observer import); could become a bottleneck at thousands.

**Stage 2 — hydration:** `GET /api/v1/orgs/{id}` returns names, acronyms, identifiers, archive state, parent_id. Sufficient for our display needs on the org side. For people, **there is no equivalent endpoint at all** — we cannot hydrate a Person from power-map without DB access or an admin scrape.

**Stage 3 — refresh:** No change-bus. usa-wa would need to poll the two GETs on a schedule (daily? weekly?). With ~200 WSL legislators and growing PDC filers, even a daily full re-poll is cheap, but it surfaces no "what changed" signal — we re-pull regardless.

**Ergonomics:**

- **Batching:** none. No `GET /api/v1/orgs?ids=a,b,c`. usa-wa must iterate one HTTP call per org.
- **Pagination:** standard `limit/offset/has_more` on search; capped at `limit=50`.
- **ETag / conditional GET:** not implemented. No `Last-Modified`, no `If-None-Match`. Re-poll is full-payload every time.
- **Rate limiting:** none enforced server-side. (`last_used_at` is updated per-call but no quota.)
- **Idempotency keys:** not relevant — read-only.

**Recommended P2 read flow:**

1. Add `power_map_person_wsl_member_id`, `power_map_person_wa_pdc`, `power_map_org_wa_pdc` as identifier-type slugs in power-map (admin UI). One-time editorial action; not in our code.
2. Stand up `src/adapters/power_map/client.py` in usa-wa with `httpx.AsyncClient`, two methods: `search_orgs(q)` and `get_org(org_id)`.
3. For the **filer→org** link, build a daily reconciliation job: walk our `Filer` table where `entity_type='organization' AND powermap_org_id IS NULL`, search power-map, match candidates whose `identifiers[].type_slug='org_wa_pdc'.value` equals our `Filer.pdc_id`. On exactly-one match, write `powermap_org_id`. On zero matches, leave NULL and flag for editorial. On multiple, flag as ambiguous.
4. For the **legislator→person** and **filer→person** links, **defer to P3.** There is no read path that supports this without either (a) shipping a new power-map endpoint, or (b) granting usa-wa direct DB access to power-map. Both are coordination work.

---

## (e) Write flow

The user's framing — "usa-wa says 'I saw a WSL member named Sen. Jane Doe at member_id=42; here's what I know — match her to a known Person or create a new one'" — is **not supported by the current power-map API.**

**What does NOT exist:**

- No `POST /api/v1/observations` or `POST /api/v1/people/observations` endpoint.
- No "match or create" / "find-or-upsert" endpoint anywhere.
- No fuzzy-match / resolution endpoint usa-wa could call to get back a candidate set.
- No bulk-write endpoint (other than the operator-only CSV script).
- No "observe identifier" endpoint that would attach a `(type_slug, value)` to an existing person and emit an editorial-attention row if the person didn't exist.

**What exists as the closest analog:**

- The CSV bulk-import pipeline (`scripts/import_cannabis_observer.py` → `src/core/ingestion/pipeline.py`). It is a **multi-pass EVTL pipeline** (Extract / Validate / Transform / Load) that:
  - Reads three CSVs (orgs, people, roles).
  - Validates each row through a Pydantic model (`PersonRow`, `OrgRow`, `RoleRow`).
  - Normalizes via dedicated normalizers (`PhoneNormalizer`, `EmailNormalizer`, `UrlNormalizer`, `IdentifierNormalizer`, `FallbackAddressNormalizer` → external `address-validator`).
  - **Dedup-matches on lowercased canonical legal name only**, not on any identifier. For person rows: `WHERE lower(n.name)=$1 AND n.name_type='legal' AND n.is_canonical=TRUE AND n.visibility='public'`. For org rows: `WHERE lower(n.name)=$1 AND n.is_canonical=TRUE`. This is the part most relevant to the user's "match an existing Person or create a new one" question — but it's batch-only, file-only, and the match key is the wrong one (name, not identifier).
  - Writes `import_provenance` rows per source-row with action ∈ {created, matched, error} and the raw row dict in JSONB.
  - Writes `field_confidence` rows per loaded field with `source_reliability` (default 0.8) and `validation_status`.
  - Is **idempotent on file_hash**: `ON CONFLICT (file_hash) DO UPDATE SET id=import_batches.id` means re-importing the same triple of CSVs is a no-op.
- The HTMX admin routes (full CRUD, including `POST /admin/people/{id}/identifiers/`). These are intended for human editors, not service-to-service writes. They expect `X-ExeDev-UserID` headers, return HTML partials, and have HTMX-specific flash/redirect ergonomics. usa-wa **could** drive them as a service-to-service path (cookie / proxy header forgery, parse HTML responses) but that is a hostile-takeover integration, not a contract.

**If usa-wa wanted to push an observation today (P2+):**

There is no clean path. The realistic options, ranked:

1. **Editorial-CSV pump (push-out-of-band).** usa-wa exports a People + Orgs + Roles CSV nightly, scp's it to a power-map-managed location, an operator runs `scripts/import_cannabis_observer.py` against it. Inherits the batch's source-reliability, the lowercased-legal-name dedup behavior (which will under-match — "Jane Doe" vs. "Jane M. Doe" vs. "Jane Doe (D-37)"), and the lack of identifier-keyed match (so two distinct WSL members with similar names will collide).
2. **New endpoint contribution.** usa-wa proposes a `POST /api/v1/observations` endpoint upstream. Schema something like: `{entity_type: 'person', identifier: {slug, value}, names: [...], assignments: [...], source_reliability: 0.9}` with a response that either returns the existing power-map_id (match by identifier first, then by exact name) or creates a new entity. Power-map would write `import_provenance` + `field_confidence` rows the same way the CSV pipeline does, treating each call as a single-row "batch" (`imported_by='usa-wa-observation'`). Probably 1–2 weeks of upstream work; needs design buy-in from the power-map owner.
3. **Direct DB writes (cross-service).** Bad. Violates the per-service-DB boundary, requires schema-lock-step coordination, defeats the audit-trail design of `import_provenance`. Mentioned for completeness; should not be done.

**The fundamental shape mismatch:** power-map's ingestion pipeline expects *batches with manifests* (file_hash, source_reliability, validation status) and dedupes by *legal name canonicalization*. usa-wa's write story expects *streaming observations* with *identifier-keyed match*. The schema supports the latter trivially; the ingestion pipeline assumes the former. An observation API is small new code, not a refactor — but it does not exist today.

---

## (f) Cross-cohort role

The user's framing was: "power-map is a general-purpose external ID tracking facility … used to push data to an external primary system of record."

The repo **partly contradicts this**.

**What the repo says (explicit):**

- `README.md`: "Maps political and corporate power: people, organizations, roles, and their temporal relationships." — power-map *is* the map.
- `AGENTS.md`: "Web service for mapping political and corporate power" — same.
- `docs/plans/2026-04-15-api-key-management-design.md`: "Enable programmatic (non-browser) **R/W** access to power-map from outside the VM via static API keys." — the *intent* is R/W; the implementation has shipped only R.
- The data model: `organizations`, `people`, `roles`, `role_assignments` are first-class with full editorial tooling, name canonicalization, archive lifecycle, duplicate detection, merge flows, and audit trails. **This is the shape of a primary system of record**, not a federation layer.
- `field_confidence` carrying `source_reliability` and `assessed_by` *does* support a "multiple upstream sources contribute" model — i.e., the schema accepts that power-map is downstream of multiple feeders. But power-map *is* the canonical entity it points to; it doesn't store a foreign URI / external_system column on `people` or `organizations` themselves.

**What the repo doesn't say:**

- There is no documentation positioning power-map as an upstream-of-something-bigger. No mention of pushing data out. No outbound integration code. No "system of record" hand-off.

**Reconciling with the user's framing:**

The user is closer to a future-state vision than the current implementation. The schema *could* serve as the canonical ID hub for the whole cohort (every sibling service writes observations in, power-map resolves them, every sibling service reads `power_map_person_id` for its own foreign-key columns). The user's mental model — "general-purpose external ID tracking facility" — fits the data model. But:

- The "push observations" half is not yet built.
- The "federate to a larger primary" half is not articulated anywhere in the repo. power-map *is* the primary.

**Realistic assessment:** power-map is a **canonical-identity service for the cohort**. Siblings (usa-wa, others) need to think of it as the source of truth for "who is this person, what org does this PDC ID refer to" — not as a passthrough. The "external ID tracking" framing is *one* of its functions (via the `identifiers` table); the entity tables themselves are the bigger story. usa-wa should integrate against it as a peer system of record, not as a federation hop.

---

## (g) Maturity assessment

power-map is **architecturally mature, programmatically immature**.

**Mature signals:**

- 2+ months of dense, focused commits (commit history shows ~200 commits Mar–May 2026, multi-issue parallel agent batches, an architectural-refactor backlog at issue #77–#86 already cleared).
- Schema is at v0.1.0 but has self-healing migration blocks and is field-tested.
- Test suite has integration + unit separation, session-scoped `db_pool`, hardened teardown patterns (issue #150 fixture-leak fix was recent and methodical).
- Address validator integration is on its second major API version (#155, May 2026).
- Name i18n is comprehensive — BCP 47 / ISO 15924, deadname auto-downgrade trigger, structured parts sidecar, reading rows linked via `reading_of_id`, ICU-collation sorts. This is more thorough than most production HR systems.
- Duplicate detection, merge flows, archive lifecycle, last-identity guards — all of the data-quality machinery you'd want is there.
- Pre-commit hooks (ruff + pytest + ESLint + Prettier + vitest), 80% coverage gate, version-sync hook between `pyproject.toml` and `package.json`.
- Active git-worktree-based parallel development workflow.

**Immature signals:**

- **Public API is 2 endpoints.** No people, no role_assignments, no identifiers, no writes. The intent (`api-key-management-design.md`) was R/W, but only R has shipped, and only for orgs.
- **No SDK.** Sibling cohort services (archiver, notifier) ship Python clients; power-map doesn't.
- **No change bus.** No way to know power-map changed without polling.
- **No observation / find-or-create API.** The CSV pipeline is the only write path beyond the admin UI.
- **Only 3 open issues**, all small UI / data-quality (dark mode toggle, i18n infrastructure, dup-count cache under multi-worker). **No issue tracks "expand public API to people"**, **no issue tracks "add observation endpoint"**, **no issue tracks "add SDK."** This is a strong signal that broader API work isn't planned.
- Version `0.1.0` despite the maturity — semantic versioning hasn't been activated yet; presumably waiting on a stability commitment.

**Gating issues for usa-wa P2:** none in the power-map issue tracker today. The work usa-wa needs is *new feature work upstream*, not "wait for X to merge."

**Verdict:**

- **Architecturally:** ready to be a cohort-wide identity service.
- **For usa-wa read-only on orgs:** P2-ready today. ~1 day of adapter code on our side.
- **For usa-wa read-only on people:** **not P2-ready.** Requires a new endpoint upstream. Estimate 2–3 days of upstream work (search + detail + identifier-keyed lookup); probably more given the visibility rules that have to be respected in any JSON serialization. Coordinate with power-map owner.
- **For usa-wa write / observations:** **not P2-ready, not even close.** Requires either a new endpoint (~1–2 weeks upstream) or an editorial-CSV pump (operator-driven, brittle, identifier-blind dedup). Defer to P3.

---

## Recommendation block (decision-ready)

**Posture for P2: read-only thin slice on orgs; defer people + writes to P3.**

Concretely:

1. **Add power-map identifier-type slugs (one-time editorial, before code).** Coordinate with power-map owner to add at minimum `person_wsl_member_id` (entity_type=person), and confirm `org_wa_pdc` / `person_wa_pdc` (already seeded) are the right slugs for the PDC join. Owner adds these via `/admin/settings/identifier-types/`.
2. **Build `src/adapters/power_map/client.py` in usa-wa.** Thin `httpx.AsyncClient` wrapper around `GET /api/v1/orgs/search` and `GET /api/v1/orgs/{id}`. API key in `/etc/usa-wa/.env` as `POWER_MAP_API_KEY`. Two methods, ~80 LOC. No SDK dependency.
3. **Populate `Filer.powermap_org_id` opportunistically.** Daily reconciliation job: for each unlinked org-shaped Filer, search by name, fetch candidate details, match on `identifiers[].type_slug='org_wa_pdc'.value == filer.pdc_id`. Exactly-one match → write `powermap_org_id`. Otherwise → leave NULL and emit an `editorial_attention` row (new usa-wa table or just a log line at P2).
4. **Do NOT populate `Legislator.powermap_person_id` in P2.** No public people-read path exists. Either (a) accept that the join is operator-curated in P2 (admin enters power-map ULIDs manually into usa-wa via our own admin), or (b) push power-map to ship a public people endpoint and absorb that as a P2 gating dependency. Recommend (a) for predictability.
5. **Make zero writes to power-map in P2.** No observations, no CSV pumps. usa-wa is read-only with respect to power-map for the entire P2 horizon.
6. **In MCP / REST agent responses**, when usa-wa knows a `powermap_*_id`, include it in the entity payload as a foreign-system link. Don't proxy power-map data through us — link out, let downstream consumers fetch directly if they have an API key.

**Promotion gate (P2 → P3, when to expand integration):**

All of:

- (i) Power-map ships `GET /api/v1/people/by-identifier?slug=&value=` or equivalent. Even just `GET /api/v1/people/{id}` plus an identifier-keyed list endpoint unblocks the legislator linkage.
- (ii) Power-map ships either a `POST /api/v1/observations` endpoint OR explicitly agrees that the editorial-CSV pump is the supported write path and accepts a usa-wa-driven CSV cadence.
- (iii) Power-map publishes an SDK (path-installable Python client mirroring `archiver-client`) — or we accept that hand-rolled `httpx` is fine for the call volume.
- (iv) usa-wa's editorial backlog has surfaced ≥ ~10 cases where the missing power-map linkage is blocking agent answers. If we never actually need the join in production, P3 is not worth the coordination cost.

**Estimated effort, P2 path:** ~2 days of usa-wa work. Adapter client (~½ day), reconciliation job (~½ day), tests (~½ day), wiring into citation responses (~½ day). No upstream dependency required if power-map owner pre-seeds the identifier-type slugs.

**Estimated effort, P3 path (if all gates trip):** ~1–2 weeks of usa-wa work + 1–2 weeks of coordinated upstream work in power-map. Includes a person-side adapter, a legislator-reconciliation job, optional observation-push wiring, and the schema-and-test work upstream for the new endpoints.

---

## Blocking unknowns

These need user or sibling-team input before P2 implementation starts:

1. **Identifier-type slug naming for the WSL member join.** `person_wsl_member_id` is the obvious slug, but the slug naming convention in the seeded data is `<entity>_<jurisdiction>_<system>` (e.g. `person_wa_pdc`, `org_wa_pdc`). Should it be `person_wa_wsl_member`? `person_wa_legislature`? Pick one; this is the join key forever after. **Action:** propose `person_wa_wsl` (Washington State Legislature member) and confirm with power-map owner; he writes the row, we depend on the slug.
2. **Is power-map's identifier uniqueness enforced?** The schema has no `UNIQUE (entity_identifier_type_id, value)` constraint. So `wsl_member_id=42` could in principle attach to two power-map people. Is that intentional (the merge flow handles it) or a missed constraint? **Action:** confirm with power-map owner; if "merge flow handles it," our adapter must treat >1 result as ambiguity and not silently pick one.
3. **Will power-map ship a public people endpoint in 2026?** Without it, the legislator→power-map_person_id join is operator-curated in usa-wa indefinitely. **Action:** ask the owner whether he sees `GET /api/v1/people/by-identifier` as on his roadmap or pushed-out indefinitely. Bias our P2 plan accordingly.
4. **CSV pump or observation API for write?** If the long-term write story is the CSV pump, we should start designing the usa-wa→CSV exporter early and coordinate the schedule with the operator. If it's a new endpoint, defer all write work to P3 and just file a tracking issue. **Action:** explicit decision from the owner; "we'll figure it out later" defaults to "indefinite CSV-pump."
5. **Federated identity vs. canonical identity.** The user's framing ("push data to an external primary system of record") implies power-map is intermediate. The repo behaves like the primary. If there's a *bigger* canonical store coming (the user said "external primary system"), what is it? — because if power-map is itself routing-to-something, we should integrate against the bigger thing, not the routing layer. **Action:** clarify the user's mental model; the repo does not name a referent.
6. **API key issuance and rotation.** API keys are "show once" — what's the cohort-wide rotation cadence, and who pages whom when a key needs rotation? **Action:** standard secret-management policy question; not power-map-specific but unresolved.
7. **Coverage of WA-specific identifier types.** We need `person_wa_wsl` (or whatever slug — see #1). Do we also need `org_wa_committee_id` for legislative committees, `person_wa_voter_registration_id` for cross-system identity, `org_wa_sos_filing_id` for Secretary of State business records? **Action:** scope the WA identifier-type seed once with the user; one-time editorial pass at the power-map admin UI.

---

## Appendix — endpoint inventory (exhaustive, today's main branch)

For completeness, this is the *entire* programmatic surface of power-map today:

**Public API (`X-API-Key` auth, server-to-server):**

- `GET /api/v1/` — health check
- `GET /api/v1/orgs/search` — org search by name/acronym/variant substring
- `GET /api/v1/orgs/{org_id}` — org detail with names, acronyms, identifiers

**Admin (`X-ExeDev-UserID` auth, HTMX HTML responses, human-only):**

- Dashboard: `/admin/`, `/admin/activity/`, `/admin/imports/`
- Orgs: `/admin/orgs/`, `/admin/orgs/{id}/`, plus subpaths for names, acronyms, addresses, contacts, links, identifiers, roles, merge — full CRUD on each
- People: `/admin/people/`, `/admin/people/{id}/`, plus subpaths for names (with suggest-parts), addresses, contacts, links, identifiers, assignments, merge — full CRUD
- Roles: `/admin/roles/`, `/admin/roles/{id}/`, plus inline assignment CRUD
- Settings: `/admin/settings/`, `/admin/settings/link-types/`, `/admin/settings/identifier-types/`, `/admin/settings/api-keys/`
- Static: `/static/admin/...`, `/static/images/...`

**Operator scripts (CLI, single-VM):**

- `scripts/import_cannabis_observer.py` — CSV bulk import (3 files: orgs, people, roles)
- `scripts/seed_locales_scripts.py` — seed BCP 47 / ISO 15924 lookup tables
- `scripts/setup-db.sh` — provision local Postgres
- `scripts/dedup_links.py` — links table dedup audit
- `scripts/deduplicate_roles.py` — role dedup audit
- `scripts/migrate_person_names_locale_script.py` — one-time data migration
- `scripts/migrate_person_name_parts.py` — one-time data migration
- `scripts/cleanup_person_name_data_quality.py` — data quality sweep
- `scripts/analyse_person_name_parts.py` — analysis tool

**That's the entire integration surface.** Anything else — observations, change-feeds, batched lookups, SDK — does not exist today and is not in the open-issue backlog.
