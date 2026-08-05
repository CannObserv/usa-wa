---
title: "#144 Phase 1 — curated artifact exclusion (Wynne LD39 Senate 2001-02)"
date: 2026-08-05
---

# #144 Phase 1 — curated artifact exclusion

## Problem

The #145 `--sweep-biennia` audit surfaced two "Bucket B artifacts." External verification against
the official WA Legislature members roster (1889–2025) resolved them:

- **Marlo Braun (Id 27981)** — GENUINE (a ~1-week U.S. Naval Reserve military substitute for John
  Braun, July 18–23 2017). Matches the accepted Benson/Wyss overlap precedent → keep, no action.
- **John Wynne (Id 481)** — CONFIRMED ARTIFACT. The roster lists `Wynne, John — H-39` (House only,
  one term 1991); Val Stevens held LD39 **Senate** continuously 1997–2012. So Wynne's
  `481:chamber-senate:39:2001-02` span and its paired `481:party:republican:2001-02` span are
  spurious (a chamber-conflation in the WSL `GetSponsors` archive; he was not in the legislature in
  2001-02 at all). His real footprint — `481:party:republican:1991-92` (the 1991 House term) — is
  legitimate and must stay.

These spurious spans are **re-derived from the immutable `sponsors:2001-02` archive** on every
unrestricted rebuild. `roster_hygiene` cannot catch them — it only flags *committee-absent*
departed-ghost rows, and this artifact is a fully-formed WSL row (committee-present). So a plain
delete gets re-created. A durable correction must exclude the member at the **derivation** layer.

## Approach

Add a small **curated, evidenced artifact denylist** — `ARTIFACT_EXCLUSIONS_BY_BIENNIUM:
{biennium -> {member_id}}` — in a new pure module, and union it into the `exclude_ids_by_biennium`
set that `build_sponsor_observations` already honours (the existing #105 (b) exclusion seam). The
merge happens in `harvest_sponsor_spans.build_sponsor_spans` **after** the operator-event exemption
subtraction, so the denylist is a hard exclusion nothing else can remove. #54-safe (the archive is
never rewritten; the correction lives in the canonical-derivation layer). Wynne carries no operator
events, so no interaction with the #107/#157 overlay.

Seat + party for 2001-02 both come from the **sponsor** builder, so only `harvest_sponsor_spans`
needs wiring; `house.build` is untouched (no House artifact). The denylist module stays
builder-agnostic (plain dict + a pure union helper) so a future House-Position artifact can adopt
it trivially, but that wiring is deliberately deferred (YAGNI).

This is **Phase 1** of the two-phase fix. It prevents re-derivation; it does **not** remove the
existing already-closed PM-anchored rows (that is Phase 2, blocked on power-map#391's producer
retraction verb). Phase 1 is the prerequisite that makes any eventual retraction *stick* — without
it the next backfill would re-produce the retracted assignment.

## Tradeoffs / alternatives

- **Delete the local rows only** — rejected: re-derived on the next unrestricted rebuild.
- **Tombstone + `/admin/` archive on PM** — rejected (user direction): out-of-band; retraction
  belongs on the producer `/observations` channel → filed as power-map#391 (Phase 2).
- **Extend `roster_hygiene`** — rejected: it is a *data-driven* committee-corroboration mechanism;
  a manually-curated, evidence-backed denylist is a distinct concern and belongs in its own module.

## Steps

1. **RED** — new `test_member_artifacts.py`: assert `481 ∈ ARTIFACT_EXCLUSIONS_BY_BIENNIUM["2001-02"]`
   and that `with_artifact_exclusions` unions the curated set into a caller dict without dropping
   existing entries. New behavioural test in `test_sponsor_observations.py`: with the curated
   exclusions applied, member 481's 2001-02 party+Senate observations are dropped while the 1991-92
   party observation survives.
2. **GREEN** — add `member_artifacts.py` (`ARTIFACT_EXCLUSIONS_BY_BIENNIUM` + pure
   `with_artifact_exclusions`), each entry docstring-cited to the WA Leg roster PDF.
3. Wire `with_artifact_exclusions` into `harvest_sponsor_spans.build_sponsor_spans` after the
   operator-exemption subtraction.
4. **Verify** — full suite green; ruff clean. Optionally re-run an unrestricted `harvest_sponsor_spans`
   on a scratch/dry basis to confirm Wynne's 2001-02 spans are no longer asserted (existing closed
   rows persist — Phase 2).
5. Update AGENTS.md (the adapter-legislature module map) with the new module + the #144 note.

## Open questions / risks

- **Existing closed rows persist** until Phase 2 (power-map#391). Expected and documented; daily gate
  stays green (they are `is_active=f`). `--sweep-biennia` keeps flagging LD39 2001-02 until then.
- Low risk overall: pure additive exclusion of one evidenced (member, biennium); no schema change, no
  PM write, no operator events.
