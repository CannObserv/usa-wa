# The conformed tier

The registry-joined half of the #302 pipeline (`packages/usa-wa-pipeline/dbt/
models/conformed/`): stateless joins against the identity registry that turn
staging rows into the published products. Split out of
[`PIPELINE.md`](PIPELINE.md) — which keeps layout, commands, the raw tier,
staging, the registry and publication — because these three sections carry the
guards, and each guard encodes a production incident worth reading before
touching the model it protects.

## Conformed: crosswalks + entities (#309, in progress)

`models/conformed/`: `person_crosswalk` / `org_crosswalk` (the registry's
published identity surface — every natural key, its entity ULID, and the
`merged_into` tombstone, read via `usa_wa_pipeline.registry_read`; empty only
under `USA_WA_PIPELINE_HERMETIC=1` — a missing `DATABASE_URL` fails the build,
§ Nightly chain below) and `persons` /
`organizations` (one row per LIVE entity; logic in
`usa_wa_pipeline.conformed.entities` — person names roster > WSL > PDC with
newest-attestation-wins, org attributes from the newest biennium's roster wire,
meeting-ref fallback for Joint/`Other`, and the synthesized structural orgs —
legislature, chambers, parties — from `usa_wa_common.orgs.STRUCTURAL_ORGS`).
`profiles.yml` pins `threads: 1`: threaded Python models race first-imports of
the workspace packages. Verified on the real archive 2026-09-03: 3,135 persons
(2,999 roster-named / 135 WSL / 1 known gap — the Heck acceptance) and 219
orgs, type distribution matching canonical exactly. Assignments (the span
engine as a Python model) landed next — see below; roles/seats complete the
layer.

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
The roster family is `roster_pdf.build.build_pre1991`'s emission half minus
everything that existed to mutate Postgres (minting Persons, retiring
unasserted rows, the anchor bootstrap, citation writes); what it keeps is the
operator overlay scoped to its own members — every pre-1991 span is this
builder's, so the roster's 922 dated mid-term boundaries take effect here or
nowhere (#226) — and the unattested-span check, which refuses a seat the
overlay synthesized from an event the edition never listed.

**One resolve feeds both.** `roster_resolution()` runs the ~8,600-record
identity resolve once and partitions by disposition: WSL-joined observations
deepen the sponsor build (#228), minted ones are the roster family. Resolving
twice would double the cost and let the halves disagree about who is joined.
The acceptance oracle (`verify_pre1991` — partition exactness, person-side
Senate simultaneity — plus the party vocabulary) is imported unchanged and runs
before anything is built, as the Postgres tier runs it before anything is
written.

Nothing about the span engine is re-implemented. The pure engine
(`build_tenure_spans`, `apply_operator_events`), the projections, the #105
roster hygiene, the #145 biennium-scoped exemption and the #144 artifact
denylist are all **imported unchanged** and applied in the Postgres tier's own
order — each encodes a production incident. Two structural differences:

- **No DB half.** `close_stale_spans` (#83), the synthetic-anchor bootstrap and
  the `load_context_spans` read exist to mutate a durable table; a stateless
  transform recomputes everything, so a span the archive stops asserting is
  simply absent (retraction-as-absence, the publication contract).
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
`usa_wa_facts_seats.house`. `restrict_to_biennium` dissolves with the rest of
the DB half — a stateless rebuild is unconditionally the unrestricted, deep
one, so the #100 depth-mismatch question cannot arise here at all.

**Roles and seats are structural, not registered** (`conformed/roles.py`). A
Role is a named slot in an Organization; an Assignment binds one in time
(ONTOLOGY.md § 2). The span already carries the slot's identity as
`(span_kind, span_discriminator)`, so the `role_key` is a pure function of it —
`seat:house:ld-5:position-1`, `party-role:democratic`,
`committee-member-role:28240`, `seat:senate:ld-22` — identical on every run and
aligned 1:1 with Power Map's seat match key. No ULID mediates it; only the
*organization* the slot belongs to is registry-joined. Every key function is
imported unchanged from the adapter's normalizer and the WA vocabulary.

**#313 adds a role's own `entity_id`** without disturbing that. The key is still
structural and still what PM matches on; the ULID is a stable handle for the API
to address, minted through the registry's third kind (§ Identity registry above)
and carried across from `canonical.roles` so the #312 anchors keep naming the
same rows. Neither crosswalk may drop a role: a seat exists whether or not the
registry has reached it, and the nightly runs `dbt build → registrar → publish`,
so a brand-new seat is unregistered in the build that first sees it and bound by
the next. `unregistered_roles` and `unregistered_orgs` make that one-run latency
visible; `role_entity_mismatches` separates it from a *broken* anchor.

A deterministic join that has forked is the one failure this design cannot
tolerate — but the dbt `assignments_name_a_role` test does **not** detect it
(CR 77). `roles` is generated by iterating `assignments` through the same
`role_for_span`, so `assignments.role_key ⊆ roles.role_key` holds by
construction and that query is unfalsifiable; it pins containment, which is
worth pinning, and nothing more. The fork that can actually happen is ours
drifting from the Postgres tier that already publishes these keys to Power Map,
and the oracle for it is `canonical.roles` — 312 rows against our 312, exact in
both directions, measured 2026-09-03. `parity_spans` diffs them on its own
ratchet (`--role-baseline`, default 0), because dbt has no session to reach
that table.

The diff covers the **attributes too**, not just the key (CR 84): `role_type`,
`name` and `qualifier` are each derived here independently of the tier, and the
one production instance of this fork changed no key at all — #110 churned 305
party roles on local `member` against PM's `party_member`. All three measure 0
mismatches across the 312 roles, so `role_attribute_mismatches` is gated at zero
rather than ratcheted.

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
barely moves, so the publish shrink gate sees nothing and the parity probe only
runs afterward. Both routes to an empty deepening are refused — an empty roster
tier, and a roster tier present but parsing to zero records (an upstream
rename) — and the refusal lives on **`roster_resolution`**, not only on
`build_all_spans` (CR 76). That distinction is the whole point: `build_all_spans`
raises only when `extra_observations is None`, and neither production caller
passes `None` — the `assignments` model and `parity_spans` both hand it
`roster_resolution(...).joined`, so the resolve runs once for two families. The
guard therefore sat on a door production never opens. It now sits on the resolve,
which is the door they use. Pass `extra_observations` — `[]` included — to state
the deepening rather than derive it; an empty *corpus* (no sponsors, the
hermetic build) still resolves to an empty partition without complaint.

**Python models cannot log.** A `dbt build` never calls `configure_logging()`,
so a `get_logger()` call inside a model emits nothing — the info path is
dropped and the warning path reaches `logging.lastResort`, which prints the
message and discards `extra`. Counters that must reach an operator therefore
belong in a job, not a model: `parity_spans` recomputes the crosswalk join and
reports it under the harness, where records serialize as JSON.

**Reported is not enough — five counters are gated at zero.** The nightly's
`OnFailure=` alerting fires on the *exit code*, so a counter that only reaches
journald tells nobody while the job passes. `unregistered_spans` (a registrar
gap silently shrinking the published table), `unregistered_orgs` (the same gap
in the role dimension — a role whose org is unregistered still publishes, by
design, so nothing else notices it going headless), `malformed_roster_rows`
(partial roster corruption quietly degrading the #228 deepening),
`unparsable_canonical_keys` and `role_attribute_mismatches` (the #110 shape —
same key, different classification) each carry no known-stale story — unlike the
two divergence ratchets — and each measures 0 on the live corpus, so any of them
nonzero exits 1 and names itself in `integrity_failures`. The two ratchets name
themselves in `ratchet_failures` for the same reason (CR 89): they share the
exit code, so the alert has to say which one moved.

`unregistered_orgs` reaches the probe rather than the model for the reason
above, and the role keys are derived there from the **spans**, not from the
crosswalk-joined rows: a slot exists whether or not the person filling it is
registered, so reading the joined rows would make the role parity a statement
about the registry instead of about the derivation (CR 86). A registrar gap
would then report as a phantom role fork — sending the operator after the wrong
defect — and would *hide* a genuine fork whose only spans happen to be
unregistered.

The probe's crosswalk read is **not** the read the model made: the nightly runs
`dbt build → registrar → publish → parity`, so the registrar may have bound
keys in between. `registered_spans` therefore describes the registry as it
stands *now* — the state tomorrow's build publishes from, which is the gap
worth alarming on. A gap the registrar has since closed is transient and
correctly reads as zero.

**The canonical oracle is stale.** `python -m usa_wa_pipeline.parity_spans`
diffs both families against `canonical.assignments` — keyed on
`(source, source_id)` — and gates on a **ratchet**, not equality: measured
2026-09-03 the stored rows diverge by 82 (79 missing / 2 extra / 1 dated
differently). `chamber-house` contributes **none** of that: 329 built, 329
canonical, exact. For the WSL family (45 of those) running the Postgres-tier
adapter's *own* pipeline fresh that day reproduced the identical divergence,
because the stored rows predate the current identity resolve (#277/#281); port
and adapter agreed with each other exactly, 4,851 = 4,851, zero differences.

The roster family's 37 are **the same story from the other side**: 15
identities the snapshot minted as roster persons and today's resolve joins to
WSL members. Cliff Bailey is the worked example — canonical holds both a
shallow `15:*:1991-92` pair and a minted `cliffbailey:1985:*` pair, where this
build asserts the one merged `15:*:1985-86` tenure the deepening produces.
Nothing is lost; the tenure moved families, which is what the #97 collapse is
for.
Any growth past the baseline is a regression; a Postgres-tier rebuild would
take the baseline to zero, and lowering it then is the point.

## Conformed: the citations chain (#313)

`models/conformed/citations.py` answers *how do we know this?* for every
published entity, as a **stateless join** rather than the append-only Postgres
`Citation` ledger it replaces — so a citation the archive no longer supports
stops being emitted, exactly as a span the archive no longer asserts stops being
published. It is the **internal** tier: published bytes, immutable versions, the
same `/datasets` tree, but no subscriber contract and no schema-stability
promise, because its columns follow `/provenance`, not consumers.

One row per `(entity_type, entity_id, source, resource_id)`, joining
`stg_raw_fetches` for the digest, fetch time and URL. `entity_id` is a registry
ULID for `person`/`organization`/`role`, and the **4-part span `source_id`** for
`assignment` — the serving tier keys assignments structurally, so a span's
published identity is its key.

Per kind: a person is cited by every staging row carrying one of its natural
keys (merge tombstones followed — a citation into a retired entity is a dangling
one); an organization by the committee-roster, membership and meeting wires
naming it; an assignment **once per biennium it covers**, which is
`span_emit._ensure_citations`'s own rule moved from emit time to build time; a
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
`stg_raw_fetches` does not carry), `uncited_assignments`, `uncited_roles`,
`uncited_organizations`. Ratcheted — `uncited_persons`, baseline **3**: one
registered WSL member no wire names, plus the two Elmer E. Johnstons sharing the
fold `elmerejohnston`, which the citer refuses to guess between. Counted only —
`structural_organizations` (11: the Legislature, both chambers, eight parties),
definitional rows from `usa_wa_common.orgs` that no wire could attest, kept out
of `uncited_organizations` so a zero gate stays meaningful. Measured clean
2026-09-04: 32,790 citations over 1,391 attestations, 0 orphans.
