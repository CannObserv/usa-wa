---
title: Mirror PM Org `active` axis locally (read-mirror MVP)
date: 2026-06-26
status: planned
---

# Mirror PM Org `active` axis locally

Issue: [usa-wa#43](https://github.com/CannObserv/usa-wa/issues/43)
Upstream (shipped): [power-map#240](https://github.com/CannObserv/power-map/issues/240) / PR #242
Sibling: [usa-wa#42](https://github.com/CannObserv/usa-wa/issues/42) — the archived/deleted axis split.

## Problem

PM tracks org lifecycle on **three orthogonal axes**: `archived_at` (reversible
gate, all entity types), `deleted_at` (terminal tombstone), and **`active`**
(`organizations.active`, orgs-only — the operationally-live-vs-dissolved domain
flag). #42 mirrored the first two. `active` is the third, distinct signal: a
legislative committee can be **dissolved/defunct** (inactive) without being
**archived** (admin soft-delete) — e.g. WSL's "active-in-biennium" concept (a
committee present in one biennium, absent the next is *inactive*, not *archived*).

power-map#240 has now surfaced `active` on the public API, so there is finally a
source to mirror:
- **Read** — `active` (required `bool`) on `GET /api/v1/orgs/{id}`. Detail-only;
  **not** in search results.
- **Write** — folded into `POST /api/v1/orgs/observations` (`active` field).
  Omitted/`null` ⇒ unchanged; archived org ⇒ 422 `active_on_archived_org`;
  atomic + no-op-suppressed.
- **Feed** — a real toggle emits generic `change_kind='updated'`; no-op emits nothing.

## Approach

**Read-mirror MVP only.** Add the column and mirror PM's value in. Producer-side
(USA-WA *setting* `active` on biennium-absence retirement) is deferred to a
follow-up issue — see Open questions.

`active` is a **domain flag, not a live-read gate**. Unlike `archived_at` /
`deleted_at` it does **not** hide a row from reads, so it lives as a plain column
on `Organization` — **not** in `LifecycleMixin`, `is_live()`, or `live_only()`.
An inactive org stays in the read fan-out.

### Steps

1. **Column + migration.** `Organization.active BOOLEAN NOT NULL DEFAULT TRUE`
   (orgs only — not person/role/assignment). Migration adds the column with a
   `server_default` of `true` so existing rows backfill live; revision chains off
   `d552d384b788` (current head).
2. **Read-mirror.** In `OrganizationDescriptor.upsert_from_pm`, mirror
   `record.get("active")` onto `row.active` (PM-authoritative, alongside
   `mirror_archival`). Guard `is not None` — the detail payload always carries it,
   but search-sourced dicts (which omit it) must not clobber the local value.
3. **Keep `active` OUT of `to_observation`.** Routinely echoing a PM-owned field
   risks (a) the `active_on_archived_org` 422 on archived-org CREATEs and (b) an
   LWW write-back fight (`authority = "pm"`). A guard test pins this.
4. **Regenerate the PM client** so `active` is a typed field on `OrgDetail` +
   `OrganizationObservationRequest` (it round-trips via `additional_properties`
   today, but AGENTS.md says regenerate on PM API changes).

### Tests (TDD)

- `upsert_from_pm` mirrors `active=false` → `row.active is False`; `true` → `True`.
- A search-shaped record (no `active` key) leaves `row.active` untouched.
- An inactive org **remains visible** through `live_only` (anti-regression on the
  read-semantics decision).
- `to_observation` does **not** emit `active` (guards the 422 / write-back loop).

## Tradeoffs

- **Plain column vs mixin.** Reusing `LifecycleMixin` would wrongly couple `active`
  to the hide-from-reads axes. A standalone column keeps the read semantics
  explicit at the cost of one un-mixed-in field.
- **`additional_properties` passthrough vs regen.** Mirror works without regen;
  regen is the deliberate, typed choice the repo convention prefers.

## Open questions

- **Producer retirement (deferred).** Should USA-WA *set* `active=false` when a WSL
  committee is absent from the current biennium? That needs transition detection in
  the adapter/refresh + a deliberate observation that skips archived orgs (the 422
  guard). Tracked as a follow-up issue linked from #43; out of scope here.
