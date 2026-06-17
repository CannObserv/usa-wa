---
title: Wire entity-event sync as a person/org read sub-resource (usa-wa#19)
date: 2026-06-17
status: draft
---

# Wire entity-event sync as a person/org read sub-resource (usa-wa#19)

## Problem

#9 refined `canonical.entity_events` to PM's `ObservationEventItem` shape and added the
`pm_entity_event_id` anchor + the `fetch_record` seam, then deferred the actual sync wiring
(#19). PM #178 has since shipped the per-parent read routes (`GET /api/v1/people/{id}/events`,
`GET /api/v1/orgs/{id}/events`) — present in the generated client as `list_person_events` /
`list_org_events`. But nothing in usa-wa pulls them: the `PowerMapClient` protocol has no events
method, `GeneratedPowerMapClient` has no dispatch, the person/org descriptors don't override
`fetch_record`, and the `entity_events` mirror stays permanently empty. The schema is ready and
PM is live; only the read/mirror wiring is missing.

## Approach

Implement the **read/mirror** direction only: on a parent person/org feed bump, pull that
parent's `/events` sub-resource and upsert each event into the local `entity_events` mirror,
anchored by `pm_entity_event_id`. Concretely:

1. Add `list_entity_events(events_path, pm_id) -> list[dict]` to the `PowerMapClient` protocol,
   implement it in `GeneratedPowerMapClient` (reuse `_list_paged` for `meta` pagination,
   dispatching `list_person_events` vs `list_org_events` by `events_path`), and add a double to
   `FakeClient`.
2. Override `fetch_record` on the person and org descriptors to fetch the parent via
   `get_entity` and attach `events: [...]` (raw PM dicts) to the returned record.
3. Have person/org `upsert_from_pm` mirror those embedded events into `entity_events`:
   map PM's read `EntityEvent` (nested `PartialDate`, `EventTypeInline`) onto our flat columns,
   keyed on natural key `(source="powermap", source_id=<pm event id>)` with
   `pm_entity_event_id` as the anchor, and set `entity_id`/`entity_kind` from the parent context.

The **write/embed** direction (`to_observation` embedding `events: [...]`) is **out of scope** —
no adapter produces local `entity_events` rows (the table is empty; nothing instantiates
`EntityEvent` outside its definition), so an embed would always emit `events: []`. Defer until a
local event producer exists; file a follow-up.

## Tradeoffs / alternatives

- **Ship read + write together (as #19 literally reads)** — rejected: the write path has nothing
  to embed today, so it would be untested dead code. Clearing the table-shape blocker did not
  create a producer. Read-mirror is the only part with real data to exercise.
- **Embed `events: []` no-op in `to_observation` now** — rejected for this increment: adds a code
  path with no test fixture that asserts anything meaningful, and risks a spurious parent
  write-back. Keep the write surface untouched until a producer lands.
- **A standalone entity-events descriptor** — rejected by the spec (step 6b): events have no
  standalone PM API; they are a sub-resource of person/org and ride the parent feed bump.
- **Mirror `event_type_id` instead of `event_type_slug`** — rejected: PM's read model returns both
  via `EventTypeInline`; our XOR constraint allows exactly one. Prefer the slug (stable, portable
  PM vocab), leave `event_type_id` null.

## Steps

1. **(RED) client protocol + wrapper test.** Add a failing `GeneratedPowerMapClient` wrapper test
   (`test_pmclient.py`) asserting `list_entity_events` dispatches `/people/{id}/events` and
   `/orgs/{id}/events`, paginates via `meta`, and returns raw event dicts. Add the protocol method
   to `client.py`.
2. **(GREEN) implement `list_entity_events`** in `pmclient.py` using `_list_paged` + path-based
   dispatch; add the matching method to `FakeClient` in `testing.py`. Tests pass.
3. **(RED) descriptor mapping test.** Add failing tests for a shared PM-event→local-row mapping
   helper: nested `PartialDate` → flat `event_year…event_second`; `EventTypeInline` → `event_type_slug`
   (id null); `visibility`; `event_place_text`; `linked_entity_type/id` →
   `linked_entity_kind/linked_entity_id`; natural key `(powermap, <pm id>)`; anchor set. Cover the
   partial-date case (year-only) and the linked-entity-absent case.
4. **(GREEN) implement the mapping helper** (shared by person & org, in the sync-powermap package
   or a descriptors util). Tests pass.
5. **(RED→GREEN) `fetch_record` + `upsert_from_pm` wiring** for person and org: `fetch_record`
   attaches `events`; `upsert_from_pm` upserts each embedded event into `entity_events` (insert new
   by natural key, update existing by anchor) with `entity_id`/`entity_kind` from the parent.
   Assert the parent's own `updated_at`/LWW path is untouched by event changes.
6. **Engine integration test** confirming a parent feed item drives a `fetch_record` that mirrors
   events end-to-end through `upsert_from_pm` (extend existing engine/descriptor integration tests).
7. **Run full suite** (`uv run pytest`) + `uv run ruff check .`. Update the `EntityEvent` docstring
   / spec step 6b note to reflect that read-mirror is wired and embed is the remaining follow-up.
   File the embed follow-up issue.

## Open questions / risks

- **Natural-key `source` for PM-originated events.** Plan assumes `source="powermap"`,
  `source_id=<pm event id>` (so `source_id == pm_entity_event_id`). Confirm this is the intended
  convention vs. carrying through the event's own upstream source if PM exposes one. *(Default:
  `powermap`.)*
- **Lossy fields.** PM's read `EntityEvent` carries `event_place_address`, `notes`, `verified_at`,
  `created_at` with no local columns. Plan drops them (mirrors only what the table models). Confirm
  acceptable, or scope a table addition separately.
- **Deletes.** This wires create/update mirror only. Events removed in PM won't be pruned locally
  on a parent bump unless we diff the returned set against existing anchored rows. Plan does the
  diff-and-prune within a parent's event set; flag if that's heavier than wanted for v1.
- **LWW.** Must ensure mirroring a sub-resource does not bump the parent row's `updated_at` and
  trigger a spurious write-back ([[feedback_lww_preserve_remote_clock]]).
