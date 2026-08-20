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
| …appearing in one session year only | 1,332 |
| Persons we hold today | 641, all WSL-sourced, floored at 1991 |

**One edition is archived.** Every claim below about stability across revisions is therefore
*unmeasurable* from our data — the document is republished roughly twice a decade and we have
seen it once. That is a standing caveat on the identity key, not a solved problem.

## 1. Identity — name-derived, refuse on contradiction

```
source     = usa_wa_legislature_roster
source_id  = <folded-name>:<first-session-year>       e.g. johnlobrien:1939
```

Derived from the archive alone, so a rebuild from `RawPayload` reproduces the same ids — the
property an opaque surrogate would have forfeited, and the one PM anchors depend on.

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
same WSL member id is corroborated across strictly more distinct session years than any rival.
This separates the #240 trap decisively:

```
LD16 House, roster 'William A. Grant'
  member 157   (Bill Grant)    corroborated 9 years  [1991…2007]   <- accepted
  member 14874 (Laura Grant)   corroborated 1 year   [2009]
```

Ordering: **guard passes → accept; guard fails → corroboration; neither → refuse + tally.**
Everything is derived from evidence; no curated nickname table. The residue after this rule is
two names (`Shirley Galloway`, one contested), adjudicated explicitly and recorded.

## 4. Party spans — annotations are part of the data

**Party is not a per-record constant.** A mid-term change is recorded in the annotation on the
member's **term-start** row, not by re-listing them:

```
1911 senate LD32  R      Daniel Landon  '(Changed party affiliation, 1913) Prog.'
1915 senate LD32  Prog.  Daniel Landon  '(Changed party affiliation, 1917) R'
```

**All 19 pre-1991 changes are Senate**, and that is structural rather than coincidental: a
four-year term is listed once, so a change *must* be annotated, while a House member's change
simply appears as a different token two years later.

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
span_end = min(term_start + TERM_YEARS, next_listing_on_this_seat) - 1
```

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
3. No Person carries two simultaneous Senate seats.
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
