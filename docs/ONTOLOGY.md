# Ontology — what the data becomes

Sibling of [`ARCHITECTURE.md`](ARCHITECTURE.md). That doc covers how data *gets in* (sourcing vs.
application, one adapter package per jurisdiction+target). This one covers what it *becomes*: the
entity model, the span model, the three event shapes, and which tables are declared but not yet
fed. Read it before adding a fact — the last section is the decision procedure for *where* a new
fact belongs.

Two Postgres schemas hold everything: `clearinghouse_core` (Layer 1 — `Jurisdiction`, provenance)
and `canonical` (Layer 2 — every legislative-domain table below).

## 1. The live entity model

Power Map's terminology, mirrored 1:1. usa-wa is a **producer** of identity data; the archival
system of record is Power Map (PM), and the local `canonical` tables are a query-latency cache
that also survives a State-resource outage.

| Table | Model | What it is |
|---|---|---|
| `persons` | `Person` | a human |
| `organizations` | `Organization` | any non-person legal/political entity, discriminated by `org_type` |
| `roles` | `Role` | a *named slot within* an Organization — a template, not an occupancy |
| `assignments` | `Assignment` | Person × Role × period — the binding in time |
| `person_identifiers` / `organization_identifiers` | | N:1 external-ID map, one row per `(parent, scheme)` |
| `organization_names` / `organization_acronyms` | | dated name / acronym variants for an Organization |
| `role_types` | `RoleType` | local mirror of PM's `role_types` catalog |
| `legislative_sessions` | `LegislativeSession` | a bounded period during which a legislature meets |

All PKs and FKs are ULIDs. Nearly every table carries a `(source, source_id)` natural key under a
UNIQUE constraint — that pair is the idempotency key every upsert runs on. (The mirror-only
`role_types` is the exception here: its natural key is `slug`, PM's own stable value.) Each of the
four identity
models also carries a nullable `pm_<entity>_id` PM anchor under a **partial unique index**
(`WHERE pm_… IS NOT NULL`): one local row per PM anchor, many unsynced NULLs allowed (#86 — the
invariant the #84 crash loop violated with 98 rows sharing one `pm_assignment_id`).

**Vocabularies** (open, no DB CHECK — a CHECK would 422-drift on a new PM slug):

- `Organization.org_type` — `chamber | party | committee | subcommittee | caucus |
  candidate_committee | lobbying_firm | pac | legislature | government_agency | other`.
- `Role.role_type` — `elected_member | leadership | committee_member | committee_leadership |
  staff | party_member | other`. The **classifier** PM validates against is the mirrored
  `role_types` catalog (`state_representative`, `state_senator`, …), not this column's vocab.

**Role has two identity modes**, enforced by complementary partial unique indexes:

- *seat mode* (`jurisdiction_id IS NOT NULL`) — unique on
  `(organization_id, role_type, jurisdiction_id, qualifier)` with `NULLS NOT DISTINCT`. A WA House
  seat is `(chamber, state_representative, LD-n, "Position 1"|"Position 2")`; a Senate seat is the
  same tuple with `qualifier` NULL, one per LD.
- *title mode* (`jurisdiction_id IS NULL`) — unique on `(organization_id, name)`. "Chair" under a
  committee is one Role.

Which shape a producer emits is decided **at runtime** from the mirrored catalog:
`RoleType.expects_jurisdiction` (PM's advisory hint) selects seat-vs-title, and
`RoleType.requires_qualifier` (PM's *enforced* constraint) lets the descriptor refuse a
positionless seat pre-flight rather than take a 422.

`Assignment` allows `person_id` to be NULL when `holder_name_raw` is set
(`ck_assignments_person_or_name`) — an occupancy attested by name before the Person resolves.

### Lifecycle axes

Three orthogonal axes, deliberately **not** collapsed into one column. A row is *live* iff both
tombstones are NULL.

| Axis | Where | Meaning | Effect on the PM sync cohort |
|---|---|---|---|
| `archived_at` | `LifecycleMixin` (person/org/role/assignment) | mirrors PM's reversible "inactive" gate; the PM id is still **live** | row **stays** in sweep/reconcile and is re-fetched, so a dropped un-archive self-heals (#42) |
| `deleted_at` | `LifecycleMixin` | terminal tombstone: genuine delete / merge-orphan with no surviving winner; the PM id is **gone** (re-fetch 404s) | row is **excluded** — never re-created or re-fetched |
| `active` | `Organization` only, plain boolean | PM's operational live-vs-dissolved domain flag (power-map#240) | none — a dissolved committee is inactive, **not** archived; it stays in every read |

Live reads route through `queries.live_only(stmt, *models)`, which spells
`archived_at IS NULL AND deleted_at IS NULL` once and requires at least one model (a silent no-op
would leak non-live rows — the bug it exists to prevent). Apply it **once per lifecycle model the
query joins through**: a live Role hanging off an archived Organization is dropped only if the org
hop is filtered too. `include_hidden=True` is the explicit audit/provenance escape hatch. Do not
add `active` to it.

The portable sync engine cannot import this domain layer, so it filters via the descriptor's
`deleted_column_expr` / `archived_column` indirection instead.

> **`retired_at` no longer exists.** It was the pre-2026-06-25 single overloaded column; the
> axis split renamed it to `archived_at` and added `deleted_at`
> ([`docs/plans/2026-06-25-archived-deleted-axis-split.md`](plans/2026-06-25-archived-deleted-axis-split.md)).
> No model has a `retired_at` column today. "Retire"/"retired" in the span-migration CLIs is
> unrelated vocabulary — it means collapsing a stranded Assignment onto its covering span.

### Provenance (Layer 1, `clearinghouse_core`)

Every canonical fact is attested: `Source` (one per jurisdiction+feed) → `FetchEvent` (append-only,
one per fetch) → `RawPayload` (hashed bytes) → `Citation` (polymorphic link from a row — or a
single field via `field_path` — back to the attesting `FetchEvent`). Citations are **insert-only**
for the app role (#54 `REVOKE DELETE`), which is why re-emission dedups on the attesting event's
`resource_id` rather than its id: a daily re-pull mints a fresh `FetchEvent` for the same resource.

## 2. Spans

A **tenure span** is a contiguous run of biennia in which one member held one thing — a Senate
seat, a House seat+Position, a committee membership, a party affiliation — collapsed into a single
dated record. A 12-year senator is one span, not six per-biennium rows.

### Why there is no `spans` table

There is no span table because a span **is already an Assignment**. `TenureSpan` is a frozen
dataclass in `clearinghouse_domain_legislative.tenure_spans` — a pure in-memory intermediate, never
persisted as itself. `span_emit.emit_spans` upserts exactly one `Assignment` per span carrying its
`valid_from` / `valid_to` / `is_active`.

That is not a shortcut, it is the shape: an Assignment is *Person × Role × period*, and a tenure
span is a person holding a role over a period. A separate table would fork the PM mirror — PM
keys an assignment on `(person, role, start_date)`, so a local span row would need its own anchor,
its own reconcile path, and a join to stay consistent with the assignment it duplicates.

**The `kind` is not a column.** It lives in the Assignment's `source_id`, a deterministic 4-part
colon key:

```
{member_id}:{kind}:{discriminator}:{start_biennium}
```

Keying on the tenure *start* is what makes rebuilds idempotent: an extending span upserts its own
row (updating `valid_to`), while a post-gap tenure opens a new-start row. Consumers that need the
kind parse position 1 — `close_stale_spans` scopes its sweep by
`len(parts) == 4 and parts[1] in kinds`, and the one-shot migrations detect legacy shapes by part
count. Non-4-part (legacy) `source_id`s are never touched by the sweep.

The **discriminator** is the caller's semantic decision, and changing it opens a new span: keying
a Senate seat on its LD means a district renumbered under redistricting splits a continuously
serving senator into two spans. The builder knows only biennium arithmetic.

### The span-kind vocabulary

`clearinghouse_domain_legislative/span_kinds.py` is the single definition, at Layer 2, imported
(never re-declared) by every Layer-3 builder — the drift #114 was filed to prevent, pinned by a
cross-layer test in `usa-wa-adapter-sos/tests/test_span_kinds_guard.py`.

| Constant | Value | Discriminator | Built by |
|---|---|---|---|
| `KIND_PARTY` | `party` | party slug | WSL sponsor Phase B |
| `KIND_SENATE` | `chamber-senate` | LD | WSL sponsor Phase B |
| `KIND_HOUSE` | `chamber-house` | `ld-{n}-position-{p}` | SOS House Position builder |
| `KIND_COMMITTEE` | `committee` | the committee's stable WSL `Id` | WSL committee-membership Phase B |

`SEAT_KINDS = (chamber-senate, chamber-house, committee)` — the seat-scoped subset. `party` is an
affiliation, not a seat. A seat-scoped operator event MUST name a `SEAT_KINDS` value; a typo would
otherwise record an event every builder silently no-ops.

> **`committee` is a homograph, not a coupling.** The span kind `"committee"` and the
> `Organization.org_type == "committee"` value share a literal and nothing else. An org-type is
> what a body *is*; a span kind is what a tenure *tracks*. Never derive one from the other, and
> never introduce a shared constant for them — a future rename must be free to move only one.

All three live builders write `Assignment.source = "usa_wa_legislature"`. `emit_spans` still takes
`person_source` and `assignment_source` separately because the PDC House builder (#79) resolved a
WSL-sourced Person while writing a `usa_wa_pdc`-sourced Assignment — PDC became identifier-only at
#101 and the House seat moved to the SOS builder, but the split stays available.

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
by a finer-grained table.

A sweep hangs off this. `close_stale_spans` closes any open span the current rebuild no longer
asserts (the restricted daily re-drive never rebuilds a departed member), guarded against mass
close by a fraction threshold + floor. A span whose only asserted biennium is the current one has
no valid past close date, so instead of a degenerate one-day window it is **tombstoned** with
`deleted_at` — a local use of the terminal tombstone alongside the sync engine's dead-anchor heal.

## 3. The three event shapes

Three event tables coexist. They are **not** unified because each answers a different question
with a different shape, and each has exactly one owner of its content.

| Table | Shape | Content written by | Read by |
|---|---|---|---|
| `entity_events` | PM-mirror-shaped | the PM read-mirror (`sync_entity_events`) | the C3 producer, to find its anchor |
| `operator_events` | event-shaped | the operator-succession CLI (`usa_wa_adapter_legislature.operators.cli`) | the span overlay, on every build |
| `committee_succession_events` | link-shaped | the committee-succession CLI | the C3 committee event producer |

### `entity_events` — PM-mirror-shaped

A polymorphic lifecycle event on a Person or Organization: birth / death, founding / dissolution.
It mirrors PM's `ObservationEventItem` field-for-field, and that shape is the point:

- `entity_id` is polymorphic (resolved by `entity_kind`), so it carries **no DB-level FK**;
- the instant is stored as individually nullable `event_year … event_second` components rather
  than a `Date`, so a partial date ("born 1970, month unknown") round-trips faithfully;
- the type is `event_type_slug` **XOR** `event_type_id`, mirroring PM's slug-or-id dispatch;
- `visibility` is constrained to PM's enum (`public | legal_only | hidden`);
- an optional `(linked_entity_kind, linked_entity_id)` pair — set together or not at all.

Only the **read** direction is wired: the person/org descriptors pull
`GET /{people|orgs}/{id}/events` and refresh the mirror. Nothing local produces event *content*.
The one local write is `retracted_at`, a marker (not a PM field) set when the C3 producer has
emitted `op=retract` for an anchored event, guarding the retract against re-firing until the
mirror prunes the archived PM event.

Because it mirrors PM, this table is where the C3 producer finds its anchor: PM's event identity
is `(event_type, linked_entity)`, so matching desired identity against mirrored rows sidesteps the
mirror clobbering a producer-written `(source, source_id)` on its next reconcile.

### `operator_events` — event-shaped

An operator states **what happened, on a date**, and the span builders derive the effects. Three
kinds, split by scope so a chamber move never touches the party span:

| Kind | Scope | Effect | Reasons |
|---|---|---|---|
| `departed` | person | closes *every* open span (seat + party + committee) at the date; carries no seat | `died`, `resigned`, `expelled` |
| `vacated` | seat | closes *one* named seat's span; party + committees untouched | `moved`, `resigned`, `defeated` |
| `seated` | seat | opens one named seat's span at the date instead of the biennium floor | `appointed`, `sworn_in` |

A `CHECK` enforces the shape: a seat-scoped kind must carry `(seat_kind, seat_discriminator)`;
`departed` must carry neither. A chamber move is modeled exactly as `vacated` (old seat) +
`seated` (new seat) on the same member, each applied by the builder that owns that seat kind.

Reasons are **evidence classification, not behaviour** — reasons within a kind apply identically.
`resigned` appears under both `departed` and `vacated`; the kind disambiguates.

The overlay is pure and re-applied on every build (the daily refresh re-drives every builder), so
the wire can never win back a corrected span.

### `committee_succession_events` — link-shaped

WA re-keys standing committees across eras (a new WSL `Id` roughly each decade). The *objective*
lifecycle facts — each `Id`'s active flag and founded/dissolved window — are derived from the
roster archive, floor-gated so a committee present in the earliest archived biennium claims no
founding year. What is **not** derivable is which era-`Id` continued, split from, or merged with
which: the re-orgs are irregular and no upstream link exists.

So this table matches PM's linked-entity event directly: the event is recorded on a *subject* org
(PM `org_id`) and points at a *linked* org (PM `linked_entity`), typed by `slug`:

| Slug | Subject | Linked |
|---|---|---|
| `succeeded_by` | predecessor | successor |
| `split_from` | child | parent |
| `merged_with` | one predecessor | the survivor/other |

Exactly one linked entity per event (PM's constraint), so a multi-way re-org is attested pairwise.
`effective_year` is optional. The `CHECK` bars self-loops only — **2-cycles are legitimate data**
(a committee that absorbed a portfolio under a new `Id` and reverted: House Trade & Economic
Development `924 → 966 → 924`). Every current consumer is edge-local and cycle-safe; any future
code that *walks* the graph must be cycle-guarded — `find_succession_cycles` exists to find them.

### What the two operator tables share

Both are operator attestations under one first-class provenance `Source`, `usa_wa_operator`. Each
CLI write appends a hashed `FetchEvent` + `RawPayload` (so the integrity sweep covers operator
facts) alongside the projection row. **Corrections append**: a new row is written and the prior
row's `superseded_by_id` is stamped — provenance is never mutated (#54). Consumers read only
`superseded_by_id IS NULL`.

### Why not unify them

Each shape is load-bearing. `entity_events` must stay field-for-field PM's read shape or the
mirror cannot round-trip partial dates, slug-or-id dispatch, and visibility. `operator_events` is
a *statement about a person on a date* whose effect is computed by three builders with different
`owned_kinds` — the PM event shape has no seat scoping and no `departed`-closes-everything.
`committee_succession_events` is a *typed edge between two orgs*, which `operator_events` cannot
express (it has no linked entity).

They also differ in direction — one read-only mirror, two producer inputs. Unifying them would put
PM-owned and operator-owned rows in one table under one reconcile path, exactly the clobbering the
C3 anchor strategy exists to avoid.

## 4. The declared-not-implemented tier

Four modules declare full table clusters that **nothing writes**. They are imported by
`clearinghouse_domain_legislative/__init__.py` (which registers every table with the shared
`Base.metadata`, so alembic autogenerates them and they exist in the database), but no adapter,
API route, or sidecar imports them — the only non-`__init__` importers are the domain package's
own tests.

| Module | Cluster | Tracking |
|---|---|---|
| `bills.py` | 15 tables — `bills` plus sponsorships, actions (+ classifications), versions (+ links), titles, amendments, subjects, relationships (+ types), `bill_events`, statutory citations, supplements | #28 |
| `votes.py` | `vote_events`, `vote_counts`, `person_votes` | #28 |
| `statutes.py` | `statute_codes`, `statute_titles`, `statute_chapters`, `statute_sections`, `bill_statute_changes` | — |
| `pdc.py` | `lobbying_activities`, `lobbying_positions`, `contributions` | — |

Related open work: **#28** (WSL bill cluster + `discover(since)`), **#67** (WSL committee activity
+ legislation-detail cluster), **#99** (SOS votewa as a richer candidate source).

`legislative_sessions` is the exception in this neighbourhood — it *is* written, by the WSL
adapter's idempotent bootstrap seed, which materializes the legislature Org, the House and Senate
chamber Orgs, the biennium-classified parent session, and that biennium's two regular sessions.
`bills.bills` FKs to it.

Treat this tier as **specification, not scaffolding**. The shapes were designed against the OCD /
OpenStates model and are jurisdiction-generic on purpose (`StatuteCode` is `RCW` for WA, `ORS` for
Oregon, `USC` for federal; PDC's "Filer" deliberately does not survive — a filer maps onto either
a Person or an Organization, and the adapter owns that mapping). But an empty table is not a
contract: before building on one, re-audit its columns against the actual wire, the way
[`ARCHITECTURE.md`](ARCHITECTURE.md) requires auditing a source's coverage before building a fact
on it.

## 5. Deciding where a new fact goes

In order. Stop at the first match.

**1. Is it a *correction or dating* of a tenure the wire already reports, that no wire can
supply?** → an **operator event**. Ask whether it closes everything (`departed`), closes one seat
(`vacated`), or opens one (`seated`). If it fits none of those three, do not add a fourth kind
reflexively — first check whether it is really a *reason* (evidence classification within an
existing kind, which is a one-tuple change) rather than a new behaviour. A new kind is warranted
only when the *effect on spans* differs from all three.

**2. Is it a typed relationship between two entities?** → a **link-shaped event**. If both ends
are organizations and the relation is lineage, it is a `committee_succession_events` slug. A new
slug must exist in PM's catalog first — the local `SLUGS` tuple mirrors PM, it does not define it.

**3. Is it a lifecycle instant on one entity that PM already models (birth, death, founding,
dissolution)?** → an `entity_events` row, produced *to PM* and mirrored back. Do not write event
content into the local mirror directly; the mirror's next reconcile owns those rows.

**4. Is it a new dimension of tenure — something a member holds over a contiguous run of
biennia?** → a **span kind**. Add the constant to `span_kinds.py` (never a literal in the
builder), decide the discriminator deliberately (remember: changing a discriminator splits spans),
and decide whether it belongs in `SEAT_KINDS` — i.e. whether an operator can vacate or be seated
in it. Then it needs a `resolve_role` and a `citation_target`, and a builder that owns it in
`owned_kinds`.

**5. Is it a scalar attribute of an entity that PM already has a field for?** → a **column**,
mirrored from PM. Precedent: `Organization.acronym` and `Organization.active` are scalars adopted
from PM's richer structures because the hot-path read wants one value. Where PM keeps a list or a
dated history, the scalar is the *resolved current value* and the history goes in a child table
(`organization_names`, `organization_acronyms`).

**6. Otherwise — is it a genuinely new entity with its own identity and lifecycle?** → a **table**,
in the domain package, in the `canonical` schema, with a `(source, source_id)` natural key, a ULID
PK, and a PM anchor column if PM will hold it. Register it in `__init__.py` and document it here.

Three tests to apply before adding anything:

- **Does PM own it?** If PM is system-of-record, mirror rather than invent, and do not add a CHECK
  constraint over a PM-owned vocabulary — a new PM slug would 422-drift into an outage.
- **Can the operator's correction be appended?** Anything operator-attested needs
  `superseded_by_id` semantics and a hashed provenance write. Never mutate an attestation.
- **Does it survive a rebuild?** Every derived row is re-derived daily. If the new fact would be
  overwritten by the next unrestricted rebuild, it belongs in an attestation store that the
  builders read, not in the derived row.
