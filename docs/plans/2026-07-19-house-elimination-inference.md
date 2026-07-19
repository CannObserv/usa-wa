---
title: Seat the lone unmatched House member via within-LD elimination (#103)
date: 2026-07-19
status: draft
---

## Problem

The WSL+SOS House Position builder (#101) positions a sitting member only by matching their WSL
roster surname against the SOS **ballot** (`results.vote.wa.gov`). A member who was never on that
ballot under that name — a mid-biennium **appointee** (Obras LD33, Salahuddin LD48) or a member
who **changed their name** between ballot and service (Caldier→Valdez LD26, McCabe→Mosbrucker
LD14) — gets `position_for` = `None` and no House Position seat. Simulated over all 18 archived
bienniums, a within-LD elimination pass would deterministically seat **6** such members
(`missing_position` 2025-26: 7 → 4; 2017-18: 6 → 3), with every inference verified correct and
every guardrail decline correct (43 residual LD-bienniums are genuine sequential-occupancy /
double-turnover gaps). One inference is urgent-ish: Caldier/Valdez is a *sitting* member whose
seat currently reads closed (`valid_to` 2024-12-31) in PM. Full evidence: #103 issue + comment.

## Approach

Add an elimination pass to the pure projector
([`house/projector.py`](../../packages/usa-wa-adapter-sos/src/usa_wa_adapter_sos/house/projector.py)):
per LD, when the roster has **exactly 2 sitting members**, exactly **1 is ballot-matched**, and
exactly **1 of {Position 1, Position 2} remains unclaimed**, the lone unmatched member takes the
remaining position. Track inferred `(member_id, biennium)` keys (PDC #74 precedent) through a new
`HouseSeatProjection.inferred_keys` + `inferred` summary counter; the builder logs them per
cohort. Inferred bienniums cite the **WSL sponsor roster** fetch event (the wire that actually
names the member) instead of `sos-legresults:<Y>` (which never mentions an appointee) — plumbed
via `SponsorRosterCohortProvider.fetch_event_map` into `emit_house_position_spans`. Extend
[`house/migrate.py`](../../packages/usa-wa-adapter-sos/src/usa_wa_adapter_sos/house/migrate.py)
with a within-`usa_wa_legislature` **superseded-collapse** pass (#97 `_retire_onto` pattern) that
runs **before** the PDC pass: elimination deepens 3 existing anchored spans
(Mosbrucker/Slatter/Irwin `…:2019-20` rows), whose stranded rows must transfer their anchors to
the deeper keepers (Mosbrucker's keeper is already anchored → hers is dropped + warned, the
known #80 class).

## Tradeoffs / alternatives

- **Party-constrained inference** — rejected: the position is the seat, not the ballot name; an
  appointee holds it regardless of party, and the name-change heals would fail a name constraint.
- **Cite SOS results for inferred seats (PDC #74 precedent)** — workable but the cited wire never
  names the member; the sponsor roster does (it attests 2 of the 3 inference premises).
- **Scope inference to the current biennium only** — rejected: reintroduces the #100 CR finding-1
  daily-vs-historical depth-mismatch class the one-builder design exists to prevent.
- **Skip the exactly-2-member guardrail** — rejected: free today (all 6 inferences satisfy it) and
  it excludes ghost-row shapes (the recurring LD28 `kilduff` stale roster row).

## Steps

1. Red: failing tests — projector elimination (fires on lone-unmatched 2-member LD; declines on
   3-member roster / both-unmatched / no-ballot-era / both-positions-claimed), `inferred_keys` +
   summary; emit citation switch (inferred biennium cites roster event, matched cites SOS);
   migrate superseded pass (transfer, keeper-already-anchored drop, PDC-maps-to-deep-keeper
   ordering, idempotency).
2. Green: projector elimination pass + `inferred_keys`; build.py threads inferred keys + roster
   events, logs inferred ids, updates the restricted-sweep determinism comment; emit citation
   switch; migrate superseded-collapse before the PDC pass + `superseded_retired` counter.
3. Docs: AGENTS.md (projector/build/migrate lines), COMMANDS.md, fix emit.py's stale
   `sos-whofiled` docstring (it cites the results cohort since #101).
4. Full suite + ruff; CR; merge.
5. Prod window (sidecar paused, before the next 06:45 SOS timer fire): deploy → `house.build`
   (full) → `house.migrate` → resume. Expected: 285 spans, `orphans_no_keeper` 2→0,
   `superseded_retired` 3, anchors transferred 4 (2 PDC + 2 superseded), 1 dropped, net-new PM
   assignments 0.

## Open questions / risks

- **Retraction edge (accepted):** a second same-LD departure mid-biennium blanks the
  ballot-matched member → the earlier inference retracts → the sweep closes the appointee's seat
  until data improves. Rare, self-limiting, no worse than the pre-#103 status quo; documented in
  the builder comment.
- **Mosbrucker's dropped anchor** orphans one PM assignment upstream (PM-side cleanup is the #80
  start-date-gap class — warn + accept).
