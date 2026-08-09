# Commands — succession, corroboration, and committee lineage

Split out of [COMMANDS.md](COMMANDS.md), which is where the index lives.

## Senate odd-year corroboration (#123)

The odd-year November general seats senators mid-biennium by special (Hunt, LD5, Nov 2025). The
even seating year's winners are already dated by the WSL sponsor roster, so only the **odd** cohort
is consumed here. The Senate seat is WSL-sponsor-built (`usa_wa_legislature`); SOS only consumes its
ballot evidence, so this lives SOS-side (SOS→legislature, never the reverse). Two consumers:

- **2a citation** — an elected senator's open span `valid_from` is field-cited to the odd wire
  (`sos-legresults:<odd>`): attestation of the *elected* status the operator-dated (appointed)
  boundary lacked. The Nov win does **not** move the boundary — tenure is continuous.
- **2b corroboration** — an odd-year winner with **no open `state_senator` seat** at that LD is a
  silent **missing operator `seated` event** (the failure mode the chamber-count gate only catches
  after the count has already drifted). Named + exit 1 → operator email.

Runs daily 07:00 UTC (`usa-wa-senate-corroboration.timer`), after the WSL + SOS refreshes rebuild
the open Senate cohort and archive the odd results wire. App-role DML (the citation is an idempotent
`Citation` insert; the corroboration is read-only). Exit 0 clean / 1 on a missing winner / 2 config.

```bash
# Daily gate (also the ad-hoc invocation); --dry-run builds citations then rolls back.
python -m usa_wa_facts_seats.senate_corroboration
python -m usa_wa_facts_seats.senate_corroboration --dry-run
python -m usa_wa_facts_seats.senate_corroboration --biennium 2025-26   # pin a non-current biennium
```

## House odd-year special-winner corroboration (#149)

The **House sibling** of `senate_corroboration` 2b. A House odd-year **special** winner who never
materializes into a `state_representative` Position seat was caught by nothing — the LD30 Pos 2
2015-16 / Teri Hickel case: she won the Nov 2015 special, the odd cohort was archived and named her,
she was rostered, yet she sat *unseated* for months because the backfill hadn't been run and the
daily refresh runs `restrict_to_biennium=current` (never re-emits a historical biennium). A **unit**
guard (#148) covers the odd-merge code path but cannot detect an *operational* gap (a backfill that
wasn't run). This makes it loud.

`corroborate_house_winners` consumes the odd-year `house_winners()` cohort (winners-only — a *loser*
candidacy must never false-match) and asserts every `(LD, position)` a special decided has an open
seat. Two differences from the Senate check: keyed on **`(LD, position)`** not LD (two seats/LD),
and **read-only** — no 2a citation half, since the House Position spans already cite the odd wire
(`house/build.py`'s `special_events`). Gate on seat **existence**, not identity: a wholly unoccupied
winner seat is the missing `seated` (exit 1); a seat held by someone other than the ballot winner is
`mismatched` (surfaced, not gated — a surname divergence is usually a legitimate name change).

Runs daily 07:05 UTC (`usa-wa-house-corroboration.timer`), after the WSL + SOS refreshes rebuild the
open House Position cohort and archive the odd results wire, beside Senate corroboration (07:00) and
before the succession invariants (07:15). Read-only (app role). Exit 0 clean / 1 on a missing winner
seat / 2 config.

```bash
# Daily gate (also the ad-hoc invocation).
python -m usa_wa_facts_seats.house_corroboration
python -m usa_wa_facts_seats.house_corroboration --biennium 2025-26   # pin a non-current biennium

# Historical audit (#119 report-only pattern): every archived odd year vs the point-in-time
# occupancy that covered it — the LD30-as-history regression the current-biennium daily gate can't
# reach. Exit 0 unless --strict (the post-backfill regression guard). House Position coverage floors
# at 2003-04, so pre-coverage odd years under-report (reported, not gated).
python -m usa_wa_facts_seats.house_corroboration --sweep-biennia
python -m usa_wa_facts_seats.house_corroboration --sweep-biennia --strict
```

## Operator succession (#107)

Mid-biennium successions (death, resignation, appointment) are invisible to every
wire signal — the cumulative WSL wire keeps a departed member named + committee-listed,
so their tenure span stays ghost-open, and an appointee's span starts at the biennium
floor, not the appointment date. Operators know these facts (news-first) and **interject**
them as `OperatorEvent`s. Each event is applied as an authoritative **overlay** by all
three span builders (sponsor / SOS-house / committee) after `build_tenure_spans`, before
emit; the daily refreshes re-drive the builders, so the overlay re-applies every run and
the wire can never win back a corrected span (self-durable). Provenance is first-class:
each write appends a hashed `FetchEvent` + `RawPayload` under the `usa_wa_operator` Source
(integrity-sweep covered, #54) and the touched span carries a field-level `Citation`.

```bash
# Record operator succession events (#107) — the live interjection surface. Three kinds
# split by scope so a chamber move never touches the party span:
#   departed (person-scoped, no seat) — the member stops serving entirely; every open span
#     (seat + party + committee) closes at the date. Death, full resignation, expulsion.
#   vacated  (seat-scoped) — ONE named seat's span closes at the date; party + committees
#     untouched. A chamber move's old seat, or a single-seat resignation.
#   seated   (seat-scoped) — one named seat's span opens at the date (instead of the
#     biennium floor), synthesized if the wire built none. Appointment, swearing-in.
# A chamber move = vacated(old seat) + seated(new seat) on the same member, each applied by
# the builder that owns that seat kind. seat_kind/seat_discriminator name the seat the same
# way the builders key it: chamber-senate + LD, chamber-house + ld-{n}-position-{p},
# committee + the WSL committee id. Validates kind/reason/seat shape AND that member_id
# resolves to a usa_wa_legislature Person (a typo would be a silent no-op overlay).
# App-role DML (writes operator_events + provenance); shell access is the trust boundary,
# as with the redrive CLI. Provenance is append-only — a date-correction is --supersede
# (a NEW row stamping the prior one's superseded_by_id), never a mutation (#54).
# --dry-run validates + writes, then rolls back. Exit 2 on a validation failure.
python -m usa_wa_adapter_legislature.operators.cli \
    --member-id 29091 --kind departed --reason died \
    --effective-date 2025-04-19 --evidence-url https://... --dry-run
python -m usa_wa_adapter_legislature.operators.cli \
    --member-id 35410 --kind seated --reason appointed \
    --seat-kind chamber-senate --seat-discriminator 5 \
    --effective-date 2025-06-03 --evidence-url https://...
python -m usa_wa_adapter_legislature.operators.cli --file events.json   # JSON-array batch
python -m usa_wa_adapter_legislature.operators.cli --supersede <id> \
    --member-id 35410 --kind seated --reason appointed \
    --seat-kind chamber-senate --seat-discriminator 5 \
    --effective-date 2025-06-10 --evidence-url https://...   # date-correction of <id>
python -m usa_wa_adapter_legislature.operators.cli --list               # current events

# Succession invariant check (#107) — read-only anti-drift backstop + the #107 acceptance
# oracle. A MISSING operator event is silent (a member dies, nobody records it → a ghost-open
# span inflates the chamber for up to a biennium); this oneshot makes that loud. Against the
# live open-seat cohort it asserts:
#   chamber-count — open state_senator == 49, open state_representative == 98 (147 total).
#     High (50/99) ⇒ a ghost-open predecessor (a missing departed/vacated); low (48/97) ⇒
#     an over-closed / unfilled seat (a missing seated).
#   duplicate-occupancy — no seat Role with two open occupants, and no member holding two
#     open seats in the same chamber (the "two open senators in LD5" shape).
# Read-only (app role, no writes). Exit 0 clean / 1 on any violation (the offending
# seats/members named in the succession_invariants_violation log line) — the exit 1 is what
# the OnFailure=usa-wa-notify-failure@ handler emails the operator on. Prod runs this daily
# at 07:15 UTC via usa-wa-succession-invariants.timer, AFTER the WSL 06:00 / PDC 06:30 /
# SOS 06:45 refreshes rebuild the current-biennium cohort. --expected-senate/--expected-house
# override the WA chamber constants for a redistricting count change.
python -m usa_wa_adapter_legislature.operators.invariants

# Historical duplicate-occupancy audit (#119) — the daily gate probes the OPEN cohort only, so
# a duplicate occupancy that has since CLOSED is invisible to it forever (sub-biennium
# sequential occupancy collapsed onto the shared biennium floor — both occupants dated to the
# floor because the wire can't date a mid-biennium handoff). --as-of / --sweep-biennia re-run
# BOTH duplicate halves against a point-in-time snapshot (valid_from <= D and (valid_to is null
# or valid_to >= D)) instead of is_active: seat-side (a seat with >1 occupant → named
# seat+occupants) and member-side (a member holding >1 distinct same-chamber seat, keyed on
# person_id so a name collision can't false-merge), naming every offending tuple. Ad-hoc audit,
# NOT a timer (closed history isn't actionable in the daily
# "someone died NOW" sense). Counts are reported, not gated (House Position coverage floors at
# 2003-04, so pre-2003 biennia legitimately under-count) — exits 0 unless --strict, which
# exits 1 on any duplicate (the post-backfill regression guard).
python -m usa_wa_adapter_legislature.operators.invariants --as-of 2009-01-01
python -m usa_wa_adapter_legislature.operators.invariants --sweep-biennia
python -m usa_wa_adapter_legislature.operators.invariants --sweep-biennia --strict  # CI guard
```

## Committee lineage & lifecycle (#124)

WA re-keys standing committees across eras (new WSL `Id` ~each decade), so the same
body appears as several `active=true` orgs with disparate dated names and no visible
lifecycle. Three layers restore a coherent timeline. **Objective** facts auto-derive
from the roster archive: each `Id`'s founded/dissolved window (C1a) + the `active` flag
(C1b bulk deactivation of the ~150 defunct-era backfill Ids). The **judgment** layer is
operator-attested succession links (C2) — which era-`Id` continued / split from / merged
with which — emitted to PM as `succeeded_by` / `split_from` / `merged_with` entity events
(C3). A daily **coherence** invariant (C4) + an advisory **candidate report** (C5) close
the loop. See [`docs/specs/2026-07-25-committee-lineage-lifecycle-design.md`](specs/2026-07-25-committee-lineage-lifecycle-design.md).

```bash
# C1b — one-time bulk deactivation of the defunct-era backfill (see § Reconcilers &
# validation in COMMANDS-SYNC.md;
# --all-era disables #90 live-era scoping). Pair with --max-absent-fraction 1.0.
python -m usa_wa_sync_powermap.reconcile_committee_active --all-era \
    --max-absent-fraction 1.0 --dry-run

# C2 — record an operator-attested succession link (the judgment layer). Both --subject and
# --linked are WSL committee Ids that must resolve to live usa_wa_legislature committee Orgs
# (a typo is a hard error, not a silent no-op link). App-role DML (writes
# committee_succession_events + provenance under usa_wa_operator); provenance is append-only.
# A wrong-successor / year fix is --supersede (a NEW row stamping the prior's superseded_by_id).
# On a supersede: --year sets, --clear-year clears, omitting both inherits the prior's year.
# --dry-run validates + writes, then rolls back. Exit 2 on a validation failure.
python -m usa_wa_adapter_legislature.committee_succession \
    --subject 14294 --linked 28244 --slug succeeded_by --year 2021 \
    --evidence-url https://... [--notes "renamed + re-scoped"]
python -m usa_wa_adapter_legislature.committee_succession --file links.json   # JSON-array batch
python -m usa_wa_adapter_legislature.committee_succession --supersede <id> \
    --subject 14294 --linked 31000 --slug succeeded_by --year 2022 \
    --evidence-url https://...                                    # re-link / year correction
python -m usa_wa_adapter_legislature.committee_succession --supersede <id> \
    --subject 14294 --linked 28244 --slug succeeded_by --clear-year \
    --evidence-url https://...                        # clear the year (vs omit --year = inherit)
python -m usa_wa_adapter_legislature.committee_succession --list               # current links

# C3 — emit the C1a windows + C2 links to PM as org entity events (create/refine, no-op
# gated; anchors read from the read-mirror, not a local producer row). Also RETRACTS the
# stale PM event of a superseded, unreasserted link (#127; op=retract, stamps entity_events
# .retracted_at) — a year-only correction keeps the identity and refines instead. --dry-run
# computes the diff without posting. Exit 1 if any event rejected / 2 on a global auth block.
python -m usa_wa_sync_powermap.committee_event_producer --dry-run

# C4 — daily coherence invariant (read-only anti-drift backstop): INV1 no active=false
# committee carries a live membership Assignment; INV2 the subject of a non-superseded
# succeeded_by/merged_with link is active=false (split_from exempt). Exit 1 on any violation
# → the OnFailure=usa-wa-notify-failure@ handler emails the operator. Prod runs it daily at
# 07:30 UTC via usa-wa-committee-lineage-invariants.timer, AFTER the refreshes + reconcile
# have deactivated defunct committees + closed their spans (else it pages on pre-existing drift).
python -m usa_wa_adapter_legislature.committee_lineage_invariants

# C5 — advisory candidate report (read-only; suggests which era-Id pairs to attest via C2).
# Ranks same-chamber name-similar pairs by name Jaccard + adjacent windows + shared members.
# Nothing is written — ground truth stays with the operator.
python -m usa_wa_adapter_legislature.committee_lineage_suggest
```
