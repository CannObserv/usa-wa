# Pre-1991 identity, party spans and Senate seats — design (#228)

Phase 3b of [#219](https://github.com/CannObserv/usa-wa/issues/219). Prerequisite [#227](https://github.com/CannObserv/usa-wa/issues/227) is merged. Source design: [`2026-08-14-wsl-roster-pdf-source-design.md`](2026-08-14-wsl-roster-pdf-source-design.md).

This settles the two decisions #228 was gated on — the roster-only `source_id` scheme and the
1991 identity join — plus three the design pass surfaced. Every number here was measured against
the archived edition `legroster:2025-06-05` on 2026-08-19/20.

## What the source is

| | |
|---|---|
| Records | 8,517 (pre-1991 **6,162** / 1991+ 2,355) |
| Distinct folded names, pre-1991 | 2,632 |
| …appearing in one session year only | 1,332 [^fold] |
| Persons we hold today | 641, all WSL-sourced, floored at 1991 |

[^fold]: #228's issue body reports 1,330 for this quantity. Both are measured; the folds differ.
    This figure folds each name to the concatenation of its `folded_tokens` (the `usa-wa-common`
    fold), which is the fold §1's key uses — so it is the number the identity scheme actually
    operates on. The two-name gap is not reconciled; it is small enough not to change any
    decision here, and naming the fold is what stops a future reader treating one of them as
    stale.

**One edition is archived.** Every claim below about stability across revisions is therefore
*unmeasurable* from our data — the document is republished roughly twice a decade and we have
seen it once. That is a standing caveat on the identity key, not a solved problem.

## 1. Identity — name-derived, refuse on contradiction

```
source     = usa_wa_legislature_roster
source_id  = <folded-name>:<first-session-year>       e.g. johnlobrien:1939
```

**The key is computed per resolved identity, after splitting — never per folded name.** Where a
name splits into two people, each takes its *own* group's earliest session year, so
`cwredbeck` becomes `cwredbeck:1899` and `cwredbeck:19xx`. Computing it from the whole
name-group's minimum would return both halves to one id and silently undo the split.

Derived from the archive **plus the adjudication table** (§3), so a rebuild from `RawPayload`
reproduces the same ids — the property an opaque surrogate would have forfeited, and the one PM
anchors depend on. The qualifier is not a footnote: adjudicated identities are resolved by a
decision that is *not* in the archive, so **the adjudication table is checked in as versioned
data** beside the code. Without that, reproducibility holds only for the un-adjudicated
majority, and the argument that defeated the surrogate option would apply here too.

Resolution follows #227's disposition shape rather than returning a bare match:

| outcome | action |
|---|---|
| one coherent career | mint |
| positively contradictory evidence | **refuse + tally** |
| ambiguous | **refuse + tally** |

**Never a silent merge.** A merge asserts a false identity and is far harder to undo once
synced than a refusal is to revisit.

### Why a span heuristic cannot be the splitter

Only **6** folded names span more than 40 years, and the rule that flags them flags real
careers just as hard:

| folded name | span | records | verdict |
|---|---|---|---|
| `cwredbeck` | 1899–1975 | 9 | near-certainly two people |
| `elmerejohnston` | 1899–1965 | 11 | near-certainly two people |
| `johnlobrien` | 1939–1989 | 25 | **one person** — the Speaker |
| `alslimrasmussen` | 1945–1987 | 15 | **one person** |
| `victorzednick` | 1911–1959 | 9 | unresolved |
| `charlesmbaldwin` | 1899–1941 | 5 | unresolved |

So splitting keys on *positive contradiction* (overlapping simultaneous seats, an impossible
gap **with** disjoint seat lineage), never on span width. Where the evidence merely looks odd,
refuse and tally.

A worked separation the data already supports: `William S. Day` (LD4, 1959–77) and
`Bill Day, Jr` (LD3, 1985–91) are different people sharing a folded surname, correctly split by
seat and era.

## 2. The 1991 join — the WSL id wins

A roster name crossing 1991 attaches its pre-1991 rows to the **existing** WSL-sourced Person.
No new Person, no fork. The roster key is a fallback for identities with no WSL counterpart, not
a rival space.

Measured over the **109** crossing names, using the real resolver rule (seat-scoped surname
match, then #240's given-name-initial guard):

| | |
|---|---|
| resolve to exactly one WSL member | **105** |
| ambiguous | **0** |
| no seat-scoped match | **4** |

## 3. The nickname problem — corroborate, then adjudicate

**#240's given-name-initial guard rejects formal↔nickname pairs, in both directions.** Three of
the four unmatched crossing names are this, not four separate oddities:

```
'William A. Grant'  initials {a,g,w}  vs WSL 'Bill'     {b}  -> rejected  (true match: 157)
'Bob Basich – 19B'  initials {1,b}    vs WSL 'Robert'   {r}  -> rejected  (true match: 23)
'Bill Day, Jr'      initials {b,d,j}  vs WSL 'William'  {w}  -> rejected  (true match: 103)
'Shirley Galloway'  — no WSL member on any seat, any year   -> genuinely absent
```

The guard is **safe in #226** — a false rejection there refuses a succession event. It is **not
safe here**: a rejected join mints a roster Person for someone who already has a WSL identity,
which is the fork §2 exists to prevent. The failure mode inverts between the two issues.

**Fallback rule.** Where the initial guard fails, accept a seat-scoped surname match when the
same WSL member id is corroborated across **at least two** distinct session years *and* strictly
more than any rival. The floor is load-bearing: without it the rule accepts `1 > 0`, which is
exactly the #240 shape — one candidate, seen once, and possibly the wrong person.

Ordering: **guard passes → accept; guard fails → corroboration; neither → refuse + tally.**

**What that actually resolves, measured.** Corroboration decides the *contested* case and only
that case:

```
'William A. Grant'  {157: 9 yrs, 14874: 1 yr}  -> 157 accepted (contested, decisive)
'Bill Day, Jr'      {103: 1 yr}                -> REFUSED by the floor (uncontested, single)
'Bob Basich – 19B'  {23:  1 yr}                -> REFUSED by the floor (uncontested, single)
'Shirley Galloway'  {}                         -> REFUSED (no candidate at all)
```

So the rule is not a general solution to the nickname problem — it is a *tie-breaker*, and the
uncontested-but-guard-failed cases still need adjudication. The residue is **three** names, not
one. Their adjudications, from §2's evidence:

| roster name | WSL member | basis |
|---|---|---|
| `Bill Day, Jr` | 103 (William Day) | LD3 House, sole `day` on that seat 1991–92 |
| `Bob Basich – 19B` | 23 (Robert Basich) | LD19 House, sole `basich` on that seat 1991–96 |
| `Shirley Galloway` | *none* | absent from the WSL sponsor index entirely; mint a roster Person |

`Frank "Tub" Hansen` ties `{168: 1, 169: 1}` under corroboration, but **passes the initial
guard** and therefore never reaches the fallback — he is not part of this residue.

## 4. Party spans — annotations are part of the data

**Party is not a per-record constant.** A mid-term change is recorded in the annotation on the
member's **term-start** row, not by re-listing them:

```
1911 senate LD32  R      Daniel Landon  '(Changed party affiliation, 1913) Prog.'
1915 senate LD32  Prog.  Daniel Landon  '(Changed party affiliation, 1917) R'
```

**All 19 pre-1991 changes are Senate.** That is structural for the pre-1991 era: a four-year
term is listed once, so a change mid-term has nowhere to appear except an annotation, whereas a
House member's change shows up as a different token on their next biennial row.

**The claim does not generalise, and the same dataset says so.** All **three** post-1991
change annotations are *House* rows (`pre-1991 senate: 19`, `1991+ house: 3`). They annotate to
record an exact date — `January 31, 1995` — which a biennial re-listing cannot express. So House
annotations exist and must be parsed; what is Senate-specific is the *necessity*, not the
practice.

Emit split spans — close the term-start party at the change, open the new one. Landon yields
`republican 1911–12`, `progressive 1913–16`, `republican 1917–18`.

Three format families across the 22 annotations, and the vocabulary is **not** the row
vocabulary:

- 15 — session year + token: `(Changed party affiliation, 1897) Pop.`
- 4 — calendar date, no token: `Changed party affiliation February 13, 1981`
- 3 — prose: `Changed party affiliation to Democrat, December 13, 2007`
- **`Silver R`** appears in two annotations and is **not in `PARTY_TOKENS`** — `resolve_party_token`
  returns `unknown_token` for it. A #227 follow-on: the annotation vocabulary needs its own
  folding, and an unparseable annotation must refuse with a tally, never guess.

Without this, the three 1913 Progressive senators keep Progressive spans through 1917 against
the roster's own statement that they became Republicans in 1915.

## 5. Senate seat spans — expand, bounded by the next listing

The roster lists a member only in the session year their term begins. Measured gaps between
consecutive listings on one seat:

| chamber | 2 yr | 4 yr | other |
|---|---|---|---|
| house | 3,259 (99%) | 17 | 4 |
| senate | 145 (9%) | 1,448 (91%) | 2 |

A flat four-year expansion overruns the next occupant **145 times**, and Senate is one seat per
LD — two people holding one seat. So:

```
span_end_year = min(term_start + TERM_YEARS, next_listing_on_this_seat) - 1
```

**That yields a year; spans carry dates and the engine quantizes to bienniums.** The conversion
is where an off-by-one becomes a span opening in the wrong biennium — the class of defect #226
spent three review rounds on. This spec does **not** invent a convention: the year is handed to
the existing biennium quantizer, which already owns the mapping from a session year to its
biennium bounds. Any date finer than that (a dated resignation, say) arrives from #226's
succession events, not from here.

`audit.py` records that next-listing termination is **unsound for the House** (consecutive rows
in one district-year are different seats, not a succession). The House is out of #228's scope
for exactly that reason — see #229 and #230.

### The truncations emit no events here

The 145 truncations cluster on **redistricting**, not turnover: 27 begin in 1931–33, precisely
when the district count falls 53 → 46. Annotation coverage:

| stated cause on the truncated term | count |
|---|---|
| **none** | **83** (57%) |
| redistricting / holdover | 33 |
| elected to unexpired term | 11 |
| deceased | 9 |
| resigned | 7 |
| appointed / other | 2 |

Inferring a vacancy for all 145 would invent at least 33 known-false events.

**The real events already exist.** #226 parses succession boundaries from these same annotations
and currently refuses **361** for `no_member` — **347 of them pre-1991**:

```
departed 177 · seated 169 · vacated 15      (senate 143 / house 218)
```

They are refused only because there is no Person to attach them to. So #228 emits spans and no
events; **re-running the existing #226 backfill after #228 lands** resolves them on their own
stated verbs and dates — better evidence than the truncation shape, and no second implementation
of succession parsing. #226 already defers 244 boundaries as `no_succession_verb`, so the
machinery will not invent a cause for the 83 silent truncations.

## 6. Labels with no Org

Two members carry party labels that #227 declines because no Org exists: `George M. Welty`
(LD1 House 1899, `Cit.`) and `Knute Hill` (LD59 House 1927, `Prog.`). Their **seat spans build
normally**; only the party Assignment is withheld. Per power-map#442 the label itself stays
recordable as a note or citation on the seat assignment. `resolve_party_token` already returns
the disposition and reason, so the label is available to whatever writes the span — nothing
consumes it yet.

## Scope

**In:** pre-1991 Persons, party spans (incl. annotation splits), Senate seat spans.
**Out:** House seats (#229 1965+, #230 pre-1965), succession events (#226 re-run), leadership (#231).

## Acceptance oracle

Following #227's shape — arithmetic, not inference:

1. Every pre-1991 record resolves to a Person, or is **refused with a tally**. Zero silent drops.
2. Every refusal names its reason and its subject, so the residue is actionable rather than a count.
3. No Person carries two simultaneous Senate seats **and** no Senate seat carries two
   simultaneous Persons. Both are needed: §5's truncation rule enforces the seat side, so the
   person side is the one nothing else checks — a member listed under two LDs across a
   redistricting boundary trips it.
4. The 109 crossing names produce **zero** new Persons for identities that already exist in the
   WSL space.
5. Party spans for the 22 annotated members reflect the annotation, not the row token alone.

## Standing risks

- **Revision stability is unmeasurable** with one archived edition. A reprint that renames a
  member forks them. Mitigation available because we keep every edition: diff a new edition's
  parse against the archived one *before* minting.
- **1,332 of 2,632 names appear in a single session year**, so tenure-overlap and party-continuity
  disambiguators are weakest exactly where the volume is highest.
- **`Shirley Galloway`** serves LD49 House 1979–83 and LD17 Senate 1993 but appears nowhere in the
  WSL sponsor index despite 1993 being covered. Either a WSL coverage gap or a roster error;
  unresolved.

## Addendum (2026-08-20): the #252 parse corrections re-measure this document

Implementation began with a survey of the six wide-span names in §1 and immediately falsified
one of them: `cwredbeck`'s 1899 row carried a **1974 appointment annotation** — a parser
defect, not a second person. Three defect classes were fixed under
[#252](https://github.com/CannObserv/usa-wa/issues/252) (per-chamber year state at column
boundaries; rows wrapping before their dotted leader; a footer band that cut the bottom line
of every full page). The corrected corpus:

| | was | is |
|---|---|---|
| records | 8,517 | **8,584** |
| unparsed | 2 | **0** |
| pre-1991 records | 6,162 | **6,217** |
| distinct pre-1991 folded names | 2,632 | **2,640** |
| …single session year | 1,332 | **1,330** |
| crossing names | 109 | **109** (unchanged) |

What changes in this design:

- **§1's wide-span table**: `cwredbeck` (1899–1975) is **one person** — C. W. "Red" Beck,
  House 1961–73, Senate 1974–78; his "1899" row was a mis-yeared 1974 appointment. The
  splitter's worked examples reduce to `elmerejohnston` (1899 P.P. vs 1947–65 R, stands as
  printed) and the two unresolved. The *rule* (positive contradiction, never span width) is
  unchanged — the falsified case only strengthens it, since a span heuristic would have split
  a real person on an artifact.
- **The 1,330/1,332 footnote resolves**: the corrected parse measures **1,330** under the
  `usa-wa-common` fold — the issue body's figure. The two-name gap *was* the parse artifacts.
- **Seven printed members were invisible** to the measured corpus, five as whole-person
  losses (Newschwander's 1969 Senate term; Frances Swayze, whose resignation was pinned on
  Pat Comfort; W. L. McCormick; Betty Sue Morris and Tom Mielke in the WSL era) plus ~60
  footer-cut rows. §2's join and §3's residue were re-measured after the fix: 109 crossing
  names, unchanged outcomes.
- **§5's gap table improves**: the two `other` Senate gaps were both dropped rows, now
  recovered; the Senate listing sequence has no anomalous gaps. The House residual is one
  cluster-merged 1987 row (LD43) and the three genuine 1931–1959 redistricting dormancies
  (LD47/48/49).
- **The #226 re-run gains urgency**: the old parse attributed at least one succession
  annotation to the wrong member (Comfort vs Swayze), so re-deriving events after #228 also
  corrects subjects, not just coverage.
