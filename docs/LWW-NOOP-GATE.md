# The LWW no-op gate

**Read this before adding a `write_enabled` producer descriptor**, or when a cohort's
outbox volume goes flat-but-nonzero forever. It is the named pattern behind #102
(assignment), #104 (person), and #109 (role) — three reactive rediscoveries of one defect.

## The defect

`apply_record`'s local-newer branch keeps the local row and enqueues an UPDATE to push
our version up to PM. That is correct when the local row genuinely changed. It is a
**permanent write loop** when it didn't:

1. A local write (a span rebuild, a roster rebuild, a backfill) bumps `updated_at`
   without preserving PM's clock — the #65 lesson, re-armed per cohort.
2. The `anchored_cohort` reconcile re-fetches the row, sees local newer, enqueues.
3. PM receives an observation identical to what it already holds and **no-ops it —
   without advancing its own `updated_at`**.
4. The skew is therefore never resolved. Go to 2, forever.

Nothing self-corrects, because the thing that would fix the skew (PM's clock moving)
is exactly what an identical observation fails to cause. Observed cost: 26,990
assignment deliveries in 10 days (#102, which 429'd PM), ~932 persons/day (#104),
610 roles/day (#109).

### Where step 1's skew came from — the systemic re-arm

Step 1 was assumed to be a *bulk-backfill* accident, which is why the gate was added
reactively per cohort. It was not only that. **Every row we created was born skewed**,
by the delivery itself: `_deliver` stamps the PM anchor with `set_anchor`, a plain
attribute write, and the flush that persisted it pushed `updated_at` to `now()` —
landing the local row ahead of PM's own creation clock by exactly the POST round-trip.
The chronic org row of the #109 audit sat **228ms** ahead of PM for 11 days on nothing
else. That is why a cohort could go quiet for weeks and then churn forever after one
create.

Anchor stamping now goes through `SyncEngine._stamp_anchor`, which preserves the row's
clock, so a new row lands *older* than PM → the next reconcile takes the PM-wins branch,
mirrors, adopts PM's clock → parity. Self-correcting instead of self-arming. **Route every
new anchor-stamp site through that helper** — the first cut fixed only `_deliver` and left
the sweep's fallback stamp re-arming the identical defect.

**The SQLAlchemy trap in that fix**: `updated_at` carries an `onupdate` callable, which
SQLAlchemy applies to any UPDATE whose SET clause omits the column — and assigning a
value *equal to the loaded one* registers no net attribute change, so the column drops
out of the SET clause and the onupdate overwrites the stamp with `now()`. A "preserve
this clock" write is by definition a no-change write, so it silently did nothing.
`set_last_updated` therefore `flag_modified`s the column. **A test asserting the value
right after the assignment passes even with the bug** — the divergence only appears
after the flush, so any regression guard here must flush.

**And the trap inside *that* fix**: force-flagging on every call made `_adopt_remote_clock`
— which runs for every record of every reconcile — emit a no-op UPDATE per already-converged
row (~12.7k/day; a forced 519-row production reconcile now moves `n_tup_upd` by 0). It skips
at parity. Equality is the right test *because* `upsert_from_pm` flushes before returning:
a row PM actually changed has already had its clock bumped to `now()` by the `onupdate`, so
it no longer matches and is stamped. Parity therefore means "converged and untouched".

## The precondition

```
reconcile_mode == "anchored_cohort"  ∧  write_enabled == True  ∧  no gate
```

A `read_source="feed"`-only descriptor is immune (no periodic re-fetch to re-trigger
the branch). A read-only descriptor is immune. Everything else is a candidate.

## Auditing a cohort

Empirical, from the delivery ledger — steady-state volume, then day-over-day cohort
overlap. Flat volume with ~100% overlap and 0 new rows is the signature:

```sql
select entity_type, date_trunc('day',updated_at)::date d, count(*)
from sync.powermap_outbox where updated_at > now()-interval '7 days'
group by 1,2 order by 2 desc,1;

with a as (select distinct local_id from sync.powermap_outbox
           where entity_type=:t and op='UPDATE' and updated_at::date=:day1),
     b as (select distinct local_id from sync.powermap_outbox
           where entity_type=:t and op='UPDATE' and updated_at::date=:day2)
select (select count(*) from a), (select count(*) from b),
       (select count(*) from a join b using(local_id)) overlap;
```

A cohort with ~0 steady-state enqueues **needs no gate** — org sat at 2/day at the #109
audit and was deliberately left ungated rather than grow a five-surface comparator.

## The contract

Two pieces, both on the descriptor:

```python
local_newer_noop_gate = True          # opt in

def observation_matches_record(self, observation: dict, record: dict) -> bool:
    """Would re-producing this observation leave PM's record unchanged?"""
```

`EntityDescriptor.local_newer_is_noop` is the **template** and should not be overridden:
it short-circuits on the opt-in flag (so ungated cohorts never build an observation),
guards on `dependencies_ready`, builds the observation, and delegates the verdict to the
pure comparator. `apply_record` then adopts PM's clock (`_adopt_remote_clock`) instead of
enqueuing.

Compare only the **mutable** surface. Whatever forms PM's match key for the entity is
immutable for an anchored row and either needs no comparison or *is* the comparison:

| cohort | match key | compared surface |
|---|---|---|
| assignment (#102) | `(person, role, start_date)` | `is_current`, `start_date`, `end_date` |
| person (#104) | `(source, source_id)` identifier | `display_name` proxy, `additional_identifiers` |
| role — seat (#109) | `(org, role_type, jurisdiction, qualifier)` | the tuple itself |
| role — title (#109) | `(org, title)` | title + `role_type` classifier |

## Hazards

**A false `True` erases; it does not defer.** The resolution is to adopt PM's clock, which
*drops* the pending local change rather than delaying it. A comparator must return `False`
on any surface it cannot positively confirm PM already reflects. Compare narrowly, err
toward enqueuing. This is why a wide, weakly-verified comparator (the org case) is worse
than no gate at all on a cohort that isn't actually churning.

**Leaving a cohort ungated is detected, not silent (#112).** The non-convergence backstop
counts consecutive identical `auto-attached` re-sends per row and, past a threshold,
surfaces the row in the cycle summary and emails on a rise. So the cost of *declining* to
gate is a bounded, operator-visible signal — whereas the cost of a false `True` is an
erased change with no signal at all. That asymmetry is the argument for erring toward
no-gate on a surface you cannot verify narrowly, and it is why the deliberately-ungated
org cohort is acceptable. The gates and the backstop are complementary: the gates converge
the clean clock-skew cases instantly; the backstop catches what they *cannot* — a genuine
local↔PM diff PM refuses to apply (the #110 role classifier), which no clock comparison
can see.

**The deps guard is not optional** — it is in the template precisely so no new descriptor
has to remember it. Without it, a row whose PM prerequisites are unmet builds a garbage
observation (`organization_id="None"`) that can compare equal by accident, or raises
mid-reconcile on the hot path.

**A divergent match key is a real change, not a safe skip.** Role's seat observation *is*
its match key, which tempts a blanket `True`; but a drifted tuple would resolve to a
different seat (or mint one), so it must still enqueue.

**An absent key is not an asserted NULL.** A comparator must require the key's *presence*
wherever the observation's value can legitimately be `None` (role's Senate-seat
`qualifier`), or a record that simply omits it compares equal and yields a false no-op.
Comparators assume a full detail record — both live paths fetch one, and only the
`full_list` reconcile yields list-shaped records — but requiring the key keeps that
coupling from becoming a silent trap if a gated descriptor ever switches modes.

## Convergence

An `anchored_cohort` gate self-converges: the first post-deploy reconcile adopts PM's
clock on each skewed row. A **heal CLI is only warranted for a large pre-existing
backlog** — #102's ~4,300 rows got `heal_assignment_clocks`; #104 (~434) and #109 (305)
did not need one.

## Sibling: the rejected-UPDATE replay guard (#132)

The no-op gate is not the local-newer branch's only suppressor. After the gate stands
down (or for an ungated descriptor), `apply_record` consults
`_rejected_identical_update` before enqueuing: when the row's **latest** outbox entry
is a REJECTED UPDATE whose refused-payload hash (stamped by `_reject` at the
rejection, #132) equals the row's current `to_observation` hash, the re-enqueue is
skipped — a persistent 422 (e.g. PM's `chk_no_org_cycle`) would otherwise mint a
fresh REJECTED entry every reconcile and fire the #85 rise email each cycle.

The two suppressors resolve **differently**, and the difference is the point:

- **No-op gate** (spurious clock skew, no real diff) → *adopt PM's clock*. The skew
  is the defect; parity ends the loop.
- **Replay guard** (real diff, PM refuses it) → *skip the enqueue only*. The pending
  change is real, so the clock is deliberately left ahead; any payload change (the
  data fix) re-arms and the corrected UPDATE re-sends. Suppression logs
  `update_reject_replay_suppressed` WARNING once per row, INFO thereafter (the #112
  throttle shape). Unready dependencies stand the guard down (the same
  `dependencies_ready` hoist as the gate template).

A false no-op **erases** a pending change; a false replay-skip merely defers one —
which is why the guard demands hash-exact identity while the gate demands a
descriptor-authored comparator.

## The inverse: the branch that never fires (#247)

Everything above suppresses an *over-eager* local-newer branch. #247 is the same branch
failing to run at all, and it is the more dangerous shape because its signature is
silence rather than volume.

The `anchored_cohort` reconcile is the **only** path that pushes a local change on an
already-anchored row: `sweep_unanchored` enqueues only rows whose anchor is NULL, and the
changes feed carries what PM changed, never what we did. Since #160 that reconcile sends a
stored `ETag` and short-circuits the row on a `304` — before `apply_record`, and therefore
before the local-newer branch. A `304` answers "has PM changed?", which is the right
question for PM→local (nothing to heal) and the wrong one for local→PM: a local-only edit
leaves PM untouched, so PM `304`s precisely the rows that most need pushing.

This degrades **as the ETag cache warms**, not on deploy. Outbound assignment UPDATEs were
still flowing a fortnight after #160 shipped; once coverage approached 100% the push half
stopped. And it is invisible to every health signal the engine has — zero pending, zero
rejected, zero non-converging, zero re-anchors, `replay_healed` 0 — because all of them
count work already noticed. **A cohort that has stopped propagating and one fully converged
score identically on all of them.** 397 corrected assignment spans (the #226 roster
succession dates) sat locally for six clean-looking cycles.

The fix keeps the `304` and changes what is sent: `ConditionalGetState.row_updated_at`
records the local row's clock as of the fetch that stored the validator, and the reconcile
withholds the validator when the row's clock has advanced past it. The forced full body puts
`apply_record` back on the path with every guard above intact. Two properties matter:

- **Same-clock comparison.** Both readings are the row's own `updated_at`, never PM's.
  Comparing across the two clocks (e.g. against the store row's own `updated_at`) would
  have been free, and a PM server running seconds ahead would then force a full fetch on
  every row forever — silently undoing #160 in the other direction.
- **Self-limiting.** The forced fetch re-stamps the watermark, so a local change costs one
  full GET, not one per cycle until it drains. A NULL watermark (a validator stored before
  #247) reads as advanced — verify rather than trust — which costs the cohort one full pass,
  once.

`local_newer_forced` on the sidecar cycle summary is the observable this lacked: the count
of rows found holding a change PM has not seen. A large one-off after a backfill is healthy;
**the same number every cycle means the push is wedged** — the sustained-nonzero reading is
the alert, not the spike.

The carry-drift hatch (`maybe_enqueue_enrich_drift_only`, which already fired on the `304`
path) is *not* subsumed by this: a newly-added carry field drifts the enrich payload without
touching any row's clock, so those rows still `304` and still need it.

## Related

#65 (clock preservation on import — the root cause, plus `heal_committee_curation`),
#102 (assignment gate + heal), #104 (person gate), #109 (audit, role gate, this note),
#85 (`POWERMAP_MIN_REQUEST_INTERVAL` — the backstop that keeps churn from 429ing PM),
#112 (the non-convergence backstop — the *detection* counterpart: what the gates cannot
catch, because it is a real diff PM refuses rather than a clock skew),
#132 (the rejected-UPDATE replay guard — see the Sibling section above),
#160 (conditional GET — whose `304` short-circuit is what #247 fell through),
#247 (the local-newer branch failing to fire — see the Inverse section above).
