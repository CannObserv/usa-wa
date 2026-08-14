# WSL roster PDF — the 1889–2025 archival member source

- **Date:** 2026-08-14
- **Status:** proposed
- **Scope:** Adopt the Legislature's own *Members of the Legislature 1889–2025* PDF as a second **source** on the `usa-wa-adapter-legislature` target: a frozen archival roster that extends member coverage back **102 years** below the WSL SOAP floor and supplies the **exact mid-term succession dates** every live wire structurally lacks. Three consumers, phased by ascending write-risk: audit oracle → operator-event backfill → pre-1991 span backfill.
- **Issues:** epic #219; related #107 (operator events — the backfill target), #119 (sub-biennium duplicate occupancy — the accuracy defect this closes), #118 (Position back-chain — this extends its reach), #144 (member artifacts — this is the oracle already used ad hoc), #101/#103 (House Position seat), #180 (coverage claims), power-map#302 (**open** — pre-1965 at-large seat modeling).

## Problem

Member history bottoms out hard, and mid-term boundaries are wrong.

Live coverage, measured 2026-08-14:

| | |
|---|---|
| Persons (`usa_wa_legislature`) | 641 |
| Assignments | 5,153 — committee 3,904, party 679, `chamber-house` 329, `chamber-senate` 241 |
| Earliest `valid_from` | **1991-01-01** (persons, party, Senate) |
| Earliest House Position | **2003-01-01** |
| Committee floor | **1999-00** |

Two independent defects:

1. **No history before 1991.** `GetSponsors` floors at 1991-92 (#77); SOS results at 2008, back-chained to 2003-04, with 1991–2001 explicitly unreachable across the 2002 map break (#140). WA has had a Legislature since **1889**. 102 years are simply absent.
2. **Every mid-biennium boundary is quantized to the biennium floor.** `build_tenure_spans` snaps `valid_from` to Jan 1 of the odd year. #107 built the operator-event facility to interject the real dates, but it is hand-fed one event at a time — so the ~57 historical sub-biennium sequential occupancies #119 surfaced have no source to be corrected *from*.

Worked case — LD2 Position 1, our record vs. the PDF:

| Fact (PDF) | Our span | Defect |
|---|---|---|
| Alexander resigned **Dec 31, 2013** | `2157:…ld-2-position-1:2013-14`, `2013-01-01 → 2014-12-31` | End 12 months late |
| Hunt appointed **Jan 17, 2014**, sworn **Jan 18** | `18517:…:2015-16`, `2015-01-01 → 2016-12-31` | Start ~12 months late |
| Hunt resigned **Feb 2, 2016** | (same) | End ~11 months late |
| Barkis appointed **Feb 16, 2016** | `24075:…:2017-18`, `2017-01-01 →` open | Start ~11 months late |

Four boundaries, four wrong, one district, one seat. The dates exist, published by the Secretary of the Senate and the Chief Clerk, and we do not read them.

## The source, measured

`https://leg.wa.gov/media/s4gf4suc/members-of-the-legislature-1889-2025.pdf`

233pp, Word → Print-to-PDF, **intact text layer — not a scan**. `Revision Date: June 5, 2025`. Published by Sarah Bannister (Secretary of the Senate) + Bernard C. Dean (Chief Clerk); publication lineage documented in-document back to 1962, 18 revisions listed. sha256 `275a1a92d8466dc89b9b75a8f92b711911d0c73a8f590e8a3d65d7868ea83d22`.

Contents (column-aware parse of the district section, prototype run 2026-08-14):

| | |
|---|---|
| member-year records | **8,485** — House 5,725 / Senate 2,760 |
| span | 1889 – 2025, 103 session years, 49 current LDs (53 distinct district numbers historically) |
| party vocabulary | R 4,411 · D 3,539 · P.P. 53 · Pop. 50 · Prog. 46 · Silver Rep. 11 · F.L. 7 · Cit. 1 |
| dated annotations | Appointed 461 · unexpired 337 · Resigned 325 · Elected 265 · **Speaker 158** · Deceased 136 · **Changed party 22** · Redistricted 16 · Expelled 1 |
| other sections | session dates (p1) · party division per session (p6) · Senate officers (p149) · House officers (p159) · redistricting history (p168) · maps (p173, images) · name index (p197) |
| name index | `Name* … H-39` / `S-34, H-34` — chamber+district only, **no years**; 260 entries carry multiple seats (chamber/LD moves) |

**It carries no House Position numbers.** `Position` appears twice in 233 pages, both about staff appointments; `Pos. 1` appears once, inside a prose annotation. This is the source's one structural gap and it drives the design decision below.

## Coverage and accuracy delta

**Coverage:** 1889–1990 is 102 years we hold zero rows for. It is also the only source for `role_type = leadership` — the vocab exists in [identity.py](../../packages/clearinghouse-domain-legislative/src/clearinghouse_domain_legislative/identity.py) and the roles table holds **0** such rows; the two officers sections plus 158 inline `(Speaker)` annotations would be the first data. The session-dates table extends the term calendar below its floor, and the per-session party-division counts give a chamber-count invariant for every session 1889→2025 (backdating [operators/invariants.py](../../packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/operators/invariants.py), which today can only assert 49/98 against the *open* cohort).

**Accuracy:** 461 appointments, 325 resignations, 136 deaths — all dated. Plus 22 dated party switches, which `GetSponsors` cannot express at all (one party per biennium; e.g. LD2 Tom Campbell, "Changed party affiliation January 31, 1995").

**What it does not carry:** committees, bills, candidacies, vote counts, contact data. It is a roster, not a wire.

## Design decisions

| Axis | Decision |
|---|---|
| Placement | New **source subpackage** `roster_pdf/` inside `usa-wa-adapter-legislature` — same jurisdiction+target, second source. The SOS `filings` + `results` split is the worked precedent (ARCHITECTURE.md); the package already splits on the archive axis (#183). |
| Source slug | `usa_wa_legislature_roster`, archival retention, its own `Source` row + `coverage.py` claims (`member_roster` **verified** 1889–2025). |
| Archive key | `legroster:<revision-date>` (e.g. `legroster:2025-06-05`) — the in-document revision date is the natural version key; sha256 stamped on the `FetchEvent` for change detection. |
| Extraction | **`pdfplumber`** as a real `uv` dependency — *not* shelling out to poppler's `pdftotext`. Units run `uv run --frozen --no-sync`, so a system binary is invisible to `uv.lock` and would break silently on a fresh VM. Word bounding boxes are required (see below), which rules out `pypdf` text-only extraction. |
| Phase split | Phase A archives the 5.7MB bytes; Phase B parses **offline** from `RawPayload`. The parser will need revision; re-running it must never re-fetch. |
| Cadence | **Quarterly**, never in the daily refresh. Not a systemd timer initially — a documented backfill CLI plus a change-detection probe. |
| Authority | **Closed biennia below the WSL floor only.** Operator events + WSL/SOS win for the current biennium, always. |

### Why the y-coordinate join is non-negotiable

The year gutter is a **separate text block** from the member-name block, and each district page is **two columns**. `pdftotext -layout` interleaves the columns into single lines and drops the year association — a naive line parse recovers 464 of 8,485 records (5%). Rows must be reassembled by y-coordinate within an x-bounded column. This is why the extractor needs bboxes, and it is the single largest implementation cost in the spec.

Two further extraction hazards:

- **Bold years mark a redistricting/reapportionment plan** and bold is lost in plain text extraction. Recover from font weight via `pdfplumber` char attributes, or cross-reference the redistricting-history section (p168). Do not infer.
- **Annotations wrap across lines** and a continuation line can itself end in dots + a party letter, so it parses as a spurious member row. The prototype leaked several (`"Served in Pos. 1 until December 7, 2012)"`). Continuation joining must happen before row classification.

### Name identity

The PDF has **no stable id**. WSL has one — verified stable 1991→2025 with 0 re-keys (#81). So:

- **1991+**: resolve by fuzzy `(last name, LD, chamber, session year)` against the 641 known Persons. Match rate must be gated and reported, never silently dropped.
- **1889–1990**: name identity only. Honorifics, initials-only, nicknames and marital forms are all present — `Dr. C. G. Brown`, `Margaret (Mrs. Joseph E.) Hurley`, `Frank "Buster" Brouillet`, `Robert "Bob" McCaslin`. The two-Bob-McCaslins case (Sr./Jr., already keyed on `Person.id` not display name in `member_duplicate_detail`) is the shape of the failure mode.

Follow the standing rule: **never key a parser on an exact upstream string.** Ninety years of clerks' phrasings; parse permissively, tally and report unparsed rows, never drop them silently.

## House Position — decision (b), with guardrails

The source carries no Position numbers, but **row order within a district-year is seat-lineage order**, with a seat's mid-term successors grouped under their predecessor.

Validated against our SOS-derived truth, 2003–2025:

| | |
|---|---|
| district-years compared | **523** |
| PDF row order == SOS Position order | **487 (93.1%)** |
| **inverted** | **0** |
| unresolved | 36 |

Zero inversions across 523 district-years is the load-bearing result. The 36 unresolved are attributable, on inspection, to causes outside the source: mid-biennium succession where the PDF names the seat's *first* occupant and our span holds the later one (LD1 2019 Stanford→Duerr), documented ballot↔roster **name changes** (LD14 McCabe→Mosbrucker, the same class `house/projector.py` already heals via #103 elimination), and prototype-parser artifacts (annotation-continuation leakage, nickname forms). Ordering is also demonstrably **not** alphabetical — LD2 2005 lists McCune before Campbell.

**Decision: (b) — emit post-1965 House Positions inferred from PDF ordering, anchored to SOS ballot truth at 2008+ and carried backward.** This is the same seat-assertion shape as the #118 back-chain, from a stronger signal: `backchain.py` is capped at `MAX_BACKCHAIN_HOPS_DEFAULT = 4` and dies at the 2002 map break, whereas the PDF ordering is attested continuously to 1965.

Guardrails, all mandatory:

1. **Anchor, don't guess.** Every inferred lineage must chain to a ballot-attested Position at its most recent end. An LD-era with no reachable SOS anchor emits **no** Position.
2. **Ballot always wins** over a conflicting inference — the existing `seed_positions` precedence in [projector.py](../../packages/usa-wa-facts-seats/src/usa_wa_facts_seats/house/projector.py).
3. **Feed the existing seam.** PDF ordering produces seeds of exactly the `seed_positions` shape, consumed by `usa_wa_facts_seats.house`. No new emission path; no new span identity.
4. **Track inference.** Inferred pairs join `inferred_keys` and cite the PDF archive, per the #103/#118 precedent. An inferred Position must be distinguishable from a ballot-attested one forever.
5. **Redistricting breaks the chain.** `REDISTRICTING_ERA_START_BIENNIA` already encodes this; WA keeps LD numbers across remaps, so the break must stay explicit.
6. **Hard floor at 1965.** See below.

### Pre-1965 is out of scope — power-map#302 remains open

Before 1965 WA House seats were **fungible at-large**: no Position existed. The PDF's two-unnumbered-names-per-district is, for that era, the *correct and complete* fact — not a gap. Emitting a Position there would be fabrication, and emitting a position-less `state_representative` would collide with the convention that a missing Position means the pre-1965 at-large seat.

**power-map#302 (at-large seat modeling) is open and blocks the pre-1965 House seat entirely.** Until it lands: harvest and archive pre-1965 House rows, emit Persons and party spans from them, emit **no House seat assignment**. The 1965 floor is a hard gate in the builder, not a convention.

## Phasing

Ascending write-risk. Each phase ships independently and is separately valuable.

**Phase 1 — source + audit oracle (read-only).** `roster_pdf/` transport, adapter, `coverage.py`, harvest (Phase A), normalize + cohort (Phase B). No writes to spans. Consumers: cross-check `operators/invariants.py --sweep-biennia`, validate the `sponsors/artifacts.py` denylist, backdate the chamber-count invariant from the party-division table. **Acceptance oracle:** independently reproduces the resolved #144 cases (Wynne LD39 artifact; Marlo Braun LD20 genuine) with no hand-curation. Zero write risk; pays for the parser on its own.

**Phase 2 — operator-event backfill.** Emit dated `seated`/`vacated`/`departed` events into the #107 store from the 922 dated annotations. #119's sub-biennium collapse then resolves through machinery that already exists. **Note the #119 synthesize guard**: an out-of-biennium `seated` with no built span is skipped by design (`operator_seated_no_span_out_of_biennium`), so this requires the unrestricted backfill run, exactly as documented in `operator_overlay.py`. **Acceptance oracle:** LD2 Position 1 — Alexander/Hunt/Barkis land on their four real dates. Run sidecar-paused; PM keys on `(person, role, start_date)`, so a start-date move is a PM re-anchor.

**Phase 3 — pre-1991 backfill.** Persons + party + chamber spans, 1889–1990, plus House Positions 1965+ under the (b) guardrails and no House seat pre-1965. Biggest payoff (roughly a 5× increase in member-year coverage), biggest identity risk. Gate on Phase 1's reported match rate.

**Phase 4 (deferred) — leadership.** Officers sections + 158 `(Speaker)` annotations → the first `role_type = leadership` rows. Deferred because it needs a PM role-shape decision, not because it is hard.

## Cadence and change detection

The strongest operational argument for this source: **it is frozen.** A 1943 row will never change. The only mutable tail is the current biennium — and the document *lags* it (stamped June 2025, covering through the 2025 session; 2026 successions will not appear until the 2027 revision).

Revision history in-document: 1962, 66, 78, 87, 91, 97, 99, 2001, 05, 08, 09, 11, 14, 18, 19, 20, 23, 25 — recently ~biennial with off-year online updates. **≈1 change per 12–24 months.**

Contrast the daily timers (06:45/07:00/07:05/07:15 UTC): those exist because the *current* cohort mutates and a resignation today must surface today. None of that applies here. Therefore:

- **Harvest once; re-check quarterly**, or annually just after sine die plus the biennial revision. **Never in the daily refresh** — it is a backfill and audit oracle, not a refresh source.
- **Change detection is a full GET + sha256.** The URL returns **no `ETag`, no `Last-Modified`, no `Cache-Control`** (verified 2026-08-14, Microsoft-IIS/10.0). Conditional GET is unavailable. At quarterly cadence that is ~23MB/yr — trivial.
- **URL durability is the real fragility, not the content.** `s4gf4suc` is an opaque CMS media key on leg.wa.gov's current CMS; a re-publish will likely mint a new one. The probe must treat a 404 as *needs re-discovery*, not as an outage — discover the href from the Legislative Information Center page, the same `export.html` traversal pattern `results/transport.py` already uses for SOS filenames. Falling back to a hard-coded URL that 404s forever is the failure mode to avoid.
- **Never authority for the current biennium.** Precedence: PDF wins for closed biennia below the WSL floor; operator events and WSL/SOS win at and above it.

## Risks

| Risk | Mitigation |
|---|---|
| Parser complexity — two columns, split year gutter, wrapped annotations, ~90 years of phrasings | Phase A/B split so the parser re-runs offline; report-don't-drop on unparsed rows; Phase 1 is read-only |
| Name resolution without stable ids, esp. pre-1991 | Gate Phase 3 on Phase 1's measured match rate; keyed on `Person.id` throughout; surface ambiguities rather than merging |
| Inferred Positions asserting false structural seats | Anchor-or-abstain, ballot-wins, `inferred_keys` tracking, explicit redistricting breaks, 1965 hard floor |
| PM anchor churn on start-date moves (#80) | Phases 2 and 3 run sidecar-paused, in the same window as the migrate, before anything drains |
| URL rot | Discovery step + 404-means-rediscover |
| Bold-year (redistricting) signal lost in extraction | Font-attribute extraction or cross-reference p168; do not infer |

## Open questions

1. Should Phase 1's oracle run as a **timer** (weekly, report-only) or stay ad-hoc? Leaning ad-hoc — closed history does not drift, so a timer would burn cycles to re-derive a constant.
2. Citation granularity: one archive key per revision means a 1943 row cites `legroster:2025-06-05`. Acceptable, or does the citation need a synthetic per-(district, year) target?
3. Does the pre-1965 party-span emission need power-map#302 too, or only the seat? Reading is: party is a `party_member` role, orthogonal to seat shape, so it is unblocked. Confirm before Phase 3.
