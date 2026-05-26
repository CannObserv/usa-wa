---
title: Archiver integration contract — research note
date: 2026-05-26
status: research (informs P2+ decision)
sources:
  - https://github.com/CannObserv/archiver (default branch `main`, inspected 2026-05-26)
  - github.com/CannObserv/archiver/blob/main/README.md
  - github.com/CannObserv/archiver/blob/main/docs/plans/2026-05-08-archiver-v2-architecture-design.md
  - github.com/CannObserv/archiver/blob/main/CHANGELOG.md (current `v3.5.4`, 2026-05-22)
  - github.com/CannObserv/archiver/blob/main/src/api/routes/*.py
  - github.com/CannObserv/archiver/blob/main/clients/python/README.md
related: docs/specs/2026-05-25-usa-wa-mvp-design.md §"Raw payload retention & Archiver/Watcher integration"
---

# Archiver integration contract — research note

## TL;DR

Archiver is **not** a raw-bytes blob store. It is a **central registry + authoring service** for an information model whose unit of identity is the `(InfoSource, content_fingerprint)` pair, with `SourceRevision` rows carrying *metadata* about content captures — never the bytes themselves. The bytes are produced by Watcher and consumed (within a short TTL) by Replicator; long-term bytes live in **provider buckets** (`gcs`, `gdrive`, `ia`) under URLs that Replicator writes back to Archiver as `public_url` on `info_item_rep_specs` assignments.

usa-wa's current spec language ("Archiver is the long-term system of record for raw web-sourced content") is **directionally correct but mechanically imprecise**: Archiver does not store our bytes; it stores *registry metadata that points at where the bytes ended up* (a provider-uploaded artifact whose URL Archiver curates). The integration shape we adopt has to respect that.

The repo is **not empty**. It is at `v3.5.4`, has 8 production releases in May 2026, a generated Python SDK (`archiver-client` v3.2.x), an HTMX admin dashboard, an outbox-driven Redis change-bus, and active sibling integrations with `watcher` and (designed) `replicator`. We are a late entrant to a working ecosystem.

---

## (a) What kinds of URLs/payloads does Archiver store?

**Stores (rows in Postgres `information.*` schema):**

| Table | What it carries |
|---|---|
| `info_items` | Semantic entity (e.g. "WSLCB board meeting agenda 2026-04-15") + a `rep_fields` JSONB bag for replication-path templating |
| `info_sources` | Either a **root** (URL-bearing, `source_spec.target.url`) or a **fragment** (parent-bearing, URL-less, extracted from the parent's bytes). XOR enforced by CHECK constraint. |
| `source_revisions` | Content-addressed *metadata* row: `(info_source_id, content_fingerprint)` unique, where fingerprint is `sha256:<hex>` over post-extraction content. Also: `captured_at`, `content_size_bytes`, `content_media_type`, an optional **`content_cache_uri`** (`file:///...` scratch path) with **`content_cache_expires_at`**. |
| `info_item_sources` | Operator-declared "this item tracks these URLs" binding, with effective-dated `role` (`primary` \| `cross_check` \| `sub_aspect`). |
| `info_item_source_revisions` | Append-only history of which revisions an item has been pinned to. |
| `rep_specs` | Replication target spec (provider config + path template + credentials alias). |
| `info_item_rep_specs` | Effective-dated assignment; carries the eventual `public_url` (provider-uploaded artifact). |

**Does NOT store:** the actual bytes. There is **no `body` / `payload` / `blob` column**. The closest thing is `content_cache_uri`, which is documented as *"authoritative-when-set" but ephemeral* — a `file://` path that Watcher writes for Replicator to pick up within a default 600s TTL, after which Watcher's sweeper deletes the file and PATCHes the field to `NULL`.

**Long-term bytes** live in the **Replicator-uploaded provider artifact** (a GCS object, a Google Drive file, an Internet Archive item). The `public_url` written back to `info_item_rep_specs.public_url` is the canonical pointer.

**Implication for usa-wa.** "Push our cached bytes to Archiver for long-term storage" is **not a supported operation**. The supported analog is: declare a `RepSpec` (e.g. `gcs` profile `co_usa_wa`), assign it to the relevant InfoItem, and let Replicator (or its eventual equivalent) upload from Watcher's cache. usa-wa is not a Watcher today and does not produce content the way Watcher does.

---

## (b) Retrieval interface

**REST API** at `https://archiver.<host>/api/v1/...`, port 8020 prod / 8021 dev. Auth: `X-API-Key` validated against SHA-256 hashes in `information.api_keys` (env-var auth was retired in v3.4.0). All non-2xx responses use a unified error envelope (`kind`, `message`, `errors[]`, `data`).

**Endpoint surface** (from `src/api/routes/`):

| Verb + path | Purpose |
|---|---|
| `POST /info-items` | Create item (atomically with `initial_source_spec`) |
| `GET /info-items`, `GET /info-items/{id}` | List + detail; populates `info_item_sources` and `info_item_rep_specs` |
| `POST /info-items/{id}/info-sources` | Bind existing InfoSource to item |
| `POST /info-items/{id}/rep-specs` | Assign RepSpec |
| `POST /info-items/{id}/source-revisions` | Manual revision pin (backfill) |
| `PATCH /info-item-rep-specs/{id}` | Writeback `public_url` (Replicator path) |
| `POST /info-sources` | Create root or fragment InfoSource |
| `GET /info-sources` | List, filter by `parent_info_source_id` |
| `POST /source-revisions` | **Idempotent** on `(info_source_id, content_fingerprint)` — returns 200 if pair exists, 201 if inserted |
| `PATCH /source-revisions/{id}` | Cache-field lifecycle (`content_cache_uri`, `content_cache_expires_at`) |
| `GET /rep-specs`, `POST /rep-specs` | RepSpec catalog |
| `POST /tools/*` | Authoring helpers (`validate_source_spec`, `validate_rep_spec`, `fetch_and_render`, `preview_extraction`, `propose_selectors`, `find_info_item`, `resolve_rep_fields`) |

**SDK:** generated Python client `archiver-client` (currently v3.2.x) at `/home/exedev/archiver/clients/python` on this VM, designed for path-installation via `[tool.uv.sources]`. Fully typed, fully async. The README example shows the exact ergonomics for `create_info_item` + `post_source_revision` + `validate_source_spec`.

**Lookup-by-URL:** no `GET /by-url` route exists today. Closest paths:
1. `GET /info-sources?...` (no URL filter yet — see open archiver#20, *"add URL-substring and search filters for typeahead consumers"*).
2. `POST /info-sources` with the same canonicalized URL returns **409** with `data.existing_info_source_id` — usable as a "find-or-create" probe but inelegant for read-only lookup.
3. `POST /tools/find-info-items` — name/text-based, not URL-keyed.

**Change-bus:** Archiver is the **producer** of the `info.changes` Redis Stream. Payload: `{event_type: "source_revision_captured", info_source_id, source_revision_id, content_fingerprint, bindings: [{info_item_id, role}, ...]}`. Subscribers can route without callbacks. Outbox-driven (transactional).

**Dashboard:** HTML/HTMX admin UI at `/dashboard/` (auth via exe.dev proxy headers). Useful for humans, not the API contract.

---

## (c) Registration model

Archiver is **passive on the network**. It does not poll URLs and does not fetch on its own initiative (its `/tools/fetch_and_render` exists for *authoring preview*, not for ongoing replication). Population is producer-pushed:

- **Operator authoring** — humans (via dashboard or `archiver-client`) create InfoItems + InfoSources, declare RepSpecs, and assign rep_specs to items.
- **Watcher (sibling service)** — owns the schedule + fetch loop. On a content shift, POSTs a `source_revision` (with cache URI) and Archiver emits the change-bus event.
- **Replicator (designed, not yet built)** — subscribes to `info.changes`, uploads the bytes (read from Watcher's cache or fallback-refetched and hash-verified), PATCHes `public_url` back.

There is no "give Archiver a URL and it will start polling" surface. **Scheduling is Watcher's domain.** If usa-wa wants periodic re-fetch of a primary source, the cohort's intended answer is to register that URL in Archiver as an InfoSource and let Watcher schedule it. usa-wa stays read-only with respect to bytes.

Currently `usa-wa` is doing its own fetches against WA primary sources (SOAP, RCW scraping, PDC). That is **not coordinated with Watcher** today and there is no shared schedule. This is an unresolved cohort question, not strictly an Archiver-integration question.

---

## (d) Recommended MVP integration posture

**Recommendation: loose, URL-ref-only at MVP. Defer all push/sync to P3+.**

Concretely for the P0–P2 horizon:

1. usa-wa keeps its own `clearinghouse_core.fetch_events` + `raw_payloads` exactly as currently specced. No Archiver dependency on the hot path.
2. `clearinghouse_core.fetch_events.archiver_url` (already in the spec at line 189) holds an **optional** pointer that, when present, is the `public_url` of the relevant `info_item_rep_specs` row (i.e. the GCS / IA artifact URL — not an Archiver API URL).
3. **MCP/REST responses** include `archiver_url` in the citation block when known. Clients dereference directly to the provider artifact. We do not proxy through Archiver.
4. usa-wa makes **zero writes to Archiver in P0–P2**. No `POST /source-revisions`, no InfoItem creation, no RepSpec assignment.
5. **Discovery is the only Archiver touchpoint in early phases**: a lookup helper (CLI or one-shot script) that, given a known WA URL (e.g. an RCW chapter URL), queries `GET /info-sources` (once URL filter lands per archiver#20) or the existing dashboard to find a matching `info_source_id` → active `info_item_rep_specs` → `public_url`. Cache that mapping in `sources.config` or a side table.

**Why loose, not tight.**

- Archiver's data model is built around a **schedule-driven Watcher** producing fingerprinted SourceRevisions on content shift. usa-wa's fetches are MCP/REST-query-driven and quite likely *redundant* with Watcher's eventual coverage of the same URLs. POSTing a SourceRevision on every usa-wa fetch would either duplicate Watcher's writes (idempotent, but noisy) or compete with them (different fingerprints if extraction normalization differs).
- **There is no extraction contract on the usa-wa side yet.** Archiver fingerprints are over *post-extraction* content per the registered `source_spec.extraction`. usa-wa would have to either (a) register canonical extractions per WA source URL in Archiver, in which case we are pretending to be Watcher, or (b) ship raw-byte fingerprints, which mismatches Archiver's semantics and won't dedupe against Watcher.
- **`content_cache_uri` is single-VM and short-TTL.** It cannot be a long-term back-channel for usa-wa to surface bytes. There is no "long-term blob upload" endpoint.
- The **decision cost of being wrong about loose** is low (we are not writing anything; we can switch to tight later). The decision cost of being wrong about tight is high (we have to undo writes, deduplicate IDs, and re-architect against Watcher's schedule).

**Hybrid path for P3+, if Watcher coverage of WA sources lags:**

- Treat usa-wa as a *secondary producer* for any WA URL not yet on a Watcher schedule.
- After fetching, usa-wa would `POST /source-revisions` with the same `info_source_id` Watcher will eventually own (looked up or created via `POST /info-sources`).
- Requires usa-wa to register `source_spec` documents (canonical extraction) per WA URL — a real authoring task. Doable, but Archiver-shaped work, not usa-wa-shaped work.
- Probably better implemented as a *standalone sidecar* (`usa-wa-archiver-pump`) that watches our `fetch_events` table and translates to Archiver writes, rather than coupling it to the request hot path.

---

## (e) `Source.cache_ttl_days` vs Archiver retention

These two TTLs are **orthogonal and should remain so**.

| Concern | usa-wa `Source.cache_ttl_days` | Archiver `content_cache_expires_at` | Archiver provider `public_url` |
|---|---|---|---|
| Default | 30 days | 600 seconds | Effectively permanent (provider-managed) |
| Storage | Local Postgres `bytea` | Local VM `file://` scratch | GCS / GDrive / IA bucket |
| Owner | usa-wa | Watcher (writer), Watcher sweeper (GC), Replicator (reader) | Replicator (writes `public_url` back) |
| Purpose | Forensic recovery + small-window cache for citation-aware MCP responses | Hand-off buffer between Watcher fetch and Replicator upload | Long-term system of record |
| Lifecycle | Background sweep on `cache_ttl_days` | Watcher's per-sweep job (default 60s) | None (deletion is an operator action) |

**Practical guidance:**

- Do **not** try to align `Source.cache_ttl_days` to anything in Archiver. They serve different masters.
- After local TTL expiry, usa-wa's parent `fetch_events` row remains (URL, timestamp, content_hash). If `archiver_url` was populated, citations still dereference; if not, the citation becomes URL-only (still useful: the original primary source URL).
- **The interesting field on `fetch_events` is `archiver_url`, not anything time-based.** Populating it is the entire P3+ integration story.

---

## Blocking unknowns

These must be resolved before promoting integration past "loose URL-ref-only":

1. **Watcher's WA coverage roadmap.** Does Watcher already schedule, or intend to schedule, any of the WA primary sources usa-wa cares about (`wa.gov/leg/wsladm`, RCW, WAC, PDC)? If yes → we wait and consume. If no → we either become a producer or accept that WA URLs are usa-wa-only in Archiver's view. **Action:** survey `watcher` repo for WA-domain entries in its `Watch` / `info_source_id` registrations.
2. **Replicator status.** Archiver's design doc says "Replicator status: design-only, no code yet" (as of 2026-05-08). Without Replicator, `info_item_rep_specs.public_url` never gets populated — so even the "loose" posture has nothing to dereference to. **Action:** check `CannObserv/replicator` (likely empty/early) and confirm the timeline. Until Replicator ships, `archiver_url` in usa-wa citations is aspirational.
3. **RepSpec for "the cannabis observatory's WA archive."** Even in tight mode, we need at least one assigned `rep_spec` (provider profile, path template, credentials alias) per WA InfoItem we expect to be archived. Who authors these? Probably an org-wide operator, not us. **Action:** confirm operator ownership; do not attempt to author RepSpecs from inside usa-wa.
4. **URL-substring lookup in Archiver.** Open issue `CannObserv/archiver#20` ("`list_info_sources`: add URL-substring and search filters for typeahead consumers"). Without it, the only programmatic "find by URL" path is the 409-on-conflict probe. **Action:** track #20; it is the unblocker for our discovery helper.
5. **Extraction-canonicalization mismatch.** If usa-wa ever does POST a SourceRevision (P3+), the fingerprint must match Watcher's. That requires agreement on `source_spec.extraction` per URL. **Action:** if/when we go tight, this becomes a real schema-coordination task with Watcher's owners.
6. **Cohort-level deduplication policy.** If both Watcher and a hypothetical `usa-wa-archiver-pump` POST revisions for the same URL on the same day, Archiver's idempotency dedupes by fingerprint — but if extractions differ, they will not dedupe and we will see "duplicate" revisions with different content hashes for the same wall-clock fetch. This is an architectural smell. **Action:** if this ever becomes a real workload, Watcher should be the only writer per URL.

---

## Recommendation block (decision-ready)

**Posture:** *Loose, URL-ref-only.* usa-wa writes nothing to Archiver in P0–P2. `fetch_events.archiver_url` is populated opportunistically when known (via a discovery script that queries Archiver for matching InfoSources). MCP/REST responses include `archiver_url` in the citation block; clients dereference directly to the provider artifact (not to Archiver itself). `Source.cache_ttl_days` is governed by local-storage cost, not by anything Archiver-related.

**Promotion gate (loose → hybrid):** All of (i) Replicator is live and writing `public_url` for at least one WA InfoItem; (ii) Watcher's WA coverage is known and gaps are identified; (iii) `archiver#20` (URL filter on `GET /info-sources`) is merged; (iv) a cohort-level owner has authored at least one `rep_spec` we can attach to. Until all four hold, "tight push" is premature optimization.

**Estimated effort, loose path:** ~1–2 days of work in P2 to add the `archiver_url` column wiring + a one-shot discovery script. No SDK dependency required at MVP (the SDK becomes load-bearing only if we go to hybrid). Add `archiver-client` to `pyproject.toml` as a path dep at that point, mirroring Watcher's install pattern.

**Estimated effort, hybrid path (if we ever do it):** ~1–2 weeks. Requires `source_spec` authoring per WA URL, a sidecar pump from `fetch_events` to `POST /source-revisions`, and coordination with Watcher's owners to avoid double-writing. Defer until P3+ with explicit justification.
