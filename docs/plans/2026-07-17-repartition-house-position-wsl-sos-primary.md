---
title: Re-partition House Position — WSL+SOS primary, PDC identifier-only (symmetric with Senate)
date: 2026-07-17
status: draft
---

# Re-partition House Position — WSL+SOS primary, PDC identifier-only

Tracks usa-wa#101. Supersedes the #100 transitional fallback wiring (not a plan file — a
code path). Related: power-map#302 (at-large seat-model gap, out of scope here).

## Problem

Post-#100, the WA House Position seat is built by two builders that mint the **same span
identity at different depths**. A member serving across the 2018 boundary gets a single deep
open span (`{m}:chamber-house:{disc}:2017-18`) from the SOS-fallback builder
(`build_sos_house_spans`), but the daily `usa-wa-pdc-refresh` runs `build_pdc_spans`
**without** the SOS fallback → a shallow `…:2019-20` span, and `close_stale_spans` (which scans
all open `usa_wa_pdc` `chamber-house` rows) closes the un-asserted deep one within ~24h.
Proven empirically in #100's CR (finding 1): `closed=1`, deep span `is_active=False`,
`valid_to=2024-12-31`. The historical backfill's value for continuing-across-2018 members is
undone by the next timer fire, so `build_sos_house_spans` is **not prod-safe** today. Patching
the daily refresh to carry the fallback inverts the dependency direction (PDC must not import
SOS) and leaves the House permanently asymmetric with the Senate.

## Approach

Re-partition so the House is **symmetric with the Senate** (#75): WSL owns seat/roster
structure for both chambers; PDC and SOS enrich. One builder becomes the single source of House
Position span identity — driven daily (current roster + current SOS filing) **and**
historically (2008+) by the same pipeline — so finding 1 cannot recur by construction.

- **House membership** (who sits / LD / party / biennium): WSL sponsor roster (authority to
  1991, archived #77) — already the roster the builders read.
- **House Position 1/2**: SOS filings, **primary and uniform** 2008→present (the #100 votewa
  adapter, promoted from fallback to primary; PDC no longer supplies it).
- **House `person_wa_pdc` link**: PDC, **identifier-only** (demoted, symmetric with its Senate
  role).
- **Assignment `source`**: `usa_wa_legislature` (a seat is legislature structure — Senate
  precedent; `sponsor_span_emit` emits `chamber-senate` as `usa_wa_legislature`). The seat Role
  is already get-or-created `usa_wa_legislature`; only the Assignment `source` flips.

A new House Position span builder lives in the **SOS package as the composition root** (it
already is — `build_sos_house_spans` composes WSL roster + PDC + SOS). SOS imports WSL (roster +
seat emit) and PDC (identifier-only); WSL and PDC keep **zero** SOS imports (SOS injects the
position-lookup callable, per #100's inversion). The new builder emits the **identical**
`source_id` discriminator (`{m}:chamber-house:{disc}:{start}`) so the migration is close to an
in-place `source` re-point, not a true span collapse.

**OQ1 resolved (see #101 comment):** a matched member with no resolvable Position → **emit
nothing** (party + committee coverage stands). Post-1965 "position unknown" is a data gap on a
seat that structurally *has* a position; emitting a position-less `state_representative` is both
PM-rejected (`requires_qualifier=True`) and a false structural claim. The position-less shape is
reserved for the genuine pre-1965 at-large seat (power-map#302), which is below every current
data floor and out of scope here.

## Tradeoffs / alternatives

- **Patch the daily PDC refresh to carry the SOS fallback** — rejected: inverts the dependency
  (PDC importing SOS), keeps the House asymmetric with the Senate (two seat-authority models
  forever), and only masks finding 1 rather than removing the two-builder root cause.
- **Keep PDC as House Position authority, gate the stale-span sweep to skip cross-2018 spans**
  — rejected: a special-case guard on a general sweep; brittle, and still leaves two builders
  and the SOS-vs-PDC depth mismatch latent.
- **Model position-unknown House members as position-less `state_representative` membership**
  — rejected: PM rejects it (`requires_qualifier=True`), and it conflates the post-1965 data gap
  with the genuinely-different pre-1965 at-large seat (power-map#302). Reserve that shape.
- **Introduce a House at-large role_type now** — rejected as out of scope: the entire at-large
  era is below the 1991 data floor, so nothing is ingested yet; tracked upstream as power-map#302.

## Steps

1. **New WSL+SOS House Position span builder (TDD).** Mirror `sponsor_span_emit`'s Senate seat:
   resolve the sitting House roster per biennium from the WSL sponsor archive, look up the SOS
   Position qualifier per `(LD, folded_last, party)`, emit one `state_representative` Position
   seat span per tenure with `assignment_source=usa_wa_legislature`, **identical `source_id`
   discriminator** `{m}:chamber-house:{disc}:{start}`, cite the SOS `sos-whofiled:<Y>` cohort per
   biennium. Matched-but-no-position → emit nothing (OQ1). Verifiable: unit + end-to-end tests
   assert a cross-2018 member builds one deep open `usa_wa_legislature` span, and a
   no-position member builds no House seat.
2. **Stale-span sweep for the new builder.** Wire `close_stale_spans(assignment_source=
   'usa_wa_legislature', kinds={'chamber-house'})` into the new builder, with the mass-close
   guard (fraction computed over open `chamber-house` rows only). Verifiable: a departed
   House member's open span closes at `current-1`; a mass-close aborts.
3. **Daily driver.** Promote the SOS composition root to a real daily driver (own oneshot +
   timer, ordered **after** `usa-wa-wsl-refresh`), rebuilding current-roster House Position with
   the SOS lookup wired via `restrict_to_biennium`. Same builder as step 1's historical path.
   Verifiable: a scoped daily run rebuilds only current members, each keeping full history.
4. **Demote PDC to identifier-only for the House.** Retire `build_pdc_spans`'s House Position
   emission **and** its `close_stale_spans(usa_wa_pdc, {chamber-house})` sweep; keep Senate
   identifier links + `person_wa_pdc`. Verifiable: `build_pdc_spans` emits 0 House spans, N
   identifiers; existing Senate tests stay green.
5. **Migration (owner role, #54).** Re-source existing `usa_wa_pdc` House Position Assignments
   → `usa_wa_legislature`: transfer the PM anchor (stable — `(person, role, start_date)`
   unchanged), repoint citations, converge on the identical `source_id`. Mirror the #79/#82/#97
   `_retire_onto` index-safe discipline. `--dry-run`; idempotent. Verifiable: a dry-run reports
   the row count; a live run leaves one `usa_wa_legislature` row per prior `usa_wa_pdc` House
   seat with its anchor intact and no `uq_assignments_pm_assignment_id` collision.
6. **Deploy sequencing + guard cleanup.** Runbook: sidecar paused, run WSL + SOS harvests +
   migration in one window (before the first WSL House build drains to PM). Lift the #100 interim
   guards (docstring/AGENTS note marking `build_sos_house_spans` not-prod-safe). Update
   AGENTS.md, docs/COMMANDS.md, and the systemd unit matrix for the new daily driver.
7. **Verify end-to-end + close #101.** Full `pytest` green, `ruff` clean; drive the new daily
   path against a scoped biennium and confirm a cross-2018 member's deep span survives a
   simulated second daily fire (the finding-1 regression test).

## Open questions / risks

- **Driver placement** (RESOLVED 2026-07-17 — standalone): a new standalone SOS refresh oneshot
  + timer, ordered after `usa-wa-wsl-refresh`. Keeps SOS the composition root and the dependency
  direction clean (no SOS import in the WSL refresh entrypoint). Step 3 builds this unit.
- **`source_id` divergence (RESOLVED — collapse, not re-point; found in CR)**: the *discriminator*
  (`ld-{n}-position-{p}`) is identical, but the full 4-part `source_id`'s **`{start}`** component
  **diverges** for the central cohort — PDC omits the pre-2018 position, so a cross-2018 incumbent's
  existing PDC span is shallow (`…:2019-20`) while the SOS builder emits a deeper `…:2017-18`. So
  step 5 is a true **covering-window collapse** (map by `(person, role)` + window, `_retire_onto`),
  **and the deploy order is build → migrate** (the deep keeper must exist for the collapse). An
  in-place flip would strand the shallow row's anchor on a superseded row and duplicate the PM
  assignment (invisible to the #86 index). This is the `migrate_pdc_spans` #91/#97 pattern.
- **Migration/sidecar race**: the anchor transfer must complete before the first
  `usa_wa_legislature` House build drains to PM, or the new row collides with the old
  `usa_wa_pdc` row on the #86 anchor unique index. Enforced by the paused-sidecar sequencing
  (step 6) — a real operational risk if skipped.
- **Coverage change is intentional**: Position coverage becomes uniform 2008→present (SOS
  floor), *better* than the fallback model; pre-2008 House stays honestly position-less (party +
  committees still covered). No regression, but call it out in the deploy notes.
- **Pre-2018 PDC `person_wa_pdc` identifier backfill (follow-up, not blocking)**: the retired
  `build_sos_house_spans` was the only driver that injected the SOS fallback into
  `build_pdc_spans`, which is what let a pre-2018 House winner match (the identifier link is
  coupled to a resolved position). `build_pdc_spans` retains the `house_position_fallback` param,
  so the capability survives, but no CLI currently injects it for a historical identifier
  backfill. The daily 2018+ identifiers are unaffected (PDC has positions there). Pre-2018
  historical `person_wa_pdc` links are an enrichment deferred to a follow-up run — noted so it is
  not mistaken for a regression.
