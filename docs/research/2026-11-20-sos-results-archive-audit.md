# SOS election results archive (xlsx, 1910–2006): coverage audit (#585)

A read-only audit of the Secretary of State's *Washington State Elections Results Archive*
spreadsheet, done before anything is built on it
([ARCHITECTURE.md § Audit before you build](../ARCHITECTURE.md#audit-before-you-build)).
It answers the seven questions of the
[#585 pre-flight](https://github.com/CannObserv/usa-wa/issues/585#issuecomment-6729841498)
and ends with a go/no-go recommendation. No production code, no Phase A or B, and no
`coverage.py` claims came out of it: the measured tables below are what a build would turn into
claims.

## Verdict

- **Go**, for a narrow build: the even-year generals of 1966–2006 as House Position evidence.
  They carry 2,058 of the 2,061 House races those 21 generals should hold, every one with an
  unambiguous winner, and they agree with all 262 comparable Positions already published.
- **It closes most of #140's gap.** Of the 659 House member-bienniums the WSL roster attests
  through 2007-08 that have no published seat today, the archive positions **610 from the
  seating general alone** and 40 more from a later candidacy. Era A (1991-92 … 2001-02) has no
  published seat today except two in 2001-02; with the archive it has 96 to 98 per biennium.
- **Five things in the file are wrong, and a build must not trust them:** the Position label
  on odd-year special elections, 19 races of the 1982 general repeated as 1983, nine races filed under the wrong office (three House races as
  Senate; six Senate races as House, in 1918 and 1977), the `ElectionDate` of 31 of 59 generals, and candidate spellings.
- **Before 1966 the file has no Positions at all.** `Position #1` is a placeholder for an
  at-large pool. The first Positions were elected in 1966, so the House seated for 1965-66 was
  still at-large.

## The file

| | |
|---|---|
| Landing page | <https://www.sos.wa.gov/elections/data-research/election-data-and-maps/election-results-and-voters-pamphlets/washington-state-election-results-archive> |
| Resolved link | `/sites/default/files/2026-04/Washington%20State%20Elections%20Results%20Archive.xlsx` (the page's only `.xlsx` href) |
| Fetched | 2026-11-20T22:12Z, into scratch, never into `raw/` |
| Size, sha256 | 2,503,493 bytes, `6699e1c8c01544b4039af43b2dbb5bd44caabab6a3c7e6f617a4ee8c6929f986` |
| Response headers | `Last-Modified: Wed, 15 Apr 2026 00:00:27 GMT`, `ETag: "69ded51b-263345"`, `Cache-Control: public, max-age=7200` |

The bytes are identical to the 2026-11-08 fetch recorded on #585. The `2026-04` path segment
is the upload month, so a re-upload will move the link: resolve it from the landing page.
Unlike the roster PDF, this response carries cache validators.

One sheet, `Elections Database`: 30,674 data rows and 16 columns (`ElectionID`, `ElectionDate`,
`Description`, `ElectionTypeID`, `OfficeID`, `OfficeType`, `OfficeName`, `OfficePosition`,
`OfficeKey`, `MeasureText`, `FirstName`, `LastName`, `BallotName`, `BallotOrder`, `PartyName`,
`Votes`). There is no winner flag and no incumbent flag.

| `OfficeType` | Rows |
|---|---|
| Legislative | 22,937 (House 18,249, Senate 4,688; General 11,790, Primary 11,147) |
| Federal | 2,958 |
| Statewide | 1,984 |
| Judicial (Superior, Supreme, Court of Appeals) | 1,989 |
| Measures | 806 |

Only the legislative rows were audited. The other 7,737 rows are noted so a later harvest
archives the whole file rather than a slice of it.

### Which legislative elections are in it

| | Present | Absent or thin |
|---|---|---|
| House general | every even year 1914–2006 (47 generals) | 1910 and 1912: no House rows at all |
| Senate general | every even year 1914–2006 | 1912: none. 1910: 10 rows, 4 of 22 districts |
| House primary | even years 1946–2006 | 1948: none. 1932–1944: 12 districts each. Before 1932: none |
| Senate primary | even years 1946–2006 | 1948: none. 1910–1944: 4 to 11 districts each |
| Odd-year specials | generals 1977, 1981, 1983, 1985, 1987, 1995, 1997, 1999, 2001, 2003, 2005 | primaries only for 1977, 1995, 1997, 1999, 2001. Most of 1983 is a repeat of 1982 ([§ Defects](#defects-in-the-file)) |

Legislative coverage is 1910–2006. Nothing overlaps the `sos-legresults` feed, whose House
floor is the 2008-11-04 general and whose 2007 index holds Senate specials only (#140).

## Method

Scratch analysis, re-runnable from this description.

- **Parse by shape.** Chamber from whether `OfficeName` names a representative or a senator;
  district from the digits after `District`; Position from the trailing digits of
  `OfficePosition`; election from the year in `ElectionDate` plus `ElectionTypeID`. No exact
  label is matched. All 22,937 legislative rows parse, with no rejects.
- **Oracle: the members PDF**, as `stg_roster_members` (edition `legroster:2025-06-05`, 8,589
  rows), through `roster_pdf/audit.py`. A roster row covers the term it opens (`TERM_YEARS`), so
  a winner of the year-*Y* general is a claim on year *Y*+1.
- **Both directions.** *Winners → roster* asks whether each derived winner was seated (the
  `audit_roster` path). *Roster → winners* asks whether each member who opened a term appears
  as a winner. The second is the completeness measure. A roster row counts as opening a term
  when its annotation states no appointment, swearing-in or election ahead of any resignation
  or death (`succession.parse_annotation`). A clause after a departure says where the member
  went, not how they arrived. Mid-term successors are left out.
- **Names** fold through `usa_wa_common.names` (`surname_match_set`, `fold_token`,
  `strip_non_name_parts`), with no exact strings. A miss was then re-tested with an edit
  distance of two, only to classify it. That second pass never counts as a match.
- **Published state:** `assignments` `v20261109T080301Z-511694` (schema 1.8.0, 8,397 rows) and
  `stg_wsl_sponsors`, read from a scratch copy of `data/pipeline.duckdb`.
- **Checked twice.** A second pass re-derived the file facts, §§ 1–3, § 6 and the hazard counts
  from the raw workbook with separate code. Where a figure depends on how a name is keyed, the
  text says so and gives both results.

The match rates below are a floor on disagreement, not a score, for the reason
[MODULES-LEGISLATURE-ROSTER.md](../MODULES-LEGISLATURE-ROSTER.md) gives: a roster row covers
its whole term, so the oracle under-reports disagreement.

## 1. Completeness per district and year

Roster → winners, by era. "Term-openers" are roster rows that open a term by election.

| Era (general) | Chamber | Term-openers | No general in file | District absent | On the ballot | …and the winner | Spelling near-miss | Other |
|---|---|---|---|---|---|---|---|---|
| 1910–1964 | House | 2,748 | 193 | 0 | 2,524 | 2,503 | 27 | 4 |
| 1910–1964 | Senate | 651 | 21 | 24 | 596 | 596 | 10 | 0 |
| 1966–2000 | House | 1,737 | 0 | 2 | 1,726 | 1,726 | 8 | 1 |
| 1966–2000 | Senate | 430 | 0 | 1 | 426 | 426 | 2 | 1 |
| 2002–2006 | House | 290 | 0 | 0 | 289 | 289 | 0 | 1 |
| 2002–2006 | Senate | 71 | 0 | 0 | 71 | 71 | 0 | 0 |

Reading it:

- **1966–2006 is complete to within six rows,** spelling aside. Three are the mislabelled
  races of [§ Defects](#defects-in-the-file) (Wynne 1990; Murray and Chopp 1996). One is a
  Senate race the file lacks (1970 LD 2, Bruce A. Wilson). One is a name change (the file's
  `Jeanne Kohl` is the roster's Jeanne Kohl-Welles, 1998). One is a roster quirk (Brian Blake
  appears in the 2003 group without an annotation; he held the seat by appointment after the
  2002 winner resigned).
- **Races, counted directly:** the 21 even-year generals of 1966–2006 should hold 2,061 House
  races (99 in each of 1966–1970, 98 after). The file holds 2,058. The missing three are 1990
  LD 39 Position 1 and both 1996 LD 43 Positions, all filed under the Senate office.
- **1914–1964 is complete for who was on the ballot,** less spelling. The 193 House
  term-openers with no general are 1910 and 1912; the 21 Senate ones are 1912. The 24 absent Senate districts are
  1910 (18), 1918 (4, mislabelled as House races) and 1926 (2, LD 3).
- **Every near-miss is a one- or two-letter variant** (`Herrman`/`Herrmann`,
  `Mafaffey`/`Mahaffey`, `Thibadeau`/`Thibaudeau`), 47 in all. At least one is the roster's own
  typo (`Martiamnis` for Martinis, 1983).

Winners → roster, through `audit_roster`, for every race with one seat (the Senate in all years,
the House from 1966): **3,268 claims, 3,227 attested (98.75%)**. The count is of races as
labelled: 19 of them are the 1983 repeats of 1982 ([§ Defects](#defects-in-the-file)), attested
because 1982's winners were seated. The 41 unattested, classified by hand:

| Class | Count | Example |
|---|---|---|
| Spelling variant | 22 | `A.J. Kyan` for A. J. Ryan, 1920 |
| Name format: suffix, particle, parenthetical, reversed | 8 | `J.T. Quigg, Jr.`; `J. Vander Stoep`; `Swecker, D` |
| Name change | 1 | `Jeanne Kohl` |
| **Winner never sat for the term** | 5 | Jolly 1970, Chapin 1970, Freeman 1976, Doumit 2002, Hatfield 2004 |
| **Race filed under the wrong office** | 2 | Wynne 1990, Murray 1996 |
| Roster-side | 3 | LD 43 1987 cluster-merge (2 rows); `Martiamnis` |

No derived winner contradicts the roster about who won. The five who never sat are real:
each resigned between the election and the term, and a successor was appointed. An election
winner is not a seating.

Two of the 3,227 attestations are spurious. The 1977 LD 24 and LD 33 races are Senate specials
filed as House races ([§ Defects](#defects-in-the-file)); each winner passes as a House claim
only because they sat in the House until that election.

The per-year table is [Appendix A](#appendix-a-per-year-completeness).

## 2. Pre-1965 `Position #1` is a placeholder

Yes. Through the 1964 general every House row but 13 says `Position #1` (8,719 of 8,732), and
that one "Position" holds the district's whole at-large pool:

- In **1,066 of 1,329** pre-1965 pools, two or more seated members sit under one Position
  label: 920 pools with two, 142 with three, 4 with four (for example 1948 LD 38:
  Bargreen, Carmichael and both Mardesiches).
- In **1,301 of the 1,325** pools where a seated member was matched, the seated members are
  exactly the top vote-getters. That is block voting, not a Position contest. The other 24 are
  spelling near-misses and same-surname collisions, plus a handful where a winner did not sit.

Both pool figures depend on the name key. The second pass got 927, 137 and 2 pools (the same
1,066) and 1,306 of 1,325. The conclusion does not move.

The 13 `Position #2` rows are a defect, not Positions. All are the 1918 general, in LDs 2, 16, 19
and 24. In each, the "Position #1" race (two candidates, plus a `Scattering` row in LD 19) is the
district's **Senate** race (W. Lon Johnson, John C. Crawford, P. L. Sinclair, Wm. Bishop: all
senators in the 1919 roster, and all four districts lack a 1918 Senate general row), and the
House pool was pushed to `Position #2`. Four of the 1,329 pools above are these.

**The boundary by seating is 1967, not 1965.** The 1964 general is a pool in all 49 districts;
1966 is the first with `Position #2` rows statewide. So the members seated for 1965-66 were
elected at large, and 1967-68 is the first biennium with Positions. The repo's "pre-1965"
shorthand follows the year the law changed, as the #230 notes record it; this audit measured
only the elections. Nothing published depends on the difference today, since
no House span exists before 2001-02; a pre-1991 House build would.

## 3. The 13 `Position #3` rows

Genuine. All are LD 42 in 1966, 1968 and 1970 (primary and general; Veroske, then Van Dyk).
LD 42 elected three representatives at each of those elections, and the roster agrees: three LD 42
House members in each of 1967, 1969 and 1971, two from 1973. Those three generals hold 99 House
races each for that reason.

`seat:house:ld-42:position-3` does not exist in `roles` (98 House seats). A pre-1973 House
build would need it, with a `role_periods` window.

## 4. Deriving winners

Only `General` rows elect. The rule, per shape:

| Race shape | Where | Rule | Measured |
|---|---|---|---|
| One seat | Senate, all years; House from 1966 | Top `Votes`, by the `_top_vote_winner` discipline: a sole candidacy wins without a count; a tie or a missing count omits the race | 3,268 races as labelled, the odd years of the last row included: 442 uncontested, **0 ties, 0 missing counts, 0 omitted** |
| At-large pool | House through 1964 | Top *N*, where *N* is the district's seat count. ***N* is not in the file** | Taking *N* from the roster, the seated members are the top *N* in 1,301 of 1,325 pools |
| Odd-year special | 11 odd years | Top `Votes`, for the unexpired term. **Do not read the Position** ([§ Defects](#defects-in-the-file)) | As labelled, 32 House races and 19 Senate. In fact 16 and 16: 19 labelled races repeat 1982, and two House-labelled ones are Senate specials |

- **Primaries nominate and never elect**, under every system the file spans: party primaries
  to 1934, the blanket primary 1936–2002, pick-a-party in 2004 and 2006. Top-two began in 2008,
  outside the file. (The system dates are background, not something this audit measured.) Measured for 1966–2006: in 4,597 of 4,712 cases a party's top primary
  vote-getter is its general candidate. A primary row is candidacy evidence only: it shows
  which Position someone filed for (the McGrath case, #495).
- **Ties:** none in any one-seat general.
- **Write-ins:** four `Scattering` aggregate rows (three in generals, at most 162 votes), plus
  one-vote surname-only rows in 1918 and 1932. None can win. Do not drop rows for lacking a
  `FirstName`: the 1995 LD 43 Senate winner is recorded as `Thibaudeau` alone.
- **Fusion lines:** eight candidates in the 1910–1918 Senate primaries appear twice, once per
  party. Sum them. One 1910 general candidate is duplicated outright (`H. D. Jory` and
  `H.D. Jory`, 669 votes each); do not sum those.
- **Zero-vote rows** (7) are placeholders. Four say so in the name (`- Withdrew`,
  `- filled by Democratic Central Com.`); three are bare names in a 1946 primary.

## 5. Name matching

Through `usa_wa_common.names`, with no exact strings. Of the 5,713 term-openers whose general
is in the file, **5,632 (98.6%) are on the ballot by the names module** and 47 (0.8%) only by a
spelling near-miss.

| | On ballot by the names module |
|---|---|
| House 1914–1964 | 2,524 of 2,555 (98.8%) |
| Senate 1910–1964 | 596 of 630 (94.6%; 98.3% where the district is present) |
| House 1966–2006 | 2,015 of 2,027 (99.4%) |
| Senate 1966–2006 | 497 of 501 (99.2%) |

Read these as a floor on disagreement. The names module does what it promises: every miss is a
real difference in the source text, not a folding failure. Two things follow for a build:

- **Spelling variants need an alias table,** as the roster identity fold already keeps one
  (`identity.py`). Fuzzy matching inside an LD would be a guess. In the WSL era the known ones
  are few: `Thibadeau` (1994), `Penninton` and `Shindler` (1998).
- **`audit.py`'s surname test is asymmetric for particles.** `J. Vander Stoep` (file) against
  `J. VanderStoep` (roster) fails as a claim and passes the other way round. Not a defect in
  the audited source; noted because the oracle under-counts by it.

## 6. What it would unlock

**The gap today.** Published House seat spans start at 2001-02. Counting House member-bienniums
that `stg_wsl_sponsors` attests through 2007-08 with no published `seat:house:*` span gives 659.
The archive's evidence for each, in falling order of strength:

- **A** — the member won the general that seated the biennium, in that LD, at one Position.
- **B** — no A, but the member ran for exactly one Position in the next regular cycle (an
  appointee defending the seat).
- **D** — neither, but the LD's other member is positioned and the #103 elimination applies.

| Biennia (plan) | Unpositioned today | A | B | D | Still unpositioned |
|---|---|---|---|---|---|
| 1991-92 (1982 plan) | 100 | 97 | 1 | 1 | 1 |
| 1993-94 … 2001-02 (1991 plan) | 519 | 476 | 37 | 0 | 6 |
| 2003-04 … 2007-08 (2001 plan) | 40 | 37 | 2 | 0 | 1 |
| **Total** | **659** | **610** | **40** | **1** | **8** |

The eight left over are seven appointees who never won a seat inside the file's range
(Carlson, Scheuerman, Gordon, Blalock, Marine, Lysen, Loomis) and one spelling miss
(Thibaudeau, 1995-96).

Distinct House seats with a positioned holder, per biennium:

| Biennium | 1991-92 | 1993-94 | 1995-96 | 1997-98 | 1999-00 | 2001-02 | 2003-04 | 2005-06 | 2007-08 |
|---|---|---|---|---|---|---|---|---|---|
| Published today | 0 | 0 | 0 | 0 | 0 | 2 | 78 | 90 | 94 |
| With A and B | 97 | 98 | 98 | 98 | 98 | 96 | 98 | 98 | 98 |

**Below the WSL floor** (roster era, sessions 1967–1989): of 1,230 roster House rows, 1,170 are
A and 47 are B (1,168 and 49 under the second pass's surname rule; 1,217 either way). None is published today, and publishing them is a larger build than this one
(roster-sourced House spans, the LD 42 third seat, `role_periods` per plan).

**Agreement with what is already published.** Every published House member-biennium through
2007-08 was compared with the archive:

| | Count |
|---|---|
| Agrees, by A | 257 |
| Agrees, by B | 5 |
| **Disagrees** | **0** |
| Archive silent | 5 |

- The five silent ones are 2007-08 appointees seated after the file's last election (Schmick,
  N. Smith, Herrera, Liias, S. Nelson).
- **The four #492/#495 movers all agree by A:** Delvin (LD 8 Position 2), Carrell (LD 28
  Position 2), Pflug (LD 5 Position 1), Berkey (LD 38 Position 2). The archive carries each
  mover's own ballot Position, so the successor inference is corroborated, not needed.
- **Known gap, not a disagreement (#586):** McGrath (9951) and Lawrence (9960) have recorded
  seatings and no published House span. Both are class B at Position 2, which matches their
  recorded seatings.
- The back-chained Positions of 2003-04 … 2007-08 (#118) are inside the 262: none disagrees.

One conflict appears only if a special election's label is trusted: Takko, 2005-06, published
at LD 19 Position 1; the 2005 special row says `Position #2`. His 2006 rows say Position 1, and
the seat he was appointed to was Hatfield's Position 1. The published value is right.

## 7. Parser hazards

**Labels.** One shape each, with no variants: `State Representative District #N`,
`State Senator District #N`, and `Position #1`/`#2`/`#3` or empty for the Senate. `OfficeKey`
restates the same thing (8000 + LD × 10 + Position, 0 for the Senate) and agrees with the
labels on all 22,937 rows. That is cleaner than the results CSV's three variants (#101), and is
exactly why an exact-string parser would be tempting. Parse the shape anyway: the next upload
owes us nothing.

**Typed cells.**

- `LastName` is a **boolean** on 7 rows (6 legislative): Arthur L. True's surname was coerced
  by the spreadsheet. Read `BallotName`.
- `ElectionDate` is a 255-character string of `#` on 4 rows, all 1898 measures (a pre-1900
  date the spreadsheet cannot hold). None is legislative.
- `OfficePosition` is an integer on 728 measure rows and the string `NA` on federal and statewide rows.
- `Votes` is empty on 2 rows (a 1936 judicial primary).
- `results/workbook.py` reads xlsx with the stdlib; this sheet's typed date and boolean cells
  need their own handling there.

**`ElectionDate` is not the election day.** 31 of the 59 legislative general dates are wrong:
29 are 1 November (every general of 1960–1999, and 2001), 2000 says 4 November (held on the
7th) and 2004 says 9 November (held on the 2nd). Twelve primaries are dated the 1st. Key an
election on the year plus `ElectionTypeID`, which is unique per `ElectionID`, and never on the
date.

**Names.**

- 269 `BallotName`s carry a comma suffix (`Jr.`, `Sr.`, `M.D.`, `III`); 1,539 carry a
  parenthetical or quoted nickname; 5 are reversed, four with a comma (`Winsley, Shirley`) and
  one without (`Lee Eleanor`).
- Some carry annotations or party codes inside the name: `- Withdrew`, `Young-P`, `Siegel-BR`.
- `LastName` disagrees with `BallotName` on 48 legislative rows (typos, a truncated `albot`, a
  suffix inside `LastName`).
- 98 `FirstName` values have leading or trailing whitespace; 31 `BallotName`s have doubled spaces.

**Party.** 46 distinct labels. `sos_party_slug` classifies `Democratic` and `Republican` (21,606
rows) and nothing else: 1,331 rows across 44 labels are unclassified, among them abbreviations
(`IND`, `IP`, `Fed.`, `CPY`, `B`, `ER`, `TP`) and 46 empty. In 23 one-seat generals of
1966–2006, two candidates carry the same major party, which those primaries could not
produce. Treat party as a weak tiebreak, never as a key.

## Defects in the file

Four classes, each found by cross-checking rather than by reading labels.

**1. Races filed under the wrong office.**

| Election | LD | In the file | In fact |
|---|---|---|---|
| 1918 general | 2, 16, 19, 24 | House `Position #1` (2 candidates), House pool at `Position #2` | The Senate race, and the House pool |
| 1977 general | 24, 33 | House `Position #1` (Conner beat Nisbet; Lee beat Pedersen) | Senate specials for unexpired terms. The roster seats both winners in the Senate from November 1977 and fills their House seats by appointment that December; the file has no 1977 Senate row for either district |
| 1990 primary and general | 39 | `State Senator` (Wynne beat Wilson) | House Position 1. The same chamber conflation as #144, here in SOS's own data |
| 1996 general | 43 | `State Senator`, with Murray and Chopp as rivals | House Positions 1 and 2, each unopposed. The 1996 primary files them correctly |

The 1996 case yields a **false Senate winner** under any top-vote rule. It is caught by shape:
a Senate race in a district whose senator is mid-term, and a district missing House races.
The 1977 cases yield two false House winners, and nothing in the file's shape gives them away:
only the roster does.

**2. A block of the 1982 general is repeated as 1983.** `1983 General` holds 41 legislative
rows. Four are real: the LD 18 specials (House, Linda Smith; Senate, Alan Thompson). The other
37 are both House Positions in LDs 43–49 and the Senate in LDs 44–48, 19 races, and every one
matches the 1982 general vote for vote (LD 43 Position 1: Niemi 18,543 and Grytting 3,326 in
both). The names are keyed again (`William L. Burns`, `Bill Burns`), so it is a second entry
of one canvass and not a copied row. Two of its "winners" had left the seat before November
1983: McDonald (House LD 48) was appointed to the Senate that September, and Jones (Senate
LD 48) resigned on 1 September. Every race's vote counts were compared with every other
election's; nothing else in the file repeats.

**3. Odd-year special elections carry an unreliable Position.** Of 32 odd-year races labelled
House, 14 are the 1983 repeats and two are the 1977 Senate specials. Of the 16 real House
specials, **six labels contradict both the seat that was vacated and the winner's own next
candidacy**:

| Special | Winner | Label | Vacated seat | Winner's next race |
|---|---|---|---|---|
| 1977 LD 19 | Erak | 1 | 2 (Monohon) | 2 |
| 1977 LD 48 | Sanders | 1 | 2 (Freeman) | 2 |
| 1981 LD 2 | Kaiser | 1 | 2 (Erickson) | 2 |
| 1987 LD 18 | Cooper | 1 | 2 (Tanner) | 2 |
| 1995 LD 4 | Sterk | 1 | 2 (Padden) | 2 |
| 2005 LD 19 | Takko | 2 | 1 (Hatfield) | 1 |

(Cooper's case: Tanner, the 1986 Position 2 winner, resigned in December 1986 and Cooper was
appointed the same day; the district's Position 1 member did not resign until after the 1987
election.) The labels look defaulted: Position 1 on 11 of the 12 real specials through 1999,
Position 2 on all four of 2001–2005. Use a special for who won, and never for which Position.

**4. Even-year Positions: sound.** Three independent checks:

- Primary and general agree on a candidate's Position in **3,814 of 3,814** cases where both
  exist (1966–2006), pairing a candidate within a year and district on folded `LastName` plus
  first initial. The second pass tried five name keys of its own and got 3,603 to 3,866
  pairs, with no disagreement under any.
- A winner re-elected in the same LD at the next general kept the Position in 1,091 of 1,106
  cases within a plan, and 193 of 204 across a redistricting, on the same key. The second pass
  counted 26 switches as well, on denominators of 1,120 and 211.
- All 262 published comparisons agree (§ 6).

Among the switches are four **mutual swaps**, where a district's two incumbents trade
Positions: LD 20 1966→1968 (Kalich, Jastad), LD 18 1970→1972 (Paris, Thompson), LD 23
1972→1974 (Smith, Randall) and LD 5 2000→2002 (Anderson, Pflug). None looks like one year's
labels transposed: in each, the new arrangement still holds at the following general, and in
three of them an incumbent had held the old Position for two generals or more. The LD 5 swap
is inside the WSL era, and the Positions published for 2003-04 agree with its 2002 side. Read
all four as real.

## Side findings

- **The roster parse folds districts 50–60** (found here; outside this audit's scope). The
  members PDF prints districts 50–51, 52–53, 54–56 and 57–60 on shared pages (pp. 151–154),
  and `stg_roster_members` files them all under 51, 53, 55 and 59: it has 53 distinct districts
  where the PDF and this archive have 59 or 60. It affects House rows before 1933 only. This
  audit mapped the archive onto the folded numbering so the comparison stayed fair; unmapped,
  it reads as 63 missing members. Worth its own issue.
- **1965-66 was an at-large House** (§ 2).
- **#144's Wynne conflation has an upstream twin.** SOS's own 1990 rows put his House race
  under the Senate office.

## Recommendation

**Go: build an ingest, scoped to what the audit verified.**

1. **Scope v1 to the even-year generals of 1966–2006 as House Position evidence for the WSL
   era** (bienniums 1991-92 … 2007-08, the generals of 1990–2006). Class A alone positions 610
   of the 659 unpositioned member-bienniums, with no new inference rule.
2. **How it composes with `sos-legresults`:** as a third producer of the same
   `{LD: [HousePosition]}` map (`usa_wa_common.ballot`), selected by election year. The archive
   serves election years through 2006 and `sos-legresults` serves 2008 onward; the two never
   overlap and are never merged for one election. The projector, `positions.position_for` and
   the #103 elimination need no change. With a ballot in every biennium from 1991-92, the #118
   back-chain has nothing left to reach below 2009; ballot already wins over a seed, and the
   262-for-262 agreement says no published Position should move.
3. **Phase A** archives the whole workbook as one resource under a new source slug beside the
   two SOS feeds. Treat it like the roster PDF: a frozen document, harvested on demand,
   re-checked monthly by a dry run, with the href resolved from the landing page. It has an
   `ETag` and `Last-Modified`, so the re-check can be conditional.
4. **Guards the build needs,** each from a measured defect:
   - never read a Position from an odd-year row;
   - drop a race that repeats another election's vote counts (the 1983 block), found by
     comparing counts and not by listing districts;
   - omit, never guess, a district-year whose House race count or Senate race is the wrong
     shape (1990 LD 39, 1996 LD 43), leaving those three seats to an operator event;
   - key elections on year and type, never on `ElectionDate`;
   - read `BallotName`, never `LastName`;
   - keep a curated alias table for spelling variants, with no fuzzy matching.
5. **Coverage claims for that Phase A** (`coverage.py`, #180), from the tables above:
   `house_general` verified 1966–2006 for Positions and 1914–1964 as at-large pools, absent
   1910–1912; `senate_general` verified 1914–2006, absent 1912, partial 1910; primaries as
   candidacy evidence from 1946, absent 1948.
6. **Defer:** class B (40 member-bienniums; "ran next cycle" is a new inference and deserves
   its own decision), anything before the WSL floor (1,217 more House rows, but it needs
   roster-sourced House spans, the LD 42 third seat and per-plan `role_periods`), and the
   at-large era, which needs the #230 seat model first.

**#140 narrows; it does not close.** Its premise that Era A has no reachable source is now
false: this archive passes #140's own acceptance test (year, LD, Position, winner, party for
the 1992–2000 generals) for 96 to 98 seats in every Era A biennium. But nothing published
changes until the ingest ships. Keep #140 open as the tracking issue for that build, re-scoped
from "find a source" to "ingest this one", and close it when Era A is published. What stays
out of reach afterwards is small and named: the eight member-bienniums of § 6, the three
mislabelled seats, and McGrath and Lawrence until #586 is settled.

## Appendix A: per-year completeness

Roster → winners, per general. *Districts* is roster districts that have rows in the file, over
roster districts with a term-opening row; a district the file has and the roster lacks is not
counted. *Missing* is term-openers not found, for any reason. House districts before 1932 are in
the roster parse's folded numbering (53 districts). House "winner" before 1966 is the pool test
of § 2: the seated members matched in a pool are that pool's top vote-getters. It depends on the
name key and moves by up to four a year under another.

| General | House districts | Term-openers | On ballot | Winner | Near-miss | Missing | Senate districts | Term-openers | On ballot | Winner | Near-miss | Missing |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1910 | 0/53 | 96 | 0 | 0 | 0 | 96 | 4/22 | 22 | 4 | 4 | 0 | 18 |
| 1912 | 0/53 | 97 | 0 | 0 | 0 | 97 | 0/21 | 21 | 0 | 0 | 0 | 21 |
| 1914 | 53/53 | 98 | 98 | 98 | 0 | 0 | 21/21 | 21 | 21 | 21 | 0 | 0 |
| 1916 | 53/53 | 97 | 94 | 94 | 2 | 1 | 21/21 | 21 | 21 | 21 | 0 | 0 |
| 1918 | 53/53 | 97 | 96 | 95 | 1 | 0 | 19/23 | 23 | 19 | 19 | 0 | 4 |
| 1920 | 53/53 | 97 | 94 | 91 | 3 | 0 | 20/20 | 20 | 19 | 19 | 1 | 0 |
| 1922 | 53/53 | 97 | 96 | 95 | 1 | 0 | 21/21 | 21 | 20 | 20 | 1 | 0 |
| 1924 | 53/53 | 97 | 96 | 95 | 1 | 0 | 21/21 | 21 | 21 | 21 | 0 | 0 |
| 1926 | 53/53 | 97 | 97 | 97 | 0 | 0 | 21/22 | 23 | 21 | 21 | 0 | 2 |
| 1928 | 53/53 | 97 | 97 | 97 | 0 | 0 | 21/21 | 21 | 21 | 21 | 0 | 0 |
| 1930 | 53/53 | 97 | 97 | 97 | 0 | 0 | 22/22 | 22 | 22 | 22 | 0 | 0 |
| 1932 | 46/46 | 100 | 96 | 93 | 3 | 1 | 36/36 | 36 | 35 | 35 | 1 | 0 |
| 1934 | 46/46 | 98 | 94 | 91 | 3 | 1 | 25/25 | 25 | 22 | 22 | 3 | 0 |
| 1936 | 46/46 | 99 | 95 | 93 | 4 | 0 | 24/24 | 24 | 23 | 23 | 1 | 0 |
| 1938 | 46/46 | 99 | 99 | 99 | 0 | 0 | 22/22 | 22 | 22 | 22 | 0 | 0 |
| 1940 | 46/46 | 99 | 99 | 99 | 0 | 0 | 22/22 | 22 | 22 | 22 | 0 | 0 |
| 1942 | 46/46 | 100 | 98 | 98 | 1 | 1 | 25/25 | 25 | 25 | 25 | 0 | 0 |
| 1944 | 46/46 | 98 | 95 | 93 | 3 | 0 | 23/23 | 23 | 23 | 23 | 0 | 0 |
| 1946 | 46/46 | 99 | 98 | 97 | 1 | 0 | 23/23 | 23 | 22 | 22 | 1 | 0 |
| 1948 | 46/46 | 99 | 99 | 99 | 0 | 0 | 23/23 | 23 | 23 | 23 | 0 | 0 |
| 1950 | 46/46 | 99 | 98 | 97 | 1 | 0 | 23/23 | 23 | 23 | 23 | 0 | 0 |
| 1952 | 46/46 | 99 | 99 | 99 | 0 | 0 | 23/23 | 23 | 23 | 23 | 0 | 0 |
| 1954 | 46/46 | 99 | 99 | 99 | 0 | 0 | 23/23 | 23 | 23 | 23 | 0 | 0 |
| 1956 | 46/46 | 99 | 99 | 99 | 0 | 0 | 23/23 | 23 | 23 | 23 | 0 | 0 |
| 1958 | 49/49 | 98 | 98 | 97 | 0 | 0 | 26/26 | 26 | 26 | 26 | 0 | 0 |
| 1960 | 49/49 | 99 | 99 | 99 | 0 | 0 | 25/25 | 25 | 23 | 23 | 2 | 0 |
| 1962 | 49/49 | 99 | 99 | 99 | 0 | 0 | 25/25 | 25 | 25 | 25 | 0 | 0 |
| 1964 | 49/49 | 98 | 95 | 93 | 3 | 0 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 1966 | 49/49 | 97 | 96 | 96 | 1 | 0 | 25/25 | 25 | 25 | 25 | 0 | 0 |
| 1968 | 49/49 | 98 | 97 | 97 | 1 | 0 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 1970 | 49/49 | 97 | 97 | 97 | 0 | 0 | 24/25 | 25 | 24 | 24 | 0 | 1 |
| 1972 | 49/49 | 98 | 97 | 97 | 1 | 0 | 25/25 | 25 | 25 | 25 | 0 | 0 |
| 1974 | 49/49 | 97 | 96 | 96 | 1 | 0 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 1976 | 49/49 | 94 | 94 | 94 | 0 | 0 | 25/25 | 25 | 25 | 25 | 0 | 0 |
| 1978 | 49/49 | 96 | 96 | 96 | 0 | 0 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 1980 | 49/49 | 97 | 97 | 97 | 0 | 0 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 1982 | 49/49 | 98 | 97 | 97 | 1 | 0 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 1984 | 49/49 | 99 | 98 | 98 | 1 | 0 | 25/25 | 25 | 25 | 25 | 0 | 0 |
| 1986 | 48/48 | 94 | 94 | 94 | 0 | 0 | 23/23 | 23 | 23 | 23 | 0 | 0 |
| 1988 | 49/49 | 96 | 96 | 96 | 0 | 0 | 22/22 | 22 | 22 | 22 | 0 | 0 |
| 1990 | 49/49 | 94 | 93 | 93 | 0 | 1 | 22/22 | 22 | 22 | 22 | 0 | 0 |
| 1992 | 49/49 | 98 | 98 | 98 | 0 | 0 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 1994 | 49/49 | 94 | 93 | 93 | 1 | 0 | 22/22 | 22 | 22 | 22 | 0 | 0 |
| 1996 | 48/49 | 97 | 95 | 95 | 0 | 2 | 24/24 | 24 | 23 | 23 | 1 | 0 |
| 1998 | 49/49 | 96 | 95 | 95 | 1 | 0 | 23/23 | 23 | 21 | 21 | 1 | 1 |
| 2000 | 49/49 | 97 | 97 | 97 | 0 | 0 | 25/25 | 25 | 25 | 25 | 0 | 0 |
| 2002 | 49/49 | 98 | 97 | 97 | 0 | 1 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 2004 | 49/49 | 94 | 94 | 94 | 0 | 0 | 24/24 | 24 | 24 | 24 | 0 | 0 |
| 2006 | 49/49 | 98 | 98 | 98 | 0 | 0 | 23/23 | 23 | 23 | 23 | 0 | 0 |
