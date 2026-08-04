---
title: Scope the operator event-member stale-exemption to a member's event window (#145)
date: 2026-08-04
status: draft
---

# Scope the operator event-member stale-exemption to a member's event window

## Problem

The 2013-14 re-proof tranche (step 6) cleared its 8 conflicts with zero superseded rows — the #145 chamber-move mechanism works — but introduced a **+1 new conflict** (LD28 Senate O'Ban/Nobles, 2021-22). Root cause: the event-member stale-exemption is **biennium-global**. [`harvest_sponsor_spans.py:127`](../../packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/harvest_sponsor_spans.py) does `exclusions = {b: (ids − event_members) …}`, subtracting every operator-touched member from **every** biennium's `roster_hygiene` stale set (`house/build.py`'s `keep_ids = event_members − movers` is the analogous global exemption). A member with an event in one biennium is therefore un-excluded in **all** biennia — so O'Ban (a 2013-14 chamber-mover) is exempt in 2021-22 too, where he genuinely left (lost to Nobles, a normal biennium-boundary defeat `roster_hygiene` would close). His cumulative-wire ghost survives → his Senate span stretches to 2022-12-31 → the duplicate. Latent for any multi-career event-member (silent span-inflation even where no duplicate surfaces).

## Approach

Scope the exemption to **biennia ≤ the member's latest operator-event biennium**. A member who is committee-stale in a biennium *after* their last event is a genuine post-event ghost and must stay excluded. Concretely: add a pure `stale_exempt_members(events, biennium)` to `operator_overlay.py` returning the member ids whose latest event biennium (by `biennium_for_date(effective_date)`) is ≥ the given biennium; both builders subtract *that* per-biennium set instead of the global `event_member_ids`. This is safe because stale exclusion only ever bites a **committee-absent** member — a genuinely-serving event-member is committee-present and never in the stale set, so narrowing the exemption only affects true ghosts. For O'Ban: exempt in ≤2013-14 (a no-op there — he isn't stale), excluded in 2021-22 (correct close).

## Tradeoffs / alternatives

- **Add a `departed`/`vacated chamber-senate defeated` event for O'Ban's 2020 loss** — rejected: O'Ban→Nobles is a regular biennium-boundary election, not a mid-biennium succession; modeling it as an operator event is wrong *and* leaves the same latent inflation for every other multi-career event-member unaddressed.
- **Scope to the exact biennium(s) a member has an event in (not ≤ latest)** — rejected: a member with a `departed` event mid-2015 who was archive-gap-stale in 2013-14 needs their earlier span built too; `≤ latest` keeps the whole pre-departure tenure exempt, and post-departure ghosts still fall out.
- **Only fix the sponsor builder** (the O'Ban conflict is a Senate seat) — rejected: `house/build.py` carries the identical global exemption, so a House member with an early event who later ghosts would regress the same way; fix both for symmetry.

## Steps

1. **Pure helper (test-first).** Add `stale_exempt_members(events, biennium) -> set[str]` (+ the `latest_event_biennium_by_member` it uses) to `operator_overlay.py`, comparing biennia by `parse_biennium(...)[0]`. Unit-test in `test_operator_overlay.py`: a member with a 2013-14 event is exempt in ≤2013-14, not in 2015-16+; multiple events → latest wins; empty → empty.
2. **Sponsor builder.** Replace `harvest_sponsor_spans.py:127`'s global subtraction with the per-biennium `stale_exempt_members(events, b)`. Add an integration test (O'Ban-shaped: an event-member committee-stale in a later biennium is stale-excluded there, span not extended).
3. **House builder.** In `house/build.py`, change `keep_ids = event_members − movers_by_biennium[b]` to `keep_ids = stale_exempt_members(events, b) − movers_by_biennium[b]`. Add the symmetric house-build test.
4. **Docs.** Update the `operator_overlay` / `harvest_sponsor_spans` / `house/build.py` AGENTS.md entries to state the exemption is biennium-scoped.
5. **Ship + re-proof.** CR → merge → deploy. Then re-run the 2013-14 re-proof (sidecar paused): expect 57 → 49 (the 8 cleared, **no** 2021-22 regression), zero superseded rows, daily 49/98.

## Open questions / risks

- **Biennium comparison.** Use `parse_biennium(label)[0]` (start-year int), not lexical string compare, for ordering robustness across the century boundary — covered by a unit test.
- **Re-proof is the acceptance gate.** The fix is only proven when the 2013-14 tranche re-runs to 57→49 with no new conflict; keep the tranche-by-tranche discipline (it has now caught two distinct mechanism gaps).
