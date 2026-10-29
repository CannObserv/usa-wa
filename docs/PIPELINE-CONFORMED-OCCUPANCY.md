# The conformed tier — seat occupancy and clipping

How conformed `assignments` keeps one seat to its lawful holders (#359, #360,
#362, #501): the occupancy predicate and its two tiers, the capacity it reads,
the named list of adjudicated residue, the counterpart clipping that drains the
gate, and the roster annotation windows that date what the clip reads. Split out
of [`PIPELINE-CONFORMED.md`](PIPELINE-CONFORMED.md) when #501 grew it past its
budget; the boundary basis and interval it rests on are
[`ONTOLOGY.md` § 2](ONTOLOGY.md#boundary-basis-and-interval-369-360-increment-2-501-d6).

## Seat occupancy and clipping (#359, #360, #501)

**The predicate** (`dbt/macros/seat_overlap_pairs.sql`, shared with power-map,
#501 D7): at any instant, a seat holds no more **principals** than the
`role_periods` period covering it allows. Intervals are half-open, so a span
ending the day its successor begins is a handoff, not an overlap. The count is
checked at each principal's start and each period's start (capacity can drop
with no one starting). Party and committee roles have no limit; a seat with no
covering period is read as holding one. `membership_type` keeps a military
substitute — Wyss, LD-6, two days in 2005 under Brad Benson on leave — from
counting as a second holder. The three substitutes (Kreidler 1991, Wyss 2005,
M. Braun 2017) are a curated table, `conformed.memberships.SUBSTITUTE_TENURES`,
applied after the windows in `build_families`; the `assignments` model refuses an
entry no built span carries, and `assignments_substitutes_nested` requires each to
sit, dated on both edges, inside a principal's tenure on its seat.

**Never-seated rows** (#546): three senators the roster lists never sat — Couplin
(LD-9 1921, resigned without serving), Westman (LD-39 1941, refused by the
Senate), Mulligan (LD-39 1943, appointed, did not serve). A second curated table,
`conformed.never_seated.NEVER_SEATED`, names each row's seat and party span, and
`build_families` removes them last, before the clip. **Spans, never rows:** the
projector reads every row first, and Mulligan's is the only interlude that keeps
Reardon's 1943 and 1945 terms apart (#517). The persons stay, with no tenure. An
entry that removes less than it names fails the `assignments` model.

**Two tiers, from the boundary intervals** (#501 D6):

| Tier | Interval | Test | Severity |
|---|---|---|---|
| certain | `[valid_from_latest, valid_to_earliest)` | `assignments_seat_occupancy` | `error`, except the named list |
| may | `[valid_from, valid_to)` | `assignments_seat_occupancy_may` | `warn`, informational |

A one-biennium undated succession may overlap but never certainly does, so it is
listed, not failed. The may tier is power-map's like-for-like predicate (PM stores
outer bounds only); `store_failures` keeps its pair list in the build for the
cross-check, and `build_warnings.INFORMATIONAL_NODES` counts its warn without
failing the stage — it is never empty.

**The named list**, `dbt/seeds/accepted_seat_overlaps.csv`: certain-tier pairs
only, one unordered span_key pair per row (`lo < hi`), a category from a closed
vocabulary, and the roster evidence (#360 part A). #501 emptied two categories —
the substitutes (`membership_type`) and Woodall's edge-dated seating (the basis) —
and dropped every pair the re-key, the basis-aware clip or the tiers took out of
the certain tier; #517 retired `merged_tenure` once a roster return opened its own
span, and #546 `never_served` once a never-seated row published none. The seed
itself is the count. `assignments_seat_occupancy_stale` warns by name on an entry no longer certain — resolved, narrowed to a
may-overlap, or re-keyed (a start re-dated across a biennium, a person merge) —
and `build_warnings` fails the stage on it until it is removed. It skips the
hermetic build. An operator event that resolves a listed pair removes the entry
in the same change.

**Counterpart clipping** (`clearinghouse_domain_legislative.seat_clipping`,
#360). The gate first measured 91 overlaps, every one a quantization artifact:
the overlay dates **the span an event names**, and the counterpart across the
handoff keeps its biennium edge. Where exactly one side's edge is **dated**, the
other side's **observed** edge yields to it, and becomes **derived** — read by
basis, never by geometry, since a dated event can sit on a biennium edge
(Woodall's 1957-01-01 seating, read as quantized until #501). A derived edge is
never read back as evidence, which stops one clip cascading into the next (it
once collapsed Donn Charnley's LD-44 tenure to a day). A stated edge known only to
a window moves its counterpart to the window's **far** bound (a successor of
"Resigned July 1900" starts no earlier than July 1), and where both sides are
stated, a day inside the other side's window wins: Marjorie Happy's "Appointed
1962" opens at her husband's June 21 death.

**Annotation windows** (`conformed.windows`, #501 D6a) run after every overlay
and before the clip, over pre-1991 Senate rows: a month- or year-dated
appointment, death or resignation dates its **observed** edge to the window, and
an election — at any precision — opens a start no earlier than itself, to the
biennium's end (owner, #360: an election is an earliest start, never a seating).
An event-dated edge wins; a contradiction is counted. An election after a
departure in the same annotation is that departure's reason, not a start
(`roster_pdf.succession.start_clause`, shared with the projector's keying).

It runs in `assignment_rows`, over the **union** of both families — one seat's
two holders routinely come from different families — and moves boundaries only:
no row is dropped, added or reordered. It reads the same capacity `role_periods`
publishes (`conformed.capacity.seat_capacity`) and declines a pair inside a
multi-holder period (`multi_holder_period`): five 1889 senators of LD-19 are not a
succession. Principals only. The refusals, counted in `seat_overlaps_unclipped`:
**neither dated**, **both dated** (the #358 shape), **merged return** (the
successor nested in a predecessor row that is two tenures, #267; no live residue since
#517, where a roster return opens its own span), **crosses a
biennium** (the roster lists the successor before the predecessor's exit), and
**multi-holder period**.

**The member axis (#282)** runs first: a member's dated seating into a new seat
closes their old seat's observed ceiling (Holmquist's 2006 House-to-Senate
move), never an open one; declined member overlaps are
`assignments_one_seat_per_member`'s to report.

**Capacity** (`conformed.capacity`, #501 D1) moves the gate's old roster
exclusion into Python unchanged: a Senate district-year the roster lists with
more than one row and no annotation — a tenure note in the name counts as one —
seats that many. Exactly five qualify, all 1889 (LD-2 ×3, LD-5 ×3, LD-8 ×2,
LD-18 ×3, LD-19 ×5). One caveat if House coverage deepens past 1965: pre-1965
House seats were at-large (#230), which is a `role_periods` population, not a
gate change.
