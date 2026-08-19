# Modules — usa-wa-common (Layer 2b)

Washington-State legislative **vocabulary**, source-free. Created by #189 (AR-14) as the home
for code that is a fact about Washington's legislature rather than about any one publisher of
data about it.

Before it existed, each of these lived in whichever adapter first needed it — the election
calendar inside the PDC SODA adapter, seat keying inside the PDC normalizer, party
canonicalization inside the WSL SOAP normalizer — so **importing a peer adapter was the only
way to reuse them**. `usa-wa-adapter-sos` alone reached into `usa-wa-adapter-pdc` from five
modules purely to key a seat and fold a name.

**Layering rules.** May import Layer 1 (`clearinghouse-core`) and Layer 2
(`clearinghouse-domain-legislative`). May **not** import a `usa-wa-adapter-*`, a
`usa-wa-facts-*` or a deployment package, and speaks no wire. Enforced by the import-linter
contract in the root `pyproject.toml` (`layers` + the `usa-wa-common is source-free` contract).

```
packages/
  usa-wa-common/                      — Layer 2b: WA legislative vocabulary, source-free
    src/usa_wa_common/
      jurisdiction.py — `resolve_jurisdiction` + `JURISDICTION_SLUG`: the pre-seeded `usa-wa` Jurisdiction every runner path needs before it can drive an `AdapterRunner`. Was `usa_wa_adapter_legislature.provisioning`, beside the get-or-create of the **WSL SOAP Source** (which genuinely is that adapter's) — so the PDC harvest and both SOS harvests, all pure *sourcing* modules, imported a SOAP adapter for a row that has nothing to do with SOAP. Five of the workspace's cross-adapter file edges were this one function
      elections.py    — the WA general-election calendar: `election_year_for_biennium` (the even November that seated a biennium's House), `election_years_for_biennium` (#106 — every general a biennium's membership can be decided by: the even seating cycle **plus** the odd mid-biennium special; `start+1` excluded, it seats the next biennium), `seating_biennium_for_election_year` (#121 — even → next odd biennium, odd → the biennium *starting* that year, so it is deliberately not the strict inverse), `senate_election_years_for_biennium` (#75/#121 — staggered 4-year terms mean the sitting union is `start-1` ∪ `start-3` ∪ the odd special). Was `usa_wa_adapter_pdc.adapter`; four `usa-wa-adapter-sos` modules imported the PDC SODA client module to reach it
      seats.py        — WA legislative seat keying: `district_number` / `ld_slug` (LD parsing → the `usa-wa-ld-<n>` jurisdiction slug), `canonical_position` (`"1"`/`"2"` → the PM `qualifier` `"Position 1"`/`"Position 2"`, power-map#263; anything else → `None`, never a guessed seat), `house_seat_role_source_id` (deterministic seat-Role `source_id`, 1:1 with PM's seat match key), `house_span_discriminator` / `parse_house_span_discriminator` (the colon-free `ld-5-position-1` tenure-span discriminator + its inverse, so the 4-part span `source_id` stays parseable). Was split across `usa_wa_adapter_pdc.normalize.positions` and `usa_wa_adapter_legislature.normalize.members`
      names.py        — `fold_token` (casefold + unaccent + strip non-alphanumerics), `folded_tokens` (the ordered *atomic* tokens; public since #240, whose given-name guard needs them rather than the concatenations — re-deriving the split at the call site would fork the folding rule this module exists to single-source) and `surname_match_set` (atomic folded tokens **plus every consecutive-run concatenation**, so a space-split upstream name like `Van De Wege` matches the WSL side's joined `vandewege`). The messy half of every cross-source person match here; a divergence between two copies would silently mismatch people rather than error, which is why it is one implementation. Folding stays local — a package below the adapters must not import the Layer-4 sidecar's `normalize_name`
      parties.py      — `canonicalize_party` (WSL `Party`, either endpoint encoding: `R`/`Republican` → `republican`) and `sos_party_slug` (the SOS ballot `(Prefers X Party)` form, plus the audited `GOP` synonym). One slug vocabulary because the slug is what PM's `org_wa_party` identifier carries (power-map#270) and what the party-Org synthesis keys on. **No Independent slug** — independent is the absence of a party assignment, so unrecognised/blank → `None`. **Since #227 the module has a second entry point for a second input domain**: `resolve_party_token(token, *, year)` folds the **roster PDF's** seven historical abbreviations (166 member-year records back to 1891) to the six power-map#443 Orgs, and returns a `PartyResolution` carrying a **disposition** rather than `str | None`. The disposition is the point: `None` was overloaded three ways — deliberately unaffiliated, not a party at all, and *nobody has classified this token* — and collapsing the third into the first is exactly how those 166 records would have vanished on a run that reported success. `resolved` (slug) / `declined` (a decision: unaffiliated, `Cit.` as a hyper-local ballot label rather than an organisation, or a year outside the Org's lifespan) / `unrecognized` (must be tallied — a future edition introducing a new abbreviation has to fail loudly). `Prog.` is **year-scoped**, not a flat lookup: the Org is Roosevelt's Bull Moose formation (1913–1917) and the roster's lone 1927 row is Knute Hill, seated a decade after it dissolved, so a flat map would assert a membership that never existed (power-map#442). `year` is keyword-only and **required** so it cannot be omitted by accident; `None` is an explicit statement of ignorance and surfaces as `unrecognized`/`year_required`. `tally_party_tokens` is the census — three counters that sum to the input by construction, so "we emitted fewer spans than there were records" is arithmetic, not inference. **`canonicalize_party` is deliberately NOT widened** (against #227's own wording): `sos_party_slug` splits its input on whitespace and punctuation before folding each piece, so the historical `S` → socialist mapping in the shared table would make a stray `S` anywhere in a ballot string resolve to Socialist. The hazard is one-directional, so the vocabularies are too
      ballot.py       — the source-agnostic ballot interfaces: `HousePosition` (ballot `qualifier` + folded `name_keys` + `party_slug`), `SenateWinner` (#106 A′ — attestation, not structure: no `qualifier`, one seat per LD), `position_for` (the within-LD lookup resolving a WSL member's folded surname + party to their ballot Position; zero-or-ambiguous → `None`, never guessed), and **`HousePositionCohortProvider`** — the Protocol a fact package depends on instead of a concrete SOS provider class. See § The seam below
```

## The seam

`docs/ARCHITECTURE.md` has always claimed the House-position application "depends on a cohort
interface …, not on a concrete source", and that which SOS archive supplies the Position "is
the provider's concern, not the builder's". #189 found that claim **false in two ways**:

1. There was no interface. All seven cohort providers in the workspace were duck-typed with no
   shared `Protocol` or ABC, so a composing module's only way to state its requirement was to
   name a concrete class — and therefore the adapter package that class lived in.
2. The two providers the doc presents as interchangeable were **not substitutable**.
   `SosResultsCohortProvider` exposed `house_positions`; `SosFilingCohortProvider` exposed
   `house_filings`. The row type was identical (`HouseFiling` is a module-level alias of
   `HousePosition`) — only the method name differed, and nothing tested that they agreed.

`HousePositionCohortProvider` is that sentence in code, and
`scripts/tests/test_cohort_seam.py` is the conformance test that keeps it true. The generic,
jurisdiction-free half of the seam lives in `clearinghouse_domain_legislative.cohorts`
(`AttestedCohortProvider`, `BienniumCohortProvider`, `ArchivedBienniumCohortProvider`).
