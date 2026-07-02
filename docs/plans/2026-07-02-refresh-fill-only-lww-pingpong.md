---
title: Stop the daily-refresh LWW ping-pong — fill-only org upsert + one-shot heal (#65 Part 2)
date: 2026-07-02
status: draft
---

# Stop the daily-refresh LWW ping-pong (#65 Part 2)

## Problem

The #64 validation surfaced 34/34 standing committees divergent from PM. Root
cause (diagnosed under #65): the daily WSL refresh re-produces every committee via
`GetActiveCommittees` and the runner's `_upsert` does `ON CONFLICT DO UPDATE SET
<all set cols>`. Postgres fires that UPDATE on **every** re-pull — even when values
are unchanged — overwriting PM-curated `name`/`acronym` and bumping `updated_at`.
That daily clock bump makes the local row win LWW against PM's curation, producing
(a) a daily **4,080-entry outbox ping-pong** for 58 orgs and (b) **13 committees
permanently LWW-locked** (local clock ahead of PM), so PM's curation — and the #65
Part 1 acronym fix — can never be adopted. This also blocks sub-project 3: a
historical backfill through the same path would amplify the ping-pong across
hundreds more orgs.

## Approach

Two parts. **(1) Make the refresh's org upsert fill-only.** Add an opt-in
`fill_only` flag to `AdapterRunner`; when set, `_upsert` uses `ON CONFLICT DO
NOTHING` (insert new rows, never touch existing), exactly the #39 seed-ingest
stance ("floor, not authority"). The WA refresh constructs its runner with
`fill_only=True` for both the committee and meeting-window pulls. This aligns with
the refresh's documented "additive discovery" purpose, stops the name/acronym
clobber, and — because no UPDATE fires — stops the daily `updated_at` bump and the
outbox churn at the source. Archival (FetchEvent + RawPayload + `content_hash`) and
the per-fetch Citation are unaffected (they don't depend on the conflict policy).
**(2) One-shot heal for the 13 locked committees.** A small CLI that, for the
anchored produced cohort, re-fetches each PM `OrgDetail` and force-applies PM's
curated `name`/`acronym`/name-windows/acronyms locally via the existing
`upsert_from_pm`, bypassing the LWW clock check, then stamps clock parity — so the
already-locked rows adopt PM's curation once. After the fill-only fix, they stay
healed (no future clobber). Idempotent.

## Tradeoffs / alternatives

- **Preserve only `name`/`acronym` in the UPDATE (surgical column exclusion)** —
  rejected: `ON CONFLICT DO UPDATE` still fires for the remaining columns, so
  `updated_at` still bumps daily and the outbox ping-pong continues. Only DO
  NOTHING stops the churn.
- **Anchor-gated update (`DO UPDATE` only when `pm_organization_id IS NULL`)** —
  rejected for now: needs domain (`pm_organization_id`) knowledge inside the
  generic runner, or SQL `CASE` conditionals; more complex for no real gain, since
  the refresh has no legitimate need to update an existing committee's PM-owned
  fields (renames flow via the reconcilers → PM → mirror; contact via #31).
- **Heal by rewinding `Organization.updated_at` below PM's** — rejected: fragile
  clock arithmetic; force-applying PM's record via `upsert_from_pm` is explicit and
  reuses the audited mirror path.
- **Leave the refresh as-is, one-shot heal only** — rejected (the user's chosen
  path is the systemic fix): the ping-pong and re-lock would recur daily.

## Steps

1. **Runner `fill_only` — tests first.** Extend the runner tests: with
   `fill_only=True`, a re-`refresh()` of an existing natural-key row does **not**
   change its mutable columns and does **not** bump `updated_at`, while a new row
   still inserts; citations + archival still written both runs. Assert the default
   (`fill_only=False`) keeps today's DO-UPDATE behavior (regression guard).
2. **Runner `fill_only` — implementation.** Add the flag to `AdapterRunner.__init__`;
   in `_upsert`, when `fill_only` force `on_conflict_do_nothing` (keep the id
   read-back for citations). Green step 1.
3. **Refresh uses fill-only — test + wire.** Assert `refresh.py` builds its
   `AdapterRunner` with `fill_only=True`; a refresh over an existing committee
   leaves its PM-curated `name`/`acronym` intact. Update the AGENTS.md refresh notes
   ("additive discovery = fill-only; existing rows are PM's to curate").
4. **One-shot heal — tests first.** Seed an anchored org whose local `updated_at`
   is ahead of a crafted PM record carrying curated name/acronym/windows; assert the
   heal force-applies PM's values, mirrors the windows, and is idempotent (second
   run a no-op / clock parity).
5. **One-shot heal — implementation.** Add
   `usa_wa_sync_powermap.heal_committee_curation` (read-only PM + local write via
   `upsert_from_pm`, LWW bypass for the anchored cohort). `--dry-run` previews. No
   operator token (shell = trust boundary). Green step 4.
6. **Suite + lint + docs.** `uv run pytest` + `ruff` green; document the heal CLI.
7. **Operational (prod).** Deploy the fill-only refresh (restart the timer unit).
   Run the heal once. Re-run `validate_committees` → expect `acronym_drift` cleared
   for the 21 (via #65 Part 1) and the 13 name/window drift resolved; confirm the
   outbox stops accruing daily org UPDATEs.

## Open questions / risks

- **Heal scope — 13 vs whole anchored cohort?** Proposing the whole anchored
  produced cohort (idempotent; a no-op on the already-parity 21). Confirm before
  step 5.
- **Risk — fill-only hides a genuine WSL attribute change** (e.g. a committee's
  phone changes). Accepted: renames flow via the reconcilers; other attribute
  updates for existing committees are out of the daily refresh's discovery scope
  and, if needed later, belong in a dedicated reconcile — not the clobbering path.
- **Risk — the heal write races the sidecar.** Low; both use `upsert_from_pm` and
  end at clock parity. Run the heal once and verify via `validate_committees`.
