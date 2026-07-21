---
title: Operator succession events — mid-biennium interjection mechanism (#107)
date: 2026-07-21
status: draft
---

# Operator succession events — implementation plan (#107)

Spec: [`docs/specs/2026-07-21-operator-succession-events-design.md`](../specs/2026-07-21-operator-succession-events-design.md)

## Problem

Tenure spans are biennium-quantized, so a mid-biennium succession (death, resignation, appointment) after genuine service is invisible to every #105 wire signal: a departed member stays named + committee-listed in the cumulative wire, so their span stays ghost-open up to a full biennium, and an appointee's span starts at the biennium floor instead of the appointment date. The LD5 Ramos/Hunt case reads 50/148 open seats instead of 49/147. No wire supplies intra-biennium dates; operators know these facts (news-first) and need a robust, durable way to interject them.

## Approach

A first-class `usa_wa_operator` provenance Source backing an event-shaped operator facility. A new `operator_events` table (model in `clearinghouse-domain-legislative`) is fed by a live app-role CLI (direct args or `--file` batch) in `usa-wa-adapter-legislature`; each write also appends a hashed `FetchEvent`+`RawPayload` so the integrity sweep covers operator facts. A pure `apply_operator_events` overlay runs after `build_tenure_spans` and before `emit_spans` in all three span builders (sponsor, SOS house, committee membership), authoritative over both wire projection and #105 hygiene: `departed` closes every open span for the member at `effective_date`; `seated` sets/synthesizes the named seat's `valid_from`. Because the daily refreshes re-drive the builders, the overlay re-applies every run (self-durable; correction = edit the row, appending a superseding provenance record). A dedicated guarded `usa-wa-succession-invariants` oneshot+timer asserts the chamber-count (49/98/147) and duplicate-occupancy invariants daily, emailing on drift — the anti-drift backstop and the #107 acceptance oracle. The degenerate one-day-span clamp fix lands first, independently.

## Tradeoffs / alternatives

- **Curated seed file (original #107 option 1)** — rejected: a hand-maintained checked-in file drifts silently, is deploy-gated, and sits outside the live query path. The DB-table-with-invariant-alerts design is the robust generalization the user asked for.
- **Span-override-shaped assertions** — rejected: forces operators to know internal `source_id`s and breaks when span keying changes. Event-shaped input survives refactors and needs no pipeline knowledge.
- **Apply-once mutation + lock flag** — rejected: a per-row lock is a new invariant every sweep/emit path must honor, and a missed check silently reopens the ghost. Overlay-every-build is idempotent and has fewer failure modes.
- **Event model as a Layer-1 framework primitive** — deferred (YAGNI): the events reference members/seats (legislative-domain concepts); hoist to a shared primitive only when a second jurisdiction needs it.
- **Do nothing / document residual (option 3)** — rejected: the error is externally visible in PM counts and persists ~1 biennium; not an acceptable residual on the flagship dataset.

## Steps

1. **Degenerate-span fix (independent).** In [`span_emit.close_stale_spans`](../../packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/span_emit.py), stop emitting `valid_to == valid_from` for a fully-excluded single-biennium span; close with a distinct marker instead. Red→green test on the Hunt-shaped single-biennium exclusion. Commit standalone.
2. **Event model + migration.** Add `OperatorEvent` to `clearinghouse_domain_legislative` (ULID PK, `TimestampMixin`, `source`/`source_id`, `member_id`, `kind`, `reason`, `seat` nullable, `effective_date`, `evidence_url`, `entered_by`, `entered_at`, `superseded_by_id` self-FK). Alembic autogenerate + add DML grants to `scripts/grants.sql`. Verify: migration applies under owner role, app role can DML.
3. **Operator Source + provenance write.** Add `get_or_create_operator_source()` to `provisioning.py`; a `record_operator_event()` helper that appends `FetchEvent`+`RawPayload` (canonical JSON body, `content_hash=sha256`) and upserts the projection row; `--supersede` appends fresh provenance and stamps the prior row. Verify: one write → one FetchEvent+RawPayload, hash matches, integrity sweep passes; supersede appends a second + stamps.
4. **Overlay (pure).** `operator_overlay.apply_operator_events(spans, events, *, current_biennium)` — `departed` closes all open member spans at date; `seated` sets/synthesizes the seat span's `valid_from`; a member with any event is exempt from #105 mover/stale exclusion for the affected seat. Table-driven tests with the three LD5 golden rows.
5. **Builder integration.** Wire the overlay into `build_sponsor_spans`, `usa_wa_adapter_sos.house.build`, and `harvest_committee_member_spans` (fetch current non-superseded events, apply between projection and emit, thread the operator `citation_target` so affected Assignments cite the attestation). Verify each builder end-to-end on the LD5 fixtures.
6. **Operator CLI.** `python -m usa_wa_adapter_legislature.operator_events` — direct-arg + `--file` + `--supersede` + `--list`/`--dry-run`; validates `member_id` resolves to a Person. Verify: single + batch write; refuses unknown member.
7. **Invariant oneshot + alerts.** `succession_invariants` module: chamber-count (49 Senate / 98 House / 147) + duplicate-occupancy checks against the live open cohort; exit non-zero + descriptive log on violation. Add `deploy/usa-wa-succession-invariants.{service,timer}` (after the refreshes; `assert-main-checkout` + `OnFailure=usa-wa-notify-failure@` + `--frozen --no-sync`); register in `test_unit_ordering.py`. Verify: 49/98 exits 0; synthetic ghost → non-zero + alert path.
8. **Refresh re-drive + docs.** Confirm the daily refreshes pass current events to the overlay (they already re-drive the builders). Update `AGENTS.md` (package map + Common Commands + Infrastructure table) and `docs/COMMANDS.md` with the new CLI + timer. Full `pytest` + `ruff` green.
9. **Deploy + backfill LD5.** Migrate, install/enable the new unit, then enter the Ramos `departed` + Hunt `seated` events (sidecar-aware, per #80 orphan caution if it drains). Verify live: counts 49/98/147, invariant oneshot exits 0.

## Open questions / risks

- **Sidecar interaction on backfill (step 9).** Closing/re-opening spans changes `valid_from`/`start_date`, which is in PM's assignment match key — a naive re-observe could mint an orphan (#108/#311). The overlay edits existing rows in place (anchored, id-addressed per #111), so it should update-in-place, not orphan — but the backfill should run sidecar-aware and be watched. Confirm the id-addressed path covers a `start_date` move before the live backfill.
- **`seat` encoding for `seated`.** The seat descriptor must round-trip to the right builder's Role resolver (Senate LD / House LD+Position / committee id). Settle the exact encoding + a `kind` tag in step 4; keep it symmetric with the existing span discriminators.
- **Invariant constants.** 49/98/147 are current WA chamber sizes; if WA ever redistricts the count, the constants need updating (low risk, decadal). Note them as named constants, not magic numbers.
- **Committee-span overlay scope.** `departed` closing *all* committee spans at the death date is correct, but committee spans are higher-volume; confirm the overlay's all-open-spans close doesn't trip `close_stale_spans` mass-close guards (it edits by member, not in bulk, so it shouldn't — verify in step 5).
