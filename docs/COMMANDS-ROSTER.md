# Commands — the roster PDF source

Split out of [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md); the index lives in
[COMMANDS.md](COMMANDS.md). The Legislature's own roster PDF (#225, epic #219): the Phase A
harvest and its monthly edition re-check (#237), the #226 succession backfill, and the #228
pre-1991 build. Module reference:
[MODULES-LEGISLATURE-ROSTER.md](MODULES-LEGISLATURE-ROSTER.md).

## Roster PDF — the archival member source (#225, epic #219)

The Legislature's own *Members of the Legislature 1889-2025* roster. **Not a sweep and not a
refresh**: the source publishes one document per revision (~biennially; 18 editions since 1962),
so the harvest archives exactly one resource and re-running is a cache hit.

```bash
# Phase A — archive one edition (archive-only, #54 hashed)
uv run python -m usa_wa_adapter_legislature.roster_pdf.harvest --revision 2025-06-05
```

`--force` re-fetches past the freshness cache; `--dry-run` rolls back; `--pause-seconds` sets the
`leg.wa.gov` courtesy limiter for the run (unset leaves `USA_WA_LEG_MIN_REQUEST_INTERVAL`, default
1.0s, in force — #236). Exit `0` clean · `1` failed · `2` config · **`4` degraded** — the document
could not be located, meaning the CMS media key rotated *and* the href could not be re-discovered,
so an operator must re-point the source.

Re-checked **monthly** by `usa-wa-roster-pdf-recheck.timer` (#237, 1st 09:00 UTC) — never in
the daily refresh. Closed history does not drift, and the edition lags the current biennium by
design, so it is never authority there. Phase B parses **offline** from the archive: revise the
parser and re-run without re-fetching 5.7MB.

The timer runs this same harvest `--dry-run --force`: one GET (~69MB/yr), the stamp verified
against `DEFAULT_REVISION` in `roster_pdf/harvest.py`, the archive write rolled back. `--force`
is load-bearing — the source's 90-day freshness cache would otherwise make the check a cache hit
that never fetches. When its `OnFailure=` email arrives, the summary line says which exit 4:

- **`mismatch=…`** — a new edition is published. Archive it with the `--revision` the message
  names, then bump `DEFAULT_REVISION` on `main`: the check compares against the code, so it
  alerts every month until both land. Audit the new edition before building on it (the
  `coverage.py` claim is closed at the old ceiling, and the parser has only seen this layout).
  **The harvest archives to Postgres only**: the #302 pipeline stages the roster from the raw
  store, which nothing writes on a schedule, so the published datasets stay on the old edition
  until #421 lands.
- **`unavailable=true`** — the media key rotated and the href could not be re-discovered from
  the index page; re-point `DEFAULT_ROSTER_URL` in `roster_pdf/transport.py`.

The check compares the **stamp**, not the bytes: a re-upload that keeps the `Revision Date`
stays green. Exit `1` is an outage (a non-404 status, a timeout); the next month's run retries.
Re-check by hand with `sudo systemctl start usa-wa-roster-pdf-recheck.service`.

```bash
# Succession backfill (#226) — the roster's mid-term dates → operator events.
# Reads the archive offline; the only network cost is the sponsor binding's one-time WSDL load.
uv run python -m usa_wa_adapter_legislature.roster_pdf.backfill --dry-run
```

**Run it sidecar-paused.** Every event written moves a span boundary on the next builder
re-drive, which re-anchors the corresponding PM Assignment — the same sequencing the #101
House builder documents: pause the sidecar, run this, re-drive the span builders, resume.
Do not merge and let the timer run.

**Re-drive means four builders, and the pre-1991 one is the point.** Until #226 the overlay
was applied only by `sponsors.build`, the SOS House builder and the committee builder — none
of which owns a span below the 1991 floor. The roster's own boundaries are *all* pre-1991, so
re-driving those three leaves every one of them correct, provenanced and **inert**. The
pre-1991 builder applies the overlay since #226 and must be in the sequence:

```bash
uv run python -m usa_wa_adapter_legislature.roster_pdf.build      # pre-1991 — the roster's own era
uv run python -m usa_wa_adapter_legislature.sponsors.build        # 1991+ and the deepened joins
uv run python -m usa_wa_adapter_legislature.operators.invariants --sweep-biennia --strict
```

Two things to read off the run rather than assume:

* `operator_cites`, not `operator_events_loaded`, is the **applied** count. The loaded figure
  counts what was read for this cohort; the overlay silently skips a seat-scoped event whose
  kind the builder does not own (a House `vacated` is #229's) and no-ops a `departed` with no
  open span. A citation is written only where a boundary really moved.
* A `departed` closes **every** covering span of the member, and `build_tenure_spans` merges
  contiguous biennia into one. A member who resigned and was returned two years later has a
  single merged span, and the event truncates the whole thing — dropping real later service.
  `departed:resigned` is the roster's largest class (205 proposals) and pre-1991 is where
  resign-and-return is both most common and least correctable from the wire, so after the
  re-drive confirm that no span the overlay closed has a roster observation in a biennium
  after its new `valid_to`.

`--limit N` stages a first run; `--dry-run` rolls back (the harness owns the rollback, so the
counters are exactly what a live run would do). Exit `0` clean · `1` failed · `2` config ·
**`4` degraded** — nothing resolved at all, meaning the roster archive or the sponsor index is
missing rather than that there was no work.

It **defers to the operator on every overlap**. An already-attested boundary is always skipped
(writing it would replace the existing `entered_by`/evidence URL with the machine's, and there
is nothing to correct); one that *disagrees* on the same tenure is logged as
`roster_backfill_attestation_conflict` with both dates and `delta_days`.

```bash
# Let the roster replace a *machine*-entered disagreement. Never touches a named operator's row.
uv run python -m usa_wa_adapter_legislature.roster_pdf.backfill --supersede-conflicts
```

## Pre-1991 build (#228) — roster Persons, party spans, Senate seat spans

The Phase B write side of the pre-1991 identity design
([spec](specs/2026-08-20-pre-1991-identity-design.md)): archive → identities → the
acceptance oracle → mint → emit → retire → deepen, in one gated pass. Verified against
production (rolled back) 2026-08-21: 6,217 pre-1991 records → **2,494 Persons minted, 3,627
roster-sourced Assignments, 933 spans through the deepened sponsor build**; refusals
`wide_gap: 14`, declines 2 (the power-map#442 adjudications), uncovered rows 9, seat
overlaps 122, `spans_retired: 0` / `spans_retired_anchored: 0` / `spans_closed: 0` on a
first run (nothing to strand yet, and every shallow row the deepening supersedes is already
closed — measured: zero open duplicate `(person, role)` pairs before or after). Every tally lands in `counters`, so the
#178 job ledger holds the residue, not just the completion log.

**PM prerequisite — do this first.** The roster mints Persons under a *new* source
(`usa_wa_legislature_roster`), and PM requires a registered `identifier_type` for every
person observation. `person_wa_legislature_roster` must exist in PM
(`/admin/settings/identifier-types/`, power-map#456) **before** the sidecar produces, or the
whole cohort 422s — which is exactly what happened on the first run here (294 rejected before
the sidecar was stopped). Since #255 an unmapped source *defers* instead of rejecting, so the
failure mode is now a stalled queue rather than a rejection pile — but the type still has to
exist before anything reaches PM.

**The general rule:** a new Person-minting source has a PM dependency. Register its
identifier type, add it to `SOURCE_TO_IDENTIFIER_TYPE`, *then* produce.

```bash
# (Ran SIDECAR-PAUSED until #314 — deepening re-keys spans and the migrate moved PM anchors.
# The sidecar is gone; no pause step remains.)

# 1. Build (app role). Oracle violations abort with exit 1 before any write.
uv run python -m usa_wa_adapter_legislature.roster_pdf.build --dry-run
uv run python -m usa_wa_adapter_legislature.roster_pdf.build

# 2. PREVIEW the collapse and read `anchors_dropped` (#276). OWNER role, like step 4: the job
#    declares role="owner" and resolves DATABASE_URL_OWNER itself, so just load the env — a
#    per-command DATABASE_URL=... prefix is silently ignored here (see COMMANDS.md § Database migrations).
#    The dry-run rolls back, and while the sidecar stays paused nothing moves the anchors
#    underneath it, so its counters are what step 4 will do. Each drop is warned individually
#    as `sponsor_span_migrate_anchor_dropped`, carrying the `source_id` step 3 needs. Only
#    trust it once step 1 exited 0 — see below.
uv run python -m usa_wa_adapter_legislature.sponsors.migrate_spans --dry-run

# 3. RETIRED at #314 with `retract_assignments`. It retracted every source_id the preview
#    would drop, before the collapse rather than after (#276), so a dropped anchor never
#    became a live PM assignment nothing local could name. With the sidecar gone nothing
#    writes to PM at all, so anchors_dropped is now a local bookkeeping number: the PM rows
#    it names are PM's to reconcile from the published datasets.

# 4. Collapse the stranded shallow keys (OWNER role — deletes citations, #54). Deepening
#    re-keys a joined member's span to its roster-era start, stranding the shipped
#    1991-start row; the #97 collapse transfers its PM anchor onto the deepened span.
#    Measured: superseded_found=130, anchors_transferred=130, orphans=0, anchors_dropped=0.
uv run python -m usa_wa_adapter_legislature.sponsors.migrate_spans

# 5. RETIRED at #314. This resumed the sidecar so the outbox drained the new Persons +
#    Assignments to PM. PM now picks them up from the next nightly dataset publish.
```

**Why steps 2–3 come before the collapse (#276).** The collapse is a one-way door for the rows
whose anchors it drops. `_retire_onto` hard-deletes the stranded row *unconditionally* —
including on the branch where the keeper already carries a different anchor, so the row and its
only local handle disappear in the same pass that orphans the PM assignment.
`retract_assignments` resolved its targets by the **local** natural key `(source,
source_id)`, and `--source-id` was its only addressing mode — there was no
`--pm-assignment-id` door. So after the collapse a dropped anchor was a live PM assignment
nothing local could name, and the only remaining route was PM's admin-only unarchive.

**#314 dissolved the hazard rather than fixing it.** With no producer path to PM, the
collapse can no longer orphan anything on PM's side: PM reconciles from the published
datasets, where a collapsed row is simply absent. The ordering above is kept as the record
of why the constraint existed, not as a step to run.

The recorded `anchors_transferred=130, orphans=0, anchors_dropped=0` is the *clean* reading, not
a guarantee the collapse always reaches it. `anchors_dropped` rises whenever the sidecar drained
a deepened span before the collapse ran: PM keys assignments on `(person, role, start_date)`, so
the deepened span is minted as its **own** assignment (disposition `new`) and the stranded row's
anchor has nowhere to transfer. That is the hazard the sidecar pause at the top of this sequence
exists to prevent; steps 2–3 are the backstop for when it was not observed.

**The roster cohort is a standing input to every unrestricted sponsor build** (#228,
`roster_pdf/deepening.py`): a full rebuild that omitted it would re-assert the shallow
1991-start keys and recreate the stranded rows the collapse retires. The daily restricted
path never derives it (its cohort is all post-1991), so the timers are unaffected. Exit
codes: `0` clean · `1` failed (incl. an oracle violation) · `2` config · `4` degraded — no
archive, **or a sweep guard tripped**. A degraded exit on a guard means stranded rows are
still in place: read `sweep_aborted` / `retire_aborted` in the ledger counters and resolve
**before step 2**, or the collapse runs against rows the sweep never touched. Step 2, not step
4: a guard trip leaves rows the sweep should have retired, so the preview's `anchors_dropped`
set is drawn from the wrong population — and step 3 acts on it with a *terminal* retraction.
`anchors_dropped` is only meaningful once step 1 has exited `0`.

Re-runs are idempotent — Persons and Assignments upsert on natural keys, a display name
that changed since the last run is refreshed (`persons_renamed`), and a span under a key
this derivation no longer produces (an identity alias merged two folds, a parser fix moved
a group's first session year) is soft-deleted (`spans_retired`).

A stranded row **carrying a PM anchor is left alive**, counted as `spans_retired_anchored`,
and degrades the exit: retiring it would orphan the PM assignment for good, since both
`sponsors.migrate_spans` and `retract_assignments` filter `deleted_at IS NULL`. The sequence
is therefore build → step 4's collapse (which transfers the anchor onto the successor span)
→ **build again** (which retires the now-unanchored row and exits `0`). That trailing rebuild
is a *conditional* step, not part of the numbered sequence above: it is needed only when a run
reports `spans_retired_anchored > 0` and degrades the exit.

`--supersede-conflicts` is off by default: the safe reading of a disagreement is that someone
knew something the roster does not. It was overridden once, on evidence — all 17 live conflicts
were agent-entered rows citing Wikipedia/Ballotpedia, and **5 of the 9 conflicting departures
had been dated to the successor's seating date**, collapsing "incumbent departed" and
"successor seated" into one date and asserting a zero-day vacancy where 1–29 days actually
elapsed. Superseding appends the correction and stamps `superseded_by_id`; nothing is mutated,
so the retracted attestation stays auditable.

Since #363 a conflict is not only a date disagreement on one tenure. A `vacated` also
contradicts a live person-scoped `departed` for the same member in the same biennium — the
member either left the legislature or moved seats within it, and a stale `departed` beside a
fresh `vacated` closes every span the move preserves — so `--supersede-conflicts` retires that
too, **reclassifying** rather than re-dating (`written=2 superseded=2` on 2026-09-10: Stanford
and Chapman). It never runs the other way: a stale `vacated` beside a fresh `departed` is
redundant, not destructive.

Measured on the 2025-06-05 edition: **155 written, 17 superseded, 81 already attested**.
