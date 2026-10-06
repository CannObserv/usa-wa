# The conformed tier

The registry-joined half of the #302 pipeline (`packages/usa-wa-pipeline/dbt/
models/conformed/`): stateless joins against the identity registry that turn
staging rows into the published products. Split out of
[`PIPELINE.md`](PIPELINE.md) — which keeps layout, commands, the raw tier,
staging, the registry and publication — because these three sections carry the
guards, and each guard encodes a production incident worth reading before
touching the model it protects.
The first of them, crosswalks + entities, is split out again
into [`PIPELINE-CONFORMED-ENTITIES.md`](PIPELINE-CONFORMED-ENTITIES.md).

## Conformed: tenure spans (#309 part 2)

`models/conformed/assignments.py` is a thin binder over
`usa_wa_pipeline.conformed.spans` — merged tenure spans for all four kinds
(`party`, `chamber-senate`, `committee`, `chamber-house`), joined to the person
crosswalk. The span `source_id`'s parts become real columns
(`span_kind` / `span_discriminator` / `span_start_biennium`), retiring the
string-splitting workaround `docs/API.md` documents.

**Two families, one table, disjoint identity spaces.** The WSL archive keys on
numeric member ids from 1991; the roster PDF keys on minted
`<fold>:<first-session-year>` identities before it. `source` names which space
a row's `member_id` belongs to, and the crosswalk lookup is
`<source>:<member_id>` for both — a row must never inherit a module default.
The roster family is the retired tier's pre-1991 builder minus everything that
existed to mutate Postgres (minting Persons, retiring unasserted rows, the
anchor bootstrap, citation writes); what it keeps is the operator overlay
scoped to its own members — every pre-1991 span is this
builder's, so the roster's 922 dated mid-term boundaries take effect here or
nowhere (#226) — and the unattested-span check, which refuses a seat the
overlay synthesized from an event the edition never listed. The scoping runs
the other way too (#460): `build_families` hands `build_all_spans` the minted
ids as `roster_members`, whose events never reach the WSL overlays — they could
match nothing there, and each logged miss was noise in
`operator_seated_no_span_out_of_biennium` (54 of the 90 seatings #282 measured).
An id no family holds still reaches them, so a typo's miss is still reported.
Within the WSL family a `departed` miss is the family's, not each overlay's
(#466): the three overlays share one `applied_departures` tally, and
`build_all_spans` logs `operator_departed_no_open_span` only for an event none
applied — or `operator_departed_already_closed` for a term end the wire had
already closed.

**One resolve feeds both.** `roster_resolution()` runs the ~8,600-record
identity resolve once and partitions by disposition: WSL-joined observations
deepen the sponsor build (#228), minted ones are the roster family. Resolving
twice would double the cost and let the halves disagree about who is joined.
The acceptance oracle (`roster_pdf.oracle.verify_pre1991` — partition
exactness, person-side Senate simultaneity — plus the party vocabulary) runs
before anything is built.

Nothing about the span engine is re-implemented. The pure engine
(`build_tenure_spans`, `apply_operator_events`), the projections, the #105
roster hygiene, the #145 biennium-scoped exemption and the #144 artifact
denylist are all **imported unchanged** and applied in the order the retired
Postgres tier settled — each encodes a production incident. Two structural
properties:

- **No DB half.** A stateless transform recomputes everything, so a span the
  archive stops asserting is simply absent (retraction-as-absence, the
  publication contract) — no stale-span sweep (#83), no anchor bootstrap.
- **Context spans come from the same run** (#267): committee spans build first,
  then House, and both serve as the sponsor build's context — no cross-builder
  blindness and no DB read. With `chamber-house` landed the seam is complete:
  Liz Pike's 2,190-day party gap, the incident #267 is named for, is exactly a
  member who returned only to a House seat.

**The House Position seat** (`conformed/house.py`) is the Layer-3b composition
the other families do not need: WSL owns *who sits* (the sponsor roster — LD +
party), SOS owns *which position* (the ballot's Position 1/2). The #105 (a)
mover exclusion, the #123 even-seating ∪ odd-special-**winners** map, the #118
back-chain and the #103 within-LD elimination are imported unchanged from
`usa_wa_facts_seats`. A stateless rebuild is unconditionally the unrestricted,
deep one, so the #100 depth-mismatch question cannot arise here at all.

**A biennium after the current one builds no span** (#135).
`build_families` first drops WSL sponsor and committee-member rows from any
biennium after `spans.current_biennium()` (`without_future_bienniums`, logged
`spans_future_bienniums_excluded`). Staging stages every wire the raw root holds,
and a run ending past the current biennium reads as closed: one early
`sponsors:2027-28` wire would close every returning member at 2028-12-31 and
publish newcomers' spans before they start. **Spans only:** the registrar
(`load_sponsor_keys`), `persons`, `organizations` and `citations` (`newest_biennium`)
still read every staged biennium, so a WSL lookahead must settle those first.
The rollover itself needs no switch:
on Jan 1 the clock flips, every 2025-26 span closes at 2026-12-31 (the
`assignments_chamber_vacancy` warning), and the new roster reopens them.

**Roles and seats are structural, not registered** (`conformed/roles.py`). A
Role is a named slot in an Organization; an Assignment binds one in time
(ONTOLOGY.md § 2). The span already carries the slot's identity as
`(span_kind, span_discriminator)`, so the `role_key` is a pure function of it —
`seat:house:ld-5:position-1`, `party-role:democratic`,
`committee-member-role:28240`, `seat:senate:ld-22` — identical on every run and
the key a subscriber matches a seat on. No ULID mediates it; only the
*organization* the slot belongs to is registry-joined. Every key function is
imported unchanged from the adapter's normalizer and the WA vocabulary.

**#313 adds a role's own `entity_id`** without disturbing that. The key is still
structural and still what PM matches on; the ULID is a stable handle for the API
to address, minted through the registry's third kind
([`PIPELINE.md` § Identity registry](PIPELINE.md#identity-registry-308)),
seeded once from the retired tier's role ULIDs so each role kept the id it was
already published under. Neither crosswalk may drop a role: a seat exists
whether or not the registry has reached it, and the nightly runs `dbt build →
registrar → publish`, so a brand-new seat is unregistered in the build that
first sees it and bound by the next. `registry_coverage` gates
`unregistered_roles` and `unregistered_orgs` *after* the registrar, so that
one-run latency reads as zero and only a gap it left open alarms. A
brand-new org has the same latency without a counter: `organizations` is one
row per registered entity, so the committee is absent from the build that
first sees it and published by the next (Joint committee 36500: first seen
2026-09-22).

**An assignment names itself with `span_key`** (usa-wa#370): the five
structural fields — `entity_id | role_key | span_kind | span_discriminator |
span_start_biennium` — as one string, unique across the set (8,395/8,395 on
2026-09-11). It exists because power-map#490's applier measures
retraction-as-absence in the dataset's key space and an assignment has no id
of its own, so the crosswalk PM seeds from had nothing to join to. The
separator is `|`, not `:`, because `role_key` already carries colons; a value
containing it is **refused** rather than escaped, so the day a vocabulary
needs the character the build fails here instead of two tuples quietly
serializing alike.

It began as one half of a pair — `pm_anchors` copied the column rather than
recomputing it, so the two sides could not disagree about how five fields
become one string — and #314 retired that crosswalk once power-map#525 re-keyed
onto this column. What was the cutover's safety property is now simply PM's
only handle on an assignment row.

A deterministic join that has forked is the one failure this design cannot
tolerate — but the dbt `assignments_name_a_role` test does **not** detect it
(CR 77). `roles` is generated by iterating `assignments` through the same
`role_for_span`, so `assignments.role_key ⊆ roles.role_key` holds by
construction and that query is unfalsifiable; it pins containment, which is
worth pinning, and nothing more. The fork that can actually happen is a key or
attribute drifting from what consumers already matched on — and the one
production instance changed no key at all: #110 churned 305 party roles on
local `member` against PM's `party_member`. Until #412 that was diffed against
the retired tier's roles (312/312, keys and attributes, 0 mismatches). Nothing
recomputes them independently now: the key functions are imported unchanged
from `role_keys` and `usa_wa_common.seats`, so a fork needs an edit there, and
`roles.role_type`'s `accepted_values` test refuses a new classification.

Each family's input carries a **refusal**, on one rule: an input whose absence
silently deletes facts must refuse, not return empty (CR 57). The roster tier
for the #228 deepening, the SOS ballot for the House seat — chamber-house is
~4% of the table, inside the publish gate's 10% shrink floor, so its
disappearance is exactly the kind nothing downstream would catch. Both have an
explicit seam (`extra_observations`, `house_spans`) for stating the family
rather than deriving it.

Two curated Postgres inputs, both read through explicit seams and both empty
only under `USA_WA_PIPELINE_HERMETIC=1`: the registry crosswalk
(`registry_read`) and the operator succession events (`operator_read`). The
event read orders by `(effective_date, id)`: the overlay sorts **stably**, so
input order settles same-date ties (prod holds seven such pairs) and a
content-hashed dataset cannot inherit Postgres's unspecified order.

**The #228 deepening is a standing input, not an enrichment.** An empty roster
under a live sponsor corpus is *refused*, because the failure is invisible
downstream: the key set shifts to shallow 1991-start spans while the row count
barely moves, so the publish shrink gate sees nothing and every probe runs only
afterward. Both routes to an empty deepening are refused — an empty roster
tier, and a roster tier present but parsing to zero records (an upstream
rename) — and the refusal lives on **`roster_resolution`**, not only on
`build_all_spans` (CR 76). That distinction is the whole point: `build_all_spans`
raises only when `extra_observations is None`, and neither production caller
passes `None` — the `assignments` model and `registry_coverage` both hand it
`roster_resolution(...).joined`, so the resolve runs once for two families. The
guard therefore sat on a door production never opens. It now sits on the resolve,
which is the door they use. Pass `extra_observations` — `[]` included — to state
the deepening rather than derive it; an empty *corpus* (no sponsors, the
hermetic build) still resolves to an empty partition without complaint.

**Python models cannot log.** A `dbt build` never calls `configure_logging()`,
so a `get_logger()` call inside a model emits nothing — the info path is
dropped and the warning path reaches `logging.lastResort`, which prints the
message and discards `extra`. Counters that must reach an operator therefore
belong in a job, not a model: `registry_coverage` (#412 PR B) recomputes the
crosswalk join and reports it under the harness, where records serialize as JSON.

**Reported is not enough — counters are gated at zero.** The nightly's
`OnFailure=` alerting fires on the *exit code*, so a counter that only reaches
journald tells nobody while the job passes. **`registry_coverage`** carries
them: `unregistered_spans` (a registrar gap silently shrinking the published
table), `unregistered_orgs` (the same gap in the role dimension — a role whose
org is unregistered still publishes, by design, so nothing else notices it
going headless) and `unregistered_roles`, each naming itself in
`integrity_failures` when nonzero. `seat_overlaps_unclipped` (#360) rides
along, reported. Partial roster corruption quietly degrading the #228
deepening is gated in-build instead, by `stg_roster_members`' `not_null` tests.

**Post-registrar, never in-build.** The three `unregistered_*` counters cannot
be dbt tests: a new legislator, seat or committee is unregistered in the first
build that sees it, by design, and a failed build never reaches the registrar
that would register it — so every night after would fail the same way.

`unregistered_orgs` reaches the probe rather than the model for the reason
above, and the role keys are derived there from **every span**, not from the
crosswalk-joined rows: a slot exists whether or not the person filling it is
registered, so reading the joined rows would let a person gap hide the roles
only that person's spans name (CR 86).

The probe's crosswalk read is **not** the read the model made: the nightly runs
`dbt build → registrar → publish → probes`, so the registrar may have bound
keys in between. Its staging read **is** the model's: `registry_coverage`
rebuilds the families from the built duckdb's own staging tables, not the raw
store. `registered_spans` therefore describes the registry as it
stands *now* — the state tomorrow's build publishes from, which is the gap
worth alarming on. A gap the registrar has since closed is transient and
correctly reads as zero.

## Conformed: the citations chain (#313)

`models/conformed/citations.py` answers *how do we know this?* for every
published entity, as a **stateless join** rather than the append-only Postgres
`Citation` ledger it replaced — so a citation the archive no longer supports
stops being emitted, exactly as a span the archive no longer asserts stops being
published. It is the **internal** tier: published bytes, immutable versions, the
same `/datasets` tree, but no subscriber contract and no schema-stability
promise, because its columns follow `/provenance`, not consumers.

One row per `(entity_type, entity_id, source, resource_id)`; the digest, fetch
time and URL are one join away in `stg_raw_fetches`, not duplicated onto every
row. `entity_id` is a registry
ULID for `person`/`organization`/`role`, and the **4-part span `source_id`** for
`assignment` — the serving tier keys assignments structurally, so a span's
published identity is its key.

Per kind: a person is cited by every staging row carrying one of its natural
keys (merge tombstones followed — a citation into a retired entity is a dangling
one); an organization by the committee-roster, membership and meeting wires
naming it; an assignment **once per biennium it covers**, the rule the Postgres
emitter applied, moved from emit time to build time; a
role by the union of its assignments' citations, since `role_for_span` is a pure
function of the seat and there is no staging row to cite.

Two departures from a naive biennium join, both measured rather than assumed. A
**roster** span is cited with no year filter: the §5 truncation bound derives a
term from the *next* listing on a seat, so a span's bienniums routinely exclude
the listing that attests it (Gary M. Odegaard's 1987-88 Senate span rests on a
1985 listing), and filtering dropped 49 spans to zero citations. A **WSL-family**
span starting before the archive's own earliest biennium — the floor read off
the sponsor corpus, not hardcoded as 1991 — falls back to the roster, by the
member's registered fold where one exists and otherwise at every roster wire:
the fold that deepened such a span is the resolver's, and the roster↔WSL link
rule only proposes folds with a 1991+ listing, so a member who left before then
has no roster key at all. Staging keeps one revision, so that is one document.

**One stated gap.** The SOS corroboration tier is not per-entity addressable —
its rows carry ballot names and races, never a member id — so a House-Position
span corroborated by SOS is cited at its WSL evidence only.

```bash
uv run python -m usa_wa_pipeline.parity_citations   # last in the nightly probe loop
```

The probe asks the **built artifact**, not a recomputation: that is the only
check that catches a binder which dropped an input the pure function handles
fine. Gated at zero — `orphan_citations` (a citation naming a resource
`stg_raw_fetches` does not carry), `uncited_assignments`, `uncited_roles` and
`uncited_organizations`.

`uncited_roles` counts **registered** roles only. `roles.entity_id` is null for
exactly one build — the nightly runs `dbt build → registrar → publish`, so a
brand-new seat is unregistered in the build that first sees it — and a role with
no ULID has nothing to be cited *by*. Gating that at zero would have failed the
nightly and emailed the operator every time a committee was created. Those roles
are counted apart as `unregistered_roles` and reported, not gated; the
**persistent** case is caught by `registry_coverage`, which re-reads the registry
after the registrar rather than the artifact built before it. Ratcheted — `uncited_persons`, baseline **2**: the two Elmer E.
Johnstons sharing the fold `elmerejohnston`, which the citer refuses to guess
between. Was 3 — the registered WSL member no wire names left the count when
#366 merged him with the roster entity that cites him. Counted only —
`structural_organizations` (11: the Legislature, both chambers, eight parties),
definitional rows from `usa_wa_common.orgs` that no wire could attest, kept out
of `uncited_organizations` so a zero gate stays meaningful. Measured clean
2026-09-04: 32,790 citations over 1,391 attestations, 0 orphans.
