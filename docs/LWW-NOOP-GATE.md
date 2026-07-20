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

`_deliver` now preserves the row's clock across the anchor stamp, so a new row lands
*older* than PM → the next reconcile takes the PM-wins branch, mirrors, adopts PM's
clock → parity. Self-correcting instead of self-arming.

**The SQLAlchemy trap in that fix**: `updated_at` carries an `onupdate` callable, which
SQLAlchemy applies to any UPDATE whose SET clause omits the column — and assigning a
value *equal to the loaded one* registers no net attribute change, so the column drops
out of the SET clause and the onupdate overwrites the stamp with `now()`. A "preserve
this clock" write is by definition a no-change write, so it silently did nothing.
`set_last_updated` therefore `flag_modified`s the column. **A test asserting the value
right after the assignment passes even with the bug** — the divergence only appears
after the flush, so any regression guard here must flush.

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

**The deps guard is not optional** — it is in the template precisely so no new descriptor
has to remember it. Without it, a row whose PM prerequisites are unmet builds a garbage
observation (`organization_id="None"`) that can compare equal by accident, or raises
mid-reconcile on the hot path.

**A divergent match key is a real change, not a safe skip.** Role's seat observation *is*
its match key, which tempts a blanket `True`; but a drifted tuple would resolve to a
different seat (or mint one), so it must still enqueue.

## Convergence

An `anchored_cohort` gate self-converges: the first post-deploy reconcile adopts PM's
clock on each skewed row. A **heal CLI is only warranted for a large pre-existing
backlog** — #102's ~4,300 rows got `heal_assignment_clocks`; #104 (~434) and #109 (305)
did not need one.

## Related

#65 (clock preservation on import — the root cause, plus `heal_committee_curation`),
#102 (assignment gate + heal), #104 (person gate), #109 (audit, role gate, this note),
#85 (`POWERMAP_MIN_REQUEST_INTERVAL` — the backstop that keeps churn from 429ing PM).
