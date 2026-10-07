# Ontology — what the data becomes

Sibling of [`ARCHITECTURE.md`](ARCHITECTURE.md). That doc covers how data *gets in* (sourcing vs.
application, one adapter package per jurisdiction+target). This one covers what it *becomes*: the
entity model, the span model, the operator event shapes, and what is not modelled yet. Read it
before adding a fact — the last section is the decision procedure for *where* a new fact belongs.

**The model is the published datasets.** The #302 pipeline recomputes every entity, span and
citation from the raw store each night and publishes them as versioned datasets
([`PIPELINE-PUBLICATION.md`](PIPELINE-PUBLICATION.md)). Postgres holds only what a rebuild cannot
recompute, plus what the API serves, in three application schemas: `clearinghouse_core` (`Source`,
`SourceCoverage`, the jurisdiction rows they key on, the run ledger), `registry` (the identity
ledger and the two operator attestation tables) and `serving` (the API's projection of the
published datasets). The `canonical` schema the pipeline was built beside retired in #412.

## 1. The entity model

Power Map's terminology. Since #302 usa-wa is the **single master** for its slice: it publishes
versioned datasets, reads nothing back, and Power Map (PM) is one subscriber.

| Dataset | What it is | Addressed by |
|---|---|---|
| `persons` | a human | registry ULID (`entity_id`) |
| `organizations` | any non-person body, discriminated by `org_type` | registry ULID |
| `roles` | a *named slot within* an organization — a template, not an occupancy | structural `role_key`, plus a registry ULID |
| `assignments` | person × role × period — the binding in time | structural `span_key`; no ULID of its own |
| `person_crosswalk` / `org_crosswalk` | every natural key, the entity it binds to, and the `merged_into` tombstone | `natural_key` |
| `org_lineage` | operator-attested committee succession edges (§3) | the edge `(subject, slug, linked)` |
| `citations` | entity → the wire that attests it (internal tier) | `(entity_type, entity_id, source, resource_id)` |

**Identity is the registry's** (`registry.entities` / `entity_keys` / `adjudications`; machinery
in `clearinghouse_core.registry`). Three kinds — `person`, `org`, `role` — each a ULID bound to
natural keys `<source-slug>:<source_id>` or `<scheme>:<value>` (`usa_wa_legislature:27992`,
`wa_pdc:7710`). Matching only proposes; the registrar binds; a correction is an adjudication with
a recorded note. The registry has no delete: a merge tombstones the loser with `merged_into`, and
every conformed reader follows the tombstone to the survivor (#366). A role has no matching
problem — its key is a pure function of the seat — and is registered only for a stable handle.
Details: [`PIPELINE.md` § Identity registry](PIPELINE.md#identity-registry-308).

**Vocabularies** are plain strings; a dbt `accepted_values` test pins the ones a new value
would change the meaning of:

- `organizations.org_type` — `legislature | chamber | party | committee | other` today, ungated:
  the structural orgs from `usa_wa_common.orgs.STRUCTURAL_ORGS`, House and Senate committees as
  `committee`, Joint/`Other` bodies as `other`.
- `roles.role_type` — `state_senator | state_representative | party_member | committee_member`,
  gated; so are `assignments.span_kind` and `org_lineage.slug`.

**Role keys are structural** (`conformed/roles.py`, `role_for_span`). A Senate seat is
`seat:senate:ld-N` (one per LD, `qualifier` null); a House seat is `seat:house:ld-N:position-P`
(`qualifier` `Position 1|2`); a party slot `party-role:<slug>`; a committee seat
`committee-member-role:<committee id>`. Identical on every run, and what a subscriber matches a
seat on.

### Lifecycle

A published row has no tombstone columns. **Retraction is absence:** a row the archive stops
asserting stops being published in the next version, and the publisher's shrink gate keeps a
degraded build from shipping as mass retraction. What remains is state, not deletion:

| Signal | Where | Meaning |
|---|---|---|
| `merged_into` | the crosswalks | an adjudicated merge; the loser's keys re-point to the survivor |
| `active` | `organizations` | attested in the current biennium (roster wire, meeting window, or the declared structural flag), and false for the subject of a `succeeded_by` / `merged_with` link (#428, #447) |
| `is_active` | `assignments` | the span reaches the current biennium — an open tenure |

`active` and `is_active` both read `spans.current_biennium()`, so the 2027-01-01 rollover closes
spans and retires committees in the same build. The Postgres tier's `archived_at` / `deleted_at`
axes mirrored PM's lifecycle and retired with it (#412).

### Provenance

Every published entity is attested: `Source` (one per jurisdiction+feed) → the raw store
(`raw/<source-slug>/`, wire bodies under their sha256, one manifest per run) → every staging row's
`(source, resource_id)` → `stg_raw_fetches` (digest, fetch time, URL, once per resource) → the
`citations` dataset (entity → resource), served at `/api/v1/provenance`. It is a stateless join,
not a ledger: a citation the archive no longer supports stops being emitted. Rules per entity kind:
[`PIPELINE-CONFORMED.md` § the citations chain](PIPELINE-CONFORMED.md#conformed-the-citations-chain-313).

## 2. Spans

A **tenure span** is a contiguous run of biennia in which one member held one thing — a Senate
seat, a House seat+Position, a committee membership, a party affiliation — collapsed into a single
dated record. A 12-year senator is one span, not six per-biennium rows.

**Party is the one kind a gap does not break** (#289). A seat someone stopped holding is a tenure
that ended, so dormancy splits it; a party affiliation is an attribute of the person, not an office
they occupy, and a break in elected service is no evidence about it. `merge_party_continuity`
therefore rejoins a member's same-party spans however long the gap — the only thing that breaks
one is being attested under a *different* party in between, which is a documented switch.

### A span is an assignment

`TenureSpan` is a frozen dataclass in `clearinghouse_domain_legislative.tenure_spans` — a pure
in-memory intermediate, never stored as itself. Each span publishes as exactly one `assignments`
row carrying its `valid_from` / `valid_to` / `is_active`. That is the shape, not a shortcut: an
assignment is *person × role × period*, and a tenure span is a person holding a role over a period.

**The span's identity is a deterministic key**, built in the engine as a 4-part colon string:

```
{member_id}:{kind}:{discriminator}:{start_biennium}
```

`assignments` publishes its parts as columns (`member_id`, `span_kind`, `span_discriminator`,
`span_start_biennium`) beside `source`, which names the member-id space (`usa_wa_legislature` for
WSL numeric ids, `usa_wa_legislature_roster` for minted pre-1991 identities). `span_key` joins the
five structural fields `entity_id | role_key | span_kind | span_discriminator |
span_start_biennium` into the one handle a subscriber holds (#370); `citations` addresses an
assignment by the 4-part form. Keying on the tenure *start* is what keeps a span's identity stable
across rebuilds: an extending span keeps its key while `valid_to` moves, and a post-gap tenure
opens a new one.

The **discriminator** is the caller's semantic decision, and changing it opens a new span: keying
a Senate seat on its LD means a district renumbered under redistricting splits a continuously
serving senator into two spans. The engine knows only biennium arithmetic.

### The span-kind vocabulary

`clearinghouse_domain_legislative/span_kinds.py` is the single definition, at Layer 2, imported
(never re-declared) by every projector and family — the drift #114 was filed to prevent, pinned by
a cross-layer test in `usa-wa-facts-seats/tests/test_span_kinds_guard.py`.

| Constant | Value | Discriminator | Built by (in `usa_wa_pipeline.conformed`) |
|---|---|---|---|
| `KIND_PARTY` | `party` | party slug | the WSL sponsor family; the roster family before 1991 |
| `KIND_SENATE` | `chamber-senate` | LD | the WSL sponsor family; the roster family before 1991 |
| `KIND_HOUSE` | `chamber-house` | `ld-{n}-position-{p}` | the House family (`conformed.house`, WSL × SOS) |
| `KIND_COMMITTEE` | `committee` | the committee's stable WSL `Id` | the WSL committee-membership family |

`SEAT_KINDS = (chamber-senate, chamber-house, committee)` — the seat-scoped subset. `party` is an
affiliation, not a seat. A seat-scoped operator event MUST name a `SEAT_KINDS` value; a typo would
otherwise record an event every family silently no-ops.

> **`committee` is a homograph, not a coupling.** The span kind `"committee"` and the
> `organizations.org_type == "committee"` value share a literal and nothing else. An org-type is
> what a body *is*; a span kind is what a tenure *tracks*. Never derive one from the other, and
> never introduce a shared constant for them — a future rename must be free to move only one.

### Biennium quantization — and what it costs

Spans are built from *observations*, and an observation is `(member, kind, discriminator,
biennium)`. The resolution of the whole model is therefore **one biennium**:

- consecutive biennia (each 2 years after the last) merge; a gap splits the span in two (dormancy
  is a genuine tenure break — the opposite of the "absence ≠ retirement" rule that governs entity
  existence, because a span models a *served-this-biennium* fact);
- a span reaching the current biennium is open — `valid_to=None`, `is_active=True`; otherwise it
  closes at Dec 31 of its last biennium's even year;
- `valid_from` is Jan 1 of the start biennium's odd year.

Quantization is what **motivates operator events** (§3). A mid-biennium succession is invisible to
every wire signal: a member who died in April stays named in the cumulative roster all biennium,
so their span stays ghost-open; an appointee's span starts at the biennium floor rather than the
appointment date. No wire supplies the intra-biennium date. That gap is filled by attestation, not
by a finer-grained table — and where an attested boundary leaves its counterpart's quantized edge
overlapping it, `seat_clipping` yields the quantized edge to the stated date (#360).

Because every span is recomputed nightly, a span the archive stops asserting is simply absent from
the next version. The Postgres tier needed a sweep (`close_stale_spans`) and tombstones for that;
a stateless rebuild needs neither.

## 3. The operator event shapes

Two curated tables in the `registry` schema, both operator attestations. They are **not** unified
because each answers a different question with a different shape.

| Table | Shape | Written by | Read by |
|---|---|---|---|
| `operator_events` | event-shaped | `usa_wa_adapter_legislature.operators.cli` | the span overlay, every build (`operator_read.operator_events`) |
| `committee_succession_events` | link-shaped | `usa_wa_adapter_legislature.committees.succession_cli` | `org_lineage` and `organizations.active` (`operator_read.succession_links`) |

A third, PM-mirror-shaped `entity_events` table retired with the sync at #314.

### `operator_events` — event-shaped

An operator states **what happened, on a date**, and the span families derive the effects. Three
kinds, split by scope so a chamber move never touches the party span:

| Kind | Scope | Effect | Reasons |
|---|---|---|---|
| `departed` | person | closes *every* open span (seat + party + committee) at the date; carries no seat | `died`, `resigned`, `expelled` |
| `vacated` | seat | closes *one* named seat's span; party + committees untouched | `moved`, `resigned`, `defeated` |
| `seated` | seat | opens one named seat's span at the date instead of the biennium floor | `appointed`, `sworn_in` |

A `CHECK` enforces the shape: a seat-scoped kind must carry `(seat_kind, seat_discriminator)`;
`departed` must carry neither. A chamber move is modeled exactly as `vacated` (old seat) +
`seated` (new seat) on the same member, each applied by the family that owns that seat kind
(`owned_kinds`).

Reasons are **evidence classification, not behaviour** — reasons within a kind apply identically.
`resigned` appears under both `departed` and `vacated`; the kind disambiguates.

The overlay (`operator_overlay.apply_operator_events`) is pure and re-applied on every nightly
build, so the wire can never win back a corrected span.

### `committee_succession_events` — link-shaped

WA re-keys standing committees across eras (a new WSL `Id` roughly each decade). The *objective*
lifecycle facts — whether an `Id` is attested this biennium, its first and last biennium — are
derived from the archive into `organizations`. What is **not** derivable is which era-`Id`
continued, split from, or merged with which: the re-orgs are irregular and no upstream link
exists.

So an operator records a typed link from a *subject* org to a *linked* org, by `slug`:

| Slug | Subject | Linked |
|---|---|---|
| `succeeded_by` | predecessor | successor |
| `split_from` | child | parent |
| `merged_with` | one predecessor | the survivor/other |

Exactly one linked entity per event, so a multi-way re-org is attested pairwise. `effective_year`
is optional. The `CHECK` bars self-loops only — **2-cycles are legitimate data** (a committee that
absorbed a portfolio under a new `Id` and reverted: House Trade & Economic Development
`924 → 966 → 924`). Any code that *walks* the graph must be cycle-guarded —
`find_succession_cycles` exists to find them, and `org_lineage_time_ordered` refuses only a cycle
whose years cannot be read in order ([`PIPELINE-CONFORMED-ENTITIES.md`](PIPELINE-CONFORMED-ENTITIES.md#conformed-committee-lineage-447)).

### What the two share

Both sit under one first-class `Source`, `usa_wa_operator`. Each CLI write stores the serialized
attestation in the raw store (flushed once its transaction commits, `operators/raw.py`), which the
weekly integrity sweep covers like any harvested wire. **Corrections append**: a new row is
written and the prior row's `superseded_by_id` is stamped — an attestation is never mutated (#54).
An operator event with nothing to correct it to — a boundary the member never crossed — is
**retracted** instead (`retracted_at`, #468); a row is one or the other, never both. Readers take
only non-superseded rows — and, for operator events alone, non-retracted ones (`current_clause()`)
— ordered so same-date ties settle deterministically.

### Why not unify them

`operator_events` is a *statement about a person on a date* whose effect is computed by several
families with different `owned_kinds`; it has no linked entity. `committee_succession_events` is a
*typed edge between two orgs*, which the event shape cannot express. Folding them together would
make every reader re-discriminate the shape the table split already states.

## 4. Not modelled yet

Bills, votes, statutes, legislative sessions and PDC lobbying/contribution clusters were declared
as Postgres tables that nothing wrote, and left with the canonical tier (#412 PR F). Their shapes
(OCD / OpenStates-derived, jurisdiction-generic) remain in git history; open work is **#28** (WSL
bill cluster), **#67** (WSL committee activity + legislation detail) and **#99** (SOS votewa as a
richer candidate source). A future cluster arrives the way every fact does now — a raw harvest, a
staging model, a conformed model, a published dataset — and is re-audited against the actual wire
first, as [`ARCHITECTURE.md`](ARCHITECTURE.md) requires of any source.

## 5. Deciding where a new fact goes

In order. Stop at the first match.

**1. Is it a *correction or dating* of a tenure the wire already reports, that no wire can
supply?** → an **operator event**. Ask whether it closes everything (`departed`), closes one seat
(`vacated`), or opens one (`seated`). If it fits none of those three, do not add a fourth kind
reflexively — first check whether it is really a *reason* (evidence classification within an
existing kind, which is a one-tuple change) rather than a new behaviour. A new kind is warranted
only when the *effect on spans* differs from all three.

**2. Is it a typed relationship between two organizations?** → a **link-shaped event**. If the
relation is lineage, it is a `committee_succession_events` slug. A new slug is a contract change:
`org_lineage`'s `accepted_values` test refuses it until someone decides it, and `organizations.active`
must be told whether it retires its subject.

**3. Is it a new dimension of tenure — something a member holds over a contiguous run of
biennia?** → a **span kind**. Add the constant to `span_kinds.py` (never a literal in a projector),
decide the discriminator deliberately (changing a discriminator splits spans), and decide whether
it belongs in `SEAT_KINDS` — i.e. whether an operator can vacate or be seated in it. Then it needs
a `role_for_span` branch (its role key), a family in `conformed.spans` that owns it in
`owned_kinds`, and a citation rule in `conformed.citations`.

**4. Is it a scalar attribute of an existing entity?** → a **column** on that conformed dataset,
with a new `ContractRelease` in `publish.PUBLISHED_DATASETS` (additive = minor). Precedent:
`organizations.active` (#428, 1.1.0), whose meaning later narrowed under a minor bump (#447, 1.2.0).

**5. Otherwise — is it a genuinely new entity with its own identity and lifecycle?** → a
**dataset**: a staging model per source, a registry kind if it needs a stable ULID across
rebuilds, a conformed model, and a `PublishedDataset` entry. Document it here.

Three tests to apply before adding anything:

- **Is it derivable from the archive?** If yes, it is a model, recomputed nightly — never a
  table someone writes. Only judgment no wire carries earns curated state.
- **Can the operator's correction be appended?** Anything operator-attested needs
  `superseded_by_id` semantics and a raw-store copy of the attestation. Never mutate one.
- **Does it survive a rebuild?** Every published row is re-derived nightly. If the new fact would
  be lost by the next rebuild, it belongs in an attestation store that the models read, not in a
  derived row.
