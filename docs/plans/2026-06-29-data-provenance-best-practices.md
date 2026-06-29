---
title: Data-provenance best practices (content_hash, wire archival, integrity sweep)
date: 2026-06-29
status: in-progress
---

# Data-provenance best practices

Issue: [usa-wa#54](https://github.com/CannObserv/usa-wa/issues/54).
First consumer: [usa-wa#39](https://github.com/CannObserv/usa-wa/issues/39) (pristine WSL committee-meeting docket archive).
Reuses: [usa-wa#49](https://github.com/CannObserv/usa-wa/issues/49) failure-alert path; [usa-wa#22](https://github.com/CannObserv/usa-wa/issues/22) DB role topology.

## Problem

Fetched source data has no integrity baseline and no deliberate retention contract:

1. **`content_hash` is plumbed but never set *or derived*.** The column exists
   (`FetchEvent.content_hash`, `LargeBinary(32)`), the runner copies
   `payload.content_hash`, and `FetchedPayload.content_hash` defaults to `None`
   — but no adapter sets it and the runner has no fallback. Every `FetchEvent`
   carries a NULL hash. No tamper baseline for any archived payload.
2. **We archive the *parsed* shape, not the wire bytes.** The WSL adapter stores
   `json.dumps(serialize_object(zeep_result))` as the `RawPayload` body — one
   lossy transform removed from what WSL sent. A hash of that attests to our
   re-serialization, not the source.
3. **`RawPayload` GC is documented but does not exist.** `cache_ttl_days` is
   consumed only by the re-fetch freshness gate (`runner._find_fresh_fetch_event`);
   nothing deletes payloads. Today every payload is retained forever *by omission*
   — so the retention contract must be decided *before* a GC is ever written, not
   bolted on as an opt-out afterward.

## Approach

Five independently-shippable items, ordered smallest/highest-value first. Decided
storage placement (the dimension #54 left unstated):

| Artifact | Lives in | Rationale |
|---|---|---|
| `content_hash` baseline | **DB** `FetchEvent` (exists) | transactional with the fetch row |
| Wire bytes (archival sources) | **DB** `RawPayload.body` | single-VM, KB–low-MB scale; Postgres TOAST compresses `bytea` transparently; keeps body+hash+citation in one txn. Filesystem/Archiver offload is the documented escape hatch — defer until volume warrants |
| Parsed dict | **DB** canonical tables only | re-derivable via `normalize`; stop archiving as "raw" |
| Frozen seeds (#39) | **repo / filesystem** | the only non-DB piece; git is the tamper evidence |

### Item 1 — always have a hash + make provenance write-once

- **Derive in the runner, not adapters.** `_record_fetch_event` computes
  `sha256(payload.body)` when the adapter leaves `content_hash=None`. Single
  chokepoint → no adapter can forget. Adapter-supplied hash still wins (e.g. a
  streamed digest).
- **Canonical-form invariant (documented in `provenance.py`):** `content_hash`
  is sha256 over *exactly* the bytes in `RawPayload.body`, pre-any-app-compression,
  no normalization. The integrity sweep and the writer must agree on this form or
  they manufacture phantom mismatches.
- **Append-only enforcement (the cheap real tamper-resistance).** Not in #54's
  scope list, folded in here: `REVOKE UPDATE, DELETE` on `fetch_events`,
  `raw_payloads`, `citations` from `usa_wa_app` in `scripts/grants.sql`. The live
  API/sidecar role then *cannot* rewrite provenance history — only the owner role
  (migrations) can. Write-once provenance for near-zero cost, reusing the #22 role
  split. This is the high-value substitute for the out-of-scope hash-chain/ledger.

### Item 2 — wire-vs-parsed archival

- Capture the SOAP XML envelope at transport via a `requests` response hook
  (proven in `scripts/spike_committee_meetings.py`), promote into `WSLClient`.
- `FetchedPayload.body` becomes the wire envelope; `content_type="text/xml"`. The
  parsed dict stays in-memory for `normalize`, no longer archived as raw.

### Item 3 — per-source retention policy

- Add `Source.retention_policy` enum: `operational_cache` (default) | `archival`.
  Inert until a GC exists — the forward contract so an eventual GC deletes only
  `operational_cache` payloads past TTL and never touches `archival`. WSL
  committee/meeting sources = `archival`.

### Item 4 — integrity sweep

- Oneshot + weekly timer (clone the `reconcile-committee-names` unit shape),
  `OnFailure=usa-wa-notify-failure@%n.service` (#49). Re-hash `RawPayload.body`
  (decode per `content_encoding` if added) vs `FetchEvent.content_hash`. Reconcile
  exit-code contract (0 clean / nonzero → email).

### Item 5 — frozen-seed tamper evidence

- Git history + commit SHA is the primary tamper evidence (git blobs are
  content-addressed). A `.sha256` sidecar is redundant *inside* the repo; emit it
  only when the seed is consumed *outside* git (loaded to DB / shipped to
  Archiver) — the loader verifies it, then writes the same hash into
  `FetchEvent.content_hash`, unifying repo-seed and fetched-source under one
  baseline. Rides #39.

## Tradeoffs / decisions

- **DB-first for wire bytes, not filesystem.** Simpler (one backup domain, no
  orphan risk, transactional). Escape hatch documented for when docket volume
  grows.
- **Compression: rely on TOAST, hash the raw wire.** Postgres pglz/lz4-compresses
  `bytea` >2KB transparently. *Do not* app-gzip then store in `bytea` — TOAST
  can't recompress already-compressed bytes and the sweep would have to decompress
  to hash. If app-level zstd is later justified by #39 docket size, add a
  `content_encoding` column and keep `content_hash` defined over the *uncompressed*
  wire. Decision recorded; not built now.
- **NULL hash = "unbaselined / legacy," never a zero-hash sentinel.** An
  all-zeros digest is a valid-looking value and a collision target; `sha256(b"")`
  is also a real value, so it can't double as "missing." The sweep treats NULL as
  skip-and-count-separately, never as a mismatch. New rows are NOT NULL in
  practice (runner always derives); the column stays nullable for the legacy tail.
- **Hash-chaining / append-only ledgers / external timestamping: out of scope.**
  Overkill for a single-VM Postgres deployment. The grants REVOKE (Item 1) buys
  most of the tamper-resistance benefit at a fraction of the cost.

## Steps

1. **Item 1** — runner derives `sha256(body)`; document canonical-form invariant;
   `REVOKE UPDATE, DELETE` on provenance tables from app role in `grants.sql`.
   (TDD: runner test asserts derived hash + adapter-supplied override.)
2. **Item 2** — `WSLClient` wire capture; `body`=envelope; retire parsed-as-raw.
3. **Item 3** — `Source.retention_policy` column + migration.
4. **Item 4** — integrity-sweep CLI + oneshot/timer + alert wiring.
5. **Item 5** — seed `.sha256`/`.meta.json` manifest convention (with #39).

## Open questions

1. Does #39's meeting-docket XML get large enough across bienniums to justify
   app-level zstd over TOAST? Measure during #39 before deciding `content_encoding`.
2. Integrity sweep full-rehash is O(all payloads) — fine now; add a since-cursor
   before it's slow. Sweep cadence vs. data volume to be tuned when #39 lands real
   archival payloads.
