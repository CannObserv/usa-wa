# Operator succession events — mid-biennium interjection mechanism

- **Date:** 2026-07-21
- **Status:** proposed
- **Scope:** A robust operator-attestation facility for mid-biennium/-session succession events (death, resignation, appointment) the WSL/SOS/PDC wires structurally cannot supply, plus the invariant alerts that make missing interjections visible. Fixes the #107 LD5 case (Ramos/Hunt) as its acceptance oracle.
- **Issues:** closes #107; related #105 (the hygiene signals this class evades), #103 (elimination seats the successor but can't end the predecessor), #106 (odd-year ballot evidence for the successor's *elected* status — orthogonal), power-map#302 (seat modeling).

## Problem

Tenure spans are **biennium-quantized** ([`tenure_spans.build_tenure_spans`](../../packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/tenure_spans.py) snaps every `valid_from` to Jan 1 of the odd year and opens `valid_to=None` for any span reaching the current biennium). Succession — death, resignation, appointment — is a **within-biennium** event. No wire the pipeline reads carries intra-biennium service dates, and the historical `GetCommitteeMembers` roster is *cumulative* for the biennium, so a member who dies mid-biennium stays fully named **and** committee-listed in the current wire. Every #105 hygiene signal (committee-absence stale exclusion, mover exclusion) is therefore evaded.

Worked case (#107, verified live 2026-07-20 — LD5 Senate 2025 succession):

| Fact | Our record | Defect |
|---|---|---|
| Bill Ramos: Senate Jan 2025, **died 2025-04-19** | `29091:chamber-senate:5:2025-26` open (+ `party` open) | Ghost-open. Named + committee-listed in the cumulative wire, so no hygiene signal fires. Closes ~20 months late (2027-28 rebuild) and even then overstates tenure. |
| Victoria Hunt: House Jan–June 2025 | `35410:chamber-house:ld-5-position-1:2025-26`, `2025-01-01 → 2025-01-01`, closed | Degenerate one-day span: the mover exclusion removed her only House biennium; the #83 sweep clamps `valid_to >= valid_from`. |
| Hunt: Senate from **2025-06-03** (appointed) | `35410:chamber-senate:5:2025-26`, `valid_from 2025-01-01`, open | Start ~5 months early (biennium floor). LD5 shows **two open senators** until the 2027 boundary. |

**Consequence in Power Map:** open-seat counts read 50 Senate / 148 total instead of 49 / 147, and the drift persists up to a full biennium with nothing surfacing it.

These events are **operationally common** and typically **news-first** — known before they reach any official machine-readable source. The system needs a first-class way for an operator to interject the fact, robustly and durably, not a one-off hand-maintained seed file (which drifts silently).

**Non-goal:** this is not a general "override any span" facility. It is a bounded, event-shaped succession vocabulary. A raw span-override escape hatch is explicitly deferred (YAGNI) until an event the vocabulary can't express actually appears.

## Design decisions (settled in brainstorming)

| Axis | Decision |
|---|---|
| Interface | DB table (`operator_events`) as the live authority, fed by a CLI accepting **either direct args or a `--file` batch**. No deploy gate. |
| Assertion shape | **Event-shaped** — operator states the real-world fact; the builder derives which spans close/open. Operator needs zero knowledge of `source_id`s. |
| Precedence | **Overlay consulted every build** — the span builders read the events and re-apply them as an authoritative layer on every run. Idempotent, self-durable; correction = edit the row. |
| Detection | **Chamber-count invariant** + **duplicate-occupancy** daily checks, emailing `USA_WA_ALERT_EMAIL`. |
| Provenance | **First-class `usa_wa_operator` Source, append-only** — each write appends a `FetchEvent` + `RawPayload` (hashed → integrity sweep covers it); affected spans carry a `Citation`; corrections append a superseding record. |
| Placement | Event **model** in `clearinghouse-domain-legislative`; overlay **logic + CLI** in `usa-wa-adapter-legislature`. |

## Architecture

Five units, each independently testable.

### 1. Event model — `clearinghouse_domain_legislative`

A new `operator_events` table (ULID PK, `TimestampMixin`). Columns:

- `source` / `source_id` — the `usa_wa_operator` Source + a deterministic event key (`{member_id}:{kind}:{seat_or_none}:{effective_date}`), so a re-ingest of the same event is an idempotent upsert.
- `member_id` — the WSL `Id` (`Person.source_id` under `usa_wa_legislature`).
- `kind` — enum `departed | seated`.
- `reason` — sub-tag: for `departed`, `died | resigned | expelled`; for `seated`, `appointed | sworn_in`.
- `seat` — the target seat descriptor, **required for `seated`, null for `departed`** (a death ends everything; an arrival names one seat). Encoded as the span discriminator the builders already use (`chamber-senate:{ld}`, `chamber-house:ld-{n}-position-{p}`, `committee:{id}`) plus a `kind` tag identifying which builder owns it.
- `effective_date` — the succession boundary.
- `evidence_url` — operator's cited source (news/official).
- `entered_by`, `entered_at` — audit (live table, git is not the trail).
- `superseded_by_id` — self-FK; a correction appends a new row and points the old one at it. The overlay reads only non-superseded rows.

The model is legislative-domain (it references members and seats), so it lives beside `identity.py`. Migration adds the table + `grants.sql` DML for the app role.

### 2. Provenance write — operator Source

Provisioning gets `get_or_create_operator_source()` (`source_slug=usa_wa_operator`, archival retention), mirroring the SOS `provisioning.py` pattern. Each CLI write:

1. Serializes the event to canonical JSON bytes.
2. Appends a synthetic `FetchEvent` + `RawPayload` (body = the JSON; `content_hash = sha256(body)` via the existing runner chokepoint semantics), so `clearinghouse_core.integrity` sweeps operator facts identically to wire facts.
3. Upserts the `operator_events` projection row.

A correction re-runs (1)–(3) appending fresh provenance and stamping `superseded_by_id` on the prior row. **Provenance is never mutated** (#54 append-only holds).

### 3. Overlay — pure, in `usa-wa-adapter-legislature`

`operator_overlay.apply_operator_events(spans, events, *, current_biennium) -> list[TenureSpan]` — a pure function run **after** `build_tenure_spans` and **before** `emit_spans` in all three builders. It is authoritative over both wire projection **and** #105 hygiene:

- **`departed`** — for every open span of `member_id`, set `valid_to = effective_date`, `is_active = False`. Terminal: closes seat + party + all committee spans at the death/resignation date.
- **`seated`** — for the span matching `member_id` + `seat`, set `valid_from = effective_date`. If the wire produced no such span (or #105 dropped it), **synthesize** the span from the seat descriptor so the arrival is recorded.
- **Hygiene override** — a member named in any operator event is exempt from the mover/stale exclusions for the affected seat, so Hunt's real House tenure (`floor → 2025-06-03`) is re-instated rather than collapsed to a one-day span, and her Senate span starts `2025-06-03`.

Because the daily refresh re-drives every builder and the overlay re-applies each run, the wire can never win back a corrected span. The overlay carries the `citation_target` for affected spans through to `emit_spans` so the closed/opened Assignment cites the operator attestation (via the injected `CitationLocator`, extended to resolve operator events to their `FetchEvent`).

Consumed by: [`harvest_sponsor_spans.build_sponsor_spans`](../../packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/harvest_sponsor_spans.py) (party + Senate seat), [`usa_wa_adapter_sos.house.build`](../../packages/usa-wa-adapter-sos/src/usa_wa_adapter_sos/house/build.py) (House Position seat), [`harvest_committee_member_spans`](../../packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/harvest_committee_member_spans.py) (committee membership). Each daily refresh re-drive passes the current, non-superseded events.

### 4. Operator CLI — `usa-wa-adapter-legislature`

`python -m usa_wa_adapter_legislature.operator_events`:

- Direct-arg mode: `--member-id --kind --reason --seat --effective-date --evidence-url` for a single event.
- Batch mode: `--file events.json` (a list of the same shape) for backfilling a biennium's known successions.
- `--supersede <event_id>` to record a correction (appends + stamps).
- `--list` / `--dry-run` for inspection.
- App-role DML (writes `operator_events` + provenance; the operator Source is not owner-gated). Shell access is the trust boundary, as with `usa_wa_api.cli.redrive`.

### 5. Invariant alerts — daily backstop

A new oneshot + timer (`usa-wa-succession-invariants`) — or folded into an existing refresh — asserting, against the live open-span cohort:

- **Chamber-count invariant** — open `state_senator` seats == 49, open `state_representative` seats == 98 (147 total). A high count (50/99) ⇒ ghost-open predecessor (missing `departed`); low (48/97) ⇒ over-closed / unfilled seat (missing `seated`).
- **Duplicate-occupancy** — no seat `(chamber, ld, position)` with two open occupants; no member with two open same-chamber seats.

On violation, email `USA_WA_ALERT_EMAIL` via the existing `alerts`/`notify-failure` gateway path, naming the offending seats/members. Exit non-zero so the `OnFailure=` handler also fires. This is both the operational drift alarm and the acceptance oracle for the #107 fix.

### 6. Degenerate-span fix (independent, land first)

The `valid_to = max(prior_end, row.valid_from)` clamp at [`span_emit.py:214`](../../packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/span_emit.py#L214) produces `valid_from == valid_to` for a fully-excluded single-biennium span. This is malformed independent of the overlay. Fix standalone: a span whose only asserted biennium was excluded closes with a distinct marker rather than a one-day window. Landable ahead of the overlay so the two changes aren't entangled; once the overlay supplies Hunt's true House window the marker is moot for that case, but the clamp fix protects every other single-biennium exclusion.

## Data flow

```
operator (news) ──> CLI (args | --file) ──> [FetchEvent+RawPayload append]  (provenance, hashed)
                                        └──> [operator_events upsert]        (queryable overlay)
                                                     │
daily refresh: wire ──> build_tenure_spans ──> apply_operator_events(spans, events) ──> emit_spans ──> Assignments
                                                     │                                      │
                                        (close predecessor / open successor,      (Citation -> operator attestation)
                                         override #105 hygiene)
                                                     │
daily invariants oneshot ──> open-cohort counts ──> chamber-count + duplicate-occupancy ──> email on drift
```

## Error handling & edge cases

- **Correction after the wire catches up** — once a future biennium's wire naturally closes the span at the boundary, the operator event still applies its (earlier, correct) date; the overlay's date wins because it re-applies every build. No conflict — the event is permanent historical truth.
- **Unknown member_id** — CLI validates the member resolves to a `Person`; refuses to write an event for a nonexistent member (a typo would otherwise be a silent no-op overlay).
- **`seated` with no wire span and an un-ingested seat** — synthesize the span from the descriptor; if the seat Role can't be resolved, `emit_spans` already logs `span_role_unresolved` and skips (never guessed).
- **Overlay + mass-close guard** — the overlay closes spans by exact member/seat, not in bulk, so `close_stale_spans`' mass-close abort is unaffected; the invariant alert (not the sweep) is the safety net for a bad batch.
- **Two events same member/seat/date** — idempotent on `source_id`; a genuine correction uses `--supersede`.

## Testing

- **Model/migration** — table + grants; `superseded_by_id` self-FK.
- **Provenance** — a CLI write appends exactly one `FetchEvent`+`RawPayload`, `content_hash` matches, integrity sweep passes; a `--supersede` appends a second and stamps the first.
- **Overlay (pure, table-driven)** — the three LD5 rows are the golden fixtures: Ramos `departed 2025-04-19` closes seat+party at that date; Hunt `seated chamber-senate:5 2025-06-03` starts Senate there and re-instates House `floor → 2025-06-03` (no degenerate span); a member with no events is untouched.
- **Builder integration** — each of the three builders applies the overlay and cites the attestation.
- **Invariants** — 49/98 passes; a synthetic ghost-open drives 50/98 → non-zero exit + alert; a duplicate seat occupancy is caught.
- **Degenerate-span fix** — a fully-excluded single-biennium span no longer emits `valid_from == valid_to`.

## Acceptance (from #107)

- Ramos Senate + party spans end `2025-04-19`; Hunt Senate starts `2025-06-03`; Hunt House span `2025-01-01 → 2025-06-03` (biennium floor start retained — sub-session swearing-in precision is out of scope); open counts read 49 / 98 / 147.
- The invariant oneshot exits 0 on the corrected cohort and emails on a re-introduced ghost.

## Out of scope / documented residuals

- **Sub-session start precision** (Hunt's real 2025-01-12 swearing-in vs the 2025-01-01 biennium floor) — a general coarseness affecting *every* member, not succession-specific. Deferred.
- **Raw span-override escape hatch** — deferred until an event the `departed`/`seated` vocabulary can't express appears.
- **External news/roster feed cross-check** (proactive detection) — separate project; the invariant alerts are the chosen backstop.
- **#106 odd-year ballot evidence** — orthogonal; supplies the successor's *elected-vs-appointed* citation, not succession dates. Tracked separately.
