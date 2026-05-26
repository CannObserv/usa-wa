# usa-wa MVP architecture & phasing

- **Date:** 2026-05-25 (revised 2026-05-26)
- **Status:** approved — proceeds to implementation planning
- **Scope:** Phases P0 through P1c (foundation + keystone vertical slice). P2+ sketched, not specified.

## Problem

Build a Washington State law/regulation/policy clearinghouse that ingests primary sources (WA Legislature SOAP, WA PDC, RCW), normalizes them into a citable canonical model, and exposes the result over MCP (primary) and REST (secondary). The service is the read model for the CannObserv cohort and the data backbone for an AI agent answering WA-policy questions.

The architecture is explicitly designed for horizontal (other US states), vertical (federal, municipal), and cohort-wide reuse. usa-wa is the WA deployment of a shared clearinghouse framework. See [Workspace shape](#workspace-shape).

Constraint context:

- Pre-production workshop on a single exe.dev VM; live service is the systemd unit on port 8000.
- Cohort majority defaults already chosen during bootstrap (Pydantic Settings, monolithic models layout, systemd deploy). See `AGENTS.md`.
- Primary-source bytes are stored only as a **time-bounded cache**. Archiver (`CannObserv/archiver`) is the long-term system of record. Watcher (`CannObserv/watcher`) may eventually own scheduled-refresh orchestration.
- Citations and confidence are first-class.
- Sibling services (`observo`, `archiver`, `watcher`, `power-map`) are upstream contributors; their integration sequence depends on each one's maturity. usa-wa is the read layer of the cohort.

## MVP scope (decisions)

| Decision | Value |
|---|---|
| First consumer | AI agent via MCP |
| Question shape | Bill lifecycle + lobbying/money (Bill + PDC together) |
| Domain coverage | WA Legislature bills, RCW (enacted law), PDC (lobbying + contributions). **Defer:** WAC/WSR (administrative rules). |
| History horizon | Current biennium (2026–) daily-refreshed + on-demand cached; previous biennium (Dec 2024 – Mar 2026) backfilled as a one-shot; older history on-demand only. |
| Freshness | Daily scheduled refresh + on-demand fetch with cache. No hourly-during-session in MVP. |
| Identity resolution | Defer to power-map (P2). usa-wa stores source IDs without cross-source linking in P1. |
| MCP transport | Streamable HTTP, mounted on the existing FastAPI process. |
| Auth | Single shared bearer token in `/etc/usa-wa/.env` for MVP; per-client tokens in P4. |
| DB identifiers | **ULIDs** for every PK and FK across the canonical and source-namespaced schemas. |
| Jurisdiction encoding | 3-letter country + 2-letter state/province (`usa-wa`) everywhere — package names, schemas, slugs, `jurisdiction_id` values. |
| Raw payload retention | Configurable TTL per source (default 30 days). Archiver is the long-term store. |

The agent question shape that grounds the model:

> "Tell me about HB-1234: what is it, who sponsored it, where is it in the process, what does it change in RCW, who's lobbying on it, who funded the sponsors?"

## Phasing

| Phase | Scope | Shippable MCP capability |
|---|---|---|
| **P0** | Workspace + Postgres + adapter contract; **discovery tracks:** Archiver schema/API, Watcher schema/API, multi-state legislative IA research | (foundation) |
| **P1a** | Spine + WA Legislature bills + first MCP tool | "describe HB-1234" — status, sponsors, actions, dates |
| **P1b** | RCW corpus + Bill↔RCW link | "what does HB-1234 actually do?" — affected RCW sections |
| **P1c** | PDC adapter (stored, un-linked to legislators) | "lobbying on HB-1234" + degraded money-around-sponsors |
| **P2** | power-map adapter (identity authority); evaluate Watcher delegation | upgrades earlier tools with cross-source identity |
| **P3** | WA Legislature depth + backfill protocol | broader coverage, 2025-26 biennium loaded |
| **P4** | Admin UI + REST stabilization + per-client tokens | operational maturity |
| **P5+** | archiver/observo/GIS/SDK as siblings mature | (out of MVP) |

Rationale: vertical-slice first. The canonical model gets pressure-tested against WA Legislature + RCW + PDC together rather than designed in a vacuum. power-map promoted from late-phase to P2 because identity unblocks every downstream MCP capability.

**P0 discovery tracks** run in parallel with the foundation work and feed findings into P1a's model design:

- **Archiver schema/API discovery** — confirm what URLs/payloads it stores, retrieval interface, registration interface (do we POST URLs to it? does it poll? does it accept content?). Output: integration contract.
- **Watcher schema/API discovery** — confirm scheduling capability, push semantics, what data it provides on change. Output: decision on whether usa-wa runs its own scheduler (APScheduler) or delegates to Watcher.
- **Multi-state legislative IA research** — survey existing data models to validate `clearinghouse-domain-legislative` shape against real edge cases. Confirmed reference sources:
  - **OpenStates** — `openstates.org` / `github.com/openstates/openstates-core`. Multi-state legislative data pipeline; canonical for bill/legislator/committee/event modeling across 50 states.
  - **LegiScan** — `legiscan.com/legiscan` / API docs. Commercial multi-state legislative tracking; useful for normalized status and action-type vocabularies.
  - **GovTrack-derived schemas** — `github.com/unitedstates/congress` and friends. Federal-focused but the model maps cleanly onto state legislatures.
  - **NCSL standards** — `ncsl.org` legislative data and process resources. Higher-level taxonomy and committee/process vocabulary.

  Output: a written delta between our P1a draft entities (bills, legislators, actions, sponsorships) and each reference; any entity/field gaps revised into Layer 2 before P1a normalization code lands.

## Architecture

### Workspace shape

Four-layer split. Three shared layers (framework + domain) + per-jurisdiction adapters and API. usa-wa is the WA deployment.

```
usa-wa/                                  # this repo, the WA deployment
├─ pyproject.toml                        # workspace root only; lists members
├─ uv.lock
├─ alembic/                              # single alembic root; env.py imports all members' metadata
├─ packages/
│  ├─ clearinghouse-core/                # Layer 1 — framework primitives, jurisdiction-agnostic
│  │  └─ src/clearinghouse_core/         # BaseAdapter, AdapterRunner, Source, FetchEvent,
│  │                                     # RawPayload, Citation, Confidence, ULID column type,
│  │                                     # Jurisdiction table, SQLAlchemy base, config, logging
│  ├─ clearinghouse-domain-legislative/  # Layer 2 — legislative-government model (state/federal)
│  │  └─ src/clearinghouse_domain_legislative/
│  │                                     # Bill, Legislator, Chamber, Committee, Hearing,
│  │                                     # BillAction, BillSponsorship, StatuteSection,
│  │                                     # BillStatuteChange, LobbyingActivity, LobbyingPosition,
│  │                                     # Contribution, Filer — all carry jurisdiction_id
│  ├─ usa-wa-adapter-legislature/        # Layer 3 — WA Legislature SOAP source mapping
│  ├─ usa-wa-adapter-pdc/                # Layer 3 — WA PDC source mapping
│  ├─ usa-wa-adapter-rcw/                # Layer 3 — WA RCW corpus source mapping
│  └─ usa-wa-api/                        # Layer 4 — WA deployment (FastAPI + MCP + query layer)
└─ tests/
```

Layer responsibilities:

| Layer | Package | Reused by |
|---|---|---|
| 1 · Framework | `clearinghouse-core` | every clearinghouse deployment, every jurisdiction, every domain |
| 2 · Domain (legislative) | `clearinghouse-domain-legislative` | every state legislature + federal legislature; **not** municipal |
| 3 · Per-source adapters | `usa-wa-adapter-legislature`, `usa-wa-adapter-pdc`, `usa-wa-adapter-rcw` | only the WA deployment |
| 4 · Deployment | `usa-wa-api` | only the WA deployment |

A future Oregon deployment would reuse Layers 1+2 and introduce its own Layer 3 adapters (e.g., `usa-or-adapter-legislature`, `usa-or-adapter-orestar`, `usa-or-adapter-ors`) plus a Layer 4 `usa-or-api`. A future municipal deployment (e.g., Olympia city council) reuses Layer 1 but introduces a new Layer 2 (`clearinghouse-domain-municipal`) because city-government concepts diverge from legislative ones.

**MVP extraction posture:** Layers 1 and 2 live as workspace members in this repo for MVP. Extract to their own repos when a second deployment justifies the cost — not before.

**Multi-state IA risk:** designing Layer 2 against only WA is the failure mode. Mitigation — every entity carries a non-null `jurisdiction_id` from day 1 (even with one value); P0 multi-state IA research surfaces edge cases that may force schema revisions before P1a lands.

### Adapter contract

Separation principle: **adapter is a pure transformer; the generic `AdapterRunner` owns orchestration.** Each adapter is one source's source-specific code. The runner is the same code for every adapter.

```python
# clearinghouse_core/adapter.py
class BaseAdapter(ABC):
    source_name: ClassVar[str]              # e.g., "usa_wa_legislature"
    schema_name: ClassVar[str]              # postgres schema; matches source_name
    jurisdiction_id: ClassVar[str]          # e.g., "usa-wa"

    @abstractmethod
    async def fetch_one(self, resource_id: str) -> RawPayload: ...

    @abstractmethod
    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]: ...

    @abstractmethod
    async def normalize(self, raw: RawPayload) -> NormalizedBatch: ...
```

```python
# clearinghouse_core/runner.py
class AdapterRunner:
    """Owns caching, provenance, retries, idempotency, scheduling glue."""

    async def fetch_and_normalize(self, resource_id: str, force: bool = False) -> None: ...
    async def refresh(self, since: datetime | None = None) -> RunSummary: ...
```

Operational modes:

- **Scheduled refresh** — APScheduler job per source calls `runner.refresh()`. Default cadence: daily; per-adapter overrides in config. **Future:** may delegate to Watcher (see P2 evaluation).
- **On-demand** — REST/MCP handlers or backfill jobs call `runner.fetch_and_normalize(id)`. Cache-hit short-circuits; cache-miss/stale refetches. Historical backfill is the same code path over a known set of IDs.

Storage:

- Raw payloads → `<schema>.fetch_events` and `<schema>.raw_payloads` (e.g., `usa_wa_legislature.fetch_events`).
- Parsed-but-not-yet-canonical intermediates (optional per adapter) → `<schema>.parsed_*`.
- Canonical entities → `canonical.*` (from `clearinghouse-domain-legislative`).
- Adapter-extraction story: copy the package + its alembic migrations + its schema namespace → standalone repo. Single alembic root with a filename convention `YYYY_MM_DD_<schema>_<topic>.py` (e.g., `2026_06_01_usa_wa_legislature_init.py`) identifies which migrations belong to which adapter. Per-package alembic configs are deferred unless extraction pressure motivates them.

Idempotency:

- Canonical tables carry a unique constraint on `(jurisdiction_id, source, source_id)` separate from the ULID PK. Re-running an adapter against unchanged source data produces no diff.

### Raw payload retention & Archiver/Watcher integration

usa-wa does **not** keep raw payloads forever. The cache is a forensic-recovery convenience for recent fetches; Archiver is the long-term system of record.

| Concern | usa-wa | Archiver | Watcher |
|---|---|---|---|
| Recent raw bytes (within TTL) | local `raw_payloads` table | (also archived async) | — |
| Older raw bytes | URL ref only; redirect to Archiver | system of record | — |
| Schedule polling | APScheduler (MVP); may delegate | — | future delegate target |
| Change notification | on-demand fetch when stale | — | future push source |

Concrete rules for MVP:

- `Source.cache_ttl_days` configurable per adapter; default 30 days.
- A background sweep GCs `raw_payloads` rows past TTL. Their parent `fetch_events` rows (URL, timestamp, content hash) persist — small metadata, useful forever.
- For older content, the response includes the original URL and (when known) the Archiver URL. Clients/agents can fetch from Archiver directly.
- **P0 discovery output** decides whether usa-wa actively pushes URLs to Archiver after fetch (tight integration) or whether Archiver runs its own polling and we just reference it (loose integration). This decision is deferred to P0 once Archiver's API is documented.
- Scheduled refresh stays in-process via APScheduler for P1. Watcher delegation evaluated in P2.

**Keyword + vector search** is acknowledged as an alternative replication route for extracted text but is out of MVP scope. When it lands, candidate approaches: Postgres FTS for keyword, pgvector for embeddings, both in the same DB — avoids operational complexity of an external search system.

### Canonical data spine

Schemas: `usa_wa_legislature.*`, `usa_wa_pdc.*`, `usa_wa_rcw.*`, `canonical.*`. Every PK and FK is a ULID. Every canonical entity carries a non-null `jurisdiction_id` (text, e.g., `'usa-wa'`).

**Provenance spine** (in `clearinghouse-core`, applies to every canonical entity):

| Table | Purpose |
|---|---|
| `clearinghouse_core.jurisdictions` | One row per deployable jurisdiction. PK is the ULID; natural key is the encoded id (`usa-wa`). Carries name + level (state/federal/municipal). |
| `clearinghouse_core.sources` | One row per configured data source. Holds `kind`, `base_url`, `reliability ∈ [0, 1]`, `cache_ttl_days`, JSON config, jurisdiction_id. |
| `clearinghouse_core.fetch_events` | One row per fetch operation. URL, timestamp, HTTP status, content hash, etag/last-modified, status enum, optional `archiver_url`. |
| `clearinghouse_core.raw_payloads` | Time-bounded cached bytes for a fetch event. Compressed `bytea`. GC'd after `cache_ttl_days`. |
| `clearinghouse_core.citations` | Polymorphic `(entity_type, entity_id) → fetch_event_id` link. Optional `field_path`. Confidence ∈ [0, 1] (single-source for MVP). |

Polymorphic Citation is a deliberate tradeoff — single citation rendering code, slight loss of DB-level FK integrity on `entity_id`. Canonical entities also carry denormalized `primary_source_id`, `last_fetched_at`, `last_fetch_event_id` for cheap MCP responses without joining citations every time.

**Legislative-domain entities** (in `clearinghouse-domain-legislative`, schema `canonical.*`). Each shown with the natural-key UNIQUE constraint that drives upserts; all carry `jurisdiction_id`.

Bill cluster (P1a + P3):

| Table | Notes |
|---|---|
| `canonical.bills` | UNIQUE `(jurisdiction_id, source, source_id)`. Fields: biennium, chamber, number, title, short_description, current_status, current_step, introduced_at, current_text. |
| `canonical.bill_sponsorships` | bill_id ↔ legislator_id ↔ role (`prime`/`co`). UNIQUE `(bill_id, legislator_id, role)`. |
| `canonical.bill_actions` | Append-only lifecycle log. bill_id, action_at, chamber, action_type (vocab), description. UNIQUE `(bill_id, source_action_id)`. |
| `canonical.legislators` | Skeletal in P1a (name, chamber, district, party, biennium, jurisdiction_id). Carries nullable `powermap_person_id` populated in P2. Was "Member" pre-rename; "Legislator" is the domain-layer term (handles state + federal). |
| `canonical.committees`, `canonical.hearings` | Skeletal in P1a, fleshed in P3. |
| `canonical.bill_versions` | Version metadata only in MVP (substitute/engrossed flags, action_at). Full version text deferred to P3. |

Statute corpus cluster (P1b — RCW is the WA instance):

| Table | Notes |
|---|---|
| `canonical.statute_codes` | Top-level identifier of a statutory body (e.g., `(jurisdiction='usa-wa', code='RCW')`). UNIQUE `(jurisdiction_id, code)`. |
| `canonical.statute_titles` | UNIQUE `(statute_code_id, number)`. |
| `canonical.statute_chapters` | UNIQUE `(statute_title_id, number)`. |
| `canonical.statute_sections` | UNIQUE `(statute_chapter_id, number)`. `text` column holds current text. |
| `canonical.bill_statute_changes` | Links bill ↔ statute_section ↔ change_type (`creates`/`amends`/`repeals`/`recodifies`). Text diff is a P3 enrichment. |

PDC-shaped cluster (P1c — generic enough to cover lobbying/finance disclosure in other states with renames):

| Table | Notes |
|---|---|
| `canonical.filers` | UNIQUE `(jurisdiction_id, source, source_id)`. Carries nullable `powermap_org_id`, `powermap_person_id` populated in P2. |
| `canonical.lobbying_activities` | Period start/end, compensation, expenses. UNIQUE `(jurisdiction_id, source, source_id)`. |
| `canonical.lobbying_positions` | Links lobbying_activity ↔ bill ↔ position (`support`/`oppose`/`neutral`). Resolution of bill_id from PDC's chamber+number+biennium reference happens during normalization; failures may set `bill_id` null and land in a `resolution_failures` queue for later backfill. |
| `canonical.contributions` | recipient_filer_id, contributor_filer_id, amount, contributed_at. UNIQUE `(jurisdiction_id, source, source_id)`. |

**History pattern:** current state + event log. Canonical entities are upserted (SCD Type 1). The append-only `canonical.bill_actions` table is the legislative lifecycle event log. Forensic recovery of recent historical field values comes from the local `raw_payloads` cache; older recovery hits Archiver. **No SCD-2 / no `valid_from`/`valid_to`.**

### MCP + REST coexistence

One uvicorn process under the existing systemd unit hosts both surfaces.

```
FastAPI app
├─ GET  /health         · unauth, systemd liveness
├─ GET  /ready          · unauth, systemd readiness
├─ GET  /api/v1/...     · REST, bearer auth
└─ POST /mcp            · MCP Streamable HTTP, bearer auth (fastmcp-mounted router)
```

Both REST handlers and MCP tools are thin presentation layers over `usa_wa_api.query` — a single async Python module exposing functions like `get_bill(chamber, number, biennium) → BillResponse`. Pydantic response models carry citations.

MCP library: `fastmcp` (FastAPI-flavored wrapper around the official `mcp` Python SDK). Tools registered with `@mcp.tool()` decorators.

**MVP tool catalog:**

| Tool | Phase | Returns |
|---|---|---|
| `describe_bill(chamber, number, biennium?)` | P1a | Bill + sponsors[] + actions[] + hearings[] + citations[] |
| `find_bills(query?, sponsor_legislator_id?, biennium?, status?, limit=25)` | P1a | Bills[] + total + citations[] |
| `bill_changes(chamber, number, biennium?)` | P1b | Bill ref + changes[{statute_section, change_type}] + citations[] |
| `describe_statute(code, section, include_proposed_changes=False)` | P1b | StatuteSection + related_bills[]? + citations[] |
| `lobbying_on_bill(chamber, number, biennium?)` | P1c | Bill ref + positions[{filer, employer?, position}] + citations[] |
| `filer_activity(filer_id? \| name_query?, biennium?)` | P1c | Filer + lobbying[] + contributions_in/out[] + citations[] |

All MCP tools implicitly scope to `jurisdiction_id='usa-wa'` in the WA deployment. The argument set stays jurisdiction-agnostic so the same tool definitions work for a future Oregon deployment.

**Auth:** single shared bearer token (`USA_WA_API_TOKEN` in `/etc/usa-wa/.env`) for MVP. `/health` and `/ready` exempted for systemd probes. Middleware lives on the FastAPI app — one place to evolve to per-client tokens in P4.

**Citations:** every REST and MCP response includes a top-level `citations: []` array. Each citation: `{source, url, fetched_at, confidence, fact?, archiver_url?}`. One bibliography per response, not inline per-field — fewer tokens, easier for the agent to surface.

## Deferred (out of MVP)

| Deferred | Phase / reason |
|---|---|
| WAC + WSR (administrative rules) | Out of scope per scope decision; revisit after MVP ships |
| Cross-source identity resolution | P2 (power-map) |
| SCD-2 / point-in-time queries | Not motivated by MCP question shape; add if a use case emerges |
| Full bill version text | P3 |
| Statute text-diff display in `bill_changes` | P3 enrichment |
| Watcher delegation of scheduled refresh | P2 evaluation, P3+ implementation |
| Tight Archiver integration (active push) | P0 discovery decides; possibly P3+ |
| Keyword + vector search | Not in MVP; pgvector + Postgres FTS likely path |
| Rate limiting, per-client quotas, OAuth | P4 (Operate) |
| MCP resources (vs. tools-only) | P4 or later if useful |
| Webhooks / change notifications | Not in MVP question shape |
| Python SDK | P5+, codegen from stable OpenAPI |
| Admin UI | P4 |
| GIS boundaries | P5+ |
| archiver, observo, watcher *deeper* integrations | P5+, gated on sibling readiness |
| Layer 1 + Layer 2 package extraction to own repos | When a second jurisdiction starts |
| Municipal-government domain (`clearinghouse-domain-municipal`) | When a city deployment starts |
| Federal-government deployment (`usa-fed-api`, etc.) | When motivated |

**Future research direction (not committed):** RCW (and statute corpora generally) as machine-readable, git-versioned text — amendments as branches, ratifications as merges. Would change the storage shape of `canonical.statute_sections` and `canonical.bill_statute_changes` significantly. Worth its own design exploration before commitment.

## Open questions

Carried into the implementation-planning phase. Resolution gates are noted.

1. **Archiver integration model** — tight (usa-wa pushes URLs / receives bytes) vs loose (usa-wa references Archiver passively). **Gate:** P0 Archiver schema/API discovery.
2. **Watcher delegation** — does usa-wa run APScheduler or push scheduling to Watcher? **Gate:** P0 Watcher schema/API discovery; defer decision to P2.
3. **Multi-state legislative IA edge cases** — does `clearinghouse-domain-legislative` shape survive contact with other states' data models? **Gate:** P0 research against the four reference sources listed in Phasing > P0; revise Layer 2 schema before P1a if needed.
4. **WA Legislature SOAP transport library** — `zeep` is conventional; worth confirming WSDL doesn't have quirks that argue for raw `httpx` + manual XML.
5. **RCW source** — ingestible via the WA Legislature SOAP service, or scraped from `app.leg.wa.gov/RCW/`? Affects whether the RCW adapter shares transport with the Legislature adapter.
6. **ULID storage representation** — `BINARY(16)` (storage-efficient, harder to read in psql) vs `text(26)` (debug-friendly, larger index). Decide in P0 and apply project-wide.
7. **`fastmcp` MVP fit** — confirm `fastmcp` supports the Streamable HTTP transport shape we want, including bearer-auth middleware sharing with REST. Fallback: official `mcp` SDK directly.
8. **APScheduler persistence** — SQLAlchemy job store in the same Postgres, or run jobs without persistence in MVP. Affects whether `alembic` migrations include scheduler tables.
9. **PDC bill-reference resolution heuristics** — documenting the exact match algorithm (chamber + number + biennium, including how to handle bill-number-substitution events). Sub-section in the P1c plan.
10. **Confidence model details** — MVP is "source's intrinsic `reliability` × freshness factor." The freshness factor's exact shape (linear decay? step function on age buckets?) is left to the P1a implementation plan.
11. **Alembic strategy** — Single alembic root with `YYYY_MM_DD_<schema>_<topic>.py` naming convention chosen for MVP. Revisit if migration cross-package coordination becomes painful.
12. **Per-package alembic ownership when Layer 1+2 extract** — when `clearinghouse-core` and `clearinghouse-domain-legislative` move to their own repos, do they own their migrations there? Likely yes; consuming deployments rebase/apply them. Detailed in extraction time.

## Next step

Implementation plan via the writing-plans skill, starting with **P0 (workspace + Postgres + adapter contract + discovery tracks)**. Subsequent plans land in `docs/plans/` parallel to this spec, one per phase.
