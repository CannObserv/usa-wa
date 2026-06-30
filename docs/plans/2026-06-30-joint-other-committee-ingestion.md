---
title: Joint/Other committee ingestion — meeting-derived, harvest-then-freeze (#39)
date: 2026-06-30
status: draft
---

# Joint/Other committee ingestion (#39)

## Problem

WA's joint and `Other` legislative committees (JTC, JLARC, SCPP, PFC, VMA, ESEC,
LEAP, Statute Law Committee, …) are **absent** from `CommitteeService` in every
biennium, so they never enter the org graph. They are real, persistent
organizational structure the clearinghouse is missing. Their **only** programmatic
source is `CommitteeMeetingService.GetCommitteeMeetings(beginDate, endDate)`, which
exposes each committee as a ref on every meeting it held — a body appears only if
it *met* in the queried window, and its `LongName` is agency-double-prefixed.

## Approach

**Harvest-then-freeze.** A one-shot backfill CLI sweeps `GetCommitteeMeetings` over
configurable biennium windows, archiving the **pristine SOAP wire** (reusing #54's
`WireFetch` + `_CapturingTransport`) under the existing `usa_wa_legislature` source
(already `retention_policy=archival`), keyed `resource_id="committee-meetings:<begin>:<end>"`.
It dedups committee refs by the stable WSL `Id` and emits `Organization` rows, then
**freezes a checked-in seed** (`seed_manifest` sidecars) so the durable set needs
zero further WSL traffic. The daily refresh pulls **only the current window** for
additive discovery. Closed windows are immutable → fetched once, never re-pulled
(WSL is vital; stay a good Internet friend).

Locked mappings (validated against live 2023-24 & 2025-26 data):
- `source_id = str(Id)` — negative sentinels (JTC -140, JLARC -5, …) verified stable across bienniums.
- `name = LongName` **verbatim** (matches existing committee normalizer; PM curates display).
- `short_name = Name`, `org_type = "other"` for the **entire** meeting-derived class.
- parent = legislature anchor (`Agency` ∈ {Joint, Other}).
- **Window-absence ≠ retirement** for this class (dormancy is normal).

## Tradeoffs / alternatives

- **RCW statutory-core seed** — rejected: the stable join key (`Id`) lives only in WSL's DB, not RCW, so RCW can't be the identity spine. (Viable later as a name/citation *enrichment overlay* keyed on `Id`.)
- **Live meeting-discovery only, no frozen seed** — rejected: dormancy makes durable bodies vanish from any single window; the frozen seed gives stable identity that survives it.
- **`org_type` taxonomy (joint_committee / legislative_agency / subcommittee)** — rejected as make-work: a single `"other"` value fences the rows out of the #44/#46 reconcilers (which gate on `org_type=="committee"`) and routes to the same PM `org_wa_legislature_committee_id` slug regardless.
- **Proactively clean the double-prefixed `LongName`** — rejected: verbatim-raw is the stance; the dirtiness is a deterministic `f"{Agency} {Name}"` PM curation can resolve downstream.

## Steps

1. `WSLClient.fetch_committee_meetings(begin, end) -> WireFetch` against `CommitteeMeetingService` — clone the `fetch_active_committees` wire-capture pattern. TDD with a recorded pristine-wire cassette (seed from the spike's 2023-24/2025-26 XML).
2. Window helpers: biennium → `(begin, end)` datetimes + `resource_id` builder; floor-probe + log the earliest biennium `GetCommitteeMeetings` answers.
3. Normalizer: dedup refs by `Id` across meetings → `Organization` rows (mappings above); extend `_parent_for_agency` for `"Other"`. Unit-tested on the cassette fixtures.
4. Harvest/freeze backfill CLI: `--from-biennium`/`--to-biennium` (default full supported span), archives wire per window, writes the seed JSON + `seed_manifest.write_sidecars`. Idempotent.
5. Seed-ingest path: `verified_digest` → `FetchEvent.content_hash`; upsert orgs by `(source, source_id)`. Re-runnable.
6. Daily refresh: add a current-window docket pull (additive discovery only). Test asserting **window-absence does not retire**.
7. Regression test: `org_type="other"` rows are excluded from the #44 active-reconcile and #46 rename-detect cohorts.

## Open questions / risks (resolved)

- **Backfill depth** — *Resolved:* validate against recent biennia (2023-24, 2025-26) only for now; full-depth backfill + floor-probe deferred to a later pass.
- **`Acronym` not universal** (e.g. Civic Health blank) — label only, never identity; keying on `Id` already covers it.
- **`Other` includes a JLARC subcommittee (I900)** — *Resolved:* the meeting payload carries **no parent field** (`Agency` is the only structural signal), so parent the whole `other` class to the legislature anchor and **defer subcommittee nesting (I900→JLARC) to PM curation** — PM is system-of-record for the org tree and `parent` is excluded from the org descriptor's enrich carry-fields. No name/acronym string-matching (not robust).
- **Seed home & loader unit** — *Resolved:* dedicated one-shot backfill CLI owns the frozen seed; daily refresh stays current-window-only (the source's `cache_ttl_days=1` would otherwise re-pull closed windows).
- **Joint-committee renames** — *Resolved:* filed as #56 (meeting-derived sibling of #46); reuse `reconcile_committee_names` machinery, only the source cohort differs. Out of scope here.
