---
title: Mirror PM dated org names into a child table (organization_names)
date: 2026-06-26
status: implemented
---

# Mirror PM dated org names (read side)

Issue: [usa-wa#45](https://github.com/CannObserv/usa-wa/issues/45) — narrowed to the **read side**.
Write-side split out: see the producer follow-up issue (biennium-rename detection, adjacent to [usa-wa#44](https://github.com/CannObserv/usa-wa/issues/44)).
Upstream: [power-map#239](https://github.com/CannObserv/power-map/issues/239) (dated org names), vendored into the client by [usa-wa#43](https://github.com/CannObserv/usa-wa/issues/43).

## Problem

PM ships dated org names: a name valid over a `[effective_start, effective_end)`
window (e.g. a committee renamed mid-biennium). `OrgDetail` carries **both** a
resolved `name` scalar *and* a `names: list[OrgName]`, each `OrgName` holding
`name`, `name_type`, `is_canonical`, `effective_start`, `effective_end`, and its
own PM `id`. usa-wa mirrors only the `name` scalar onto `Organization.name`; the
`names[]` history is **unused**.

We will ingest historical WSL data that references **former** committee names
(committee `Id` is stable; `LongName` changes, usually at a biennium boundary).
To associate that historical data to the right committee, the local cache needs
the name *history* on solid, queryable footing — not just the current name.

## Approach

Add a child table **`canonical.organization_names`**, modeled 1:1 on the existing
`OrganizationIdentifier` child-table pattern (N:1 to `organizations`, CASCADE FK,
`(source, source_id)` natural key). Each row = one PM `OrgName` variant, anchored
by `pm_org_name_id`.

`Organization.name` **stays** as the denormalized, PM-resolved "current" scalar —
the hot-path live read is unchanged and additive-only. The child table is the
historical/association surface, queried by `normalize_name(name)` when historical
ingest needs to resolve an old name → org.

### Why a child table, not JSONB

| Need | Child table | JSONB |
|---|---|---|
| Resolve former-name string → org (indexed `normalize_name`) | ✅ btree index | ✗ awkward, unindexed by default |
| Per-name PM anchor (`OrgName.id`) for idempotent LWW sync | ✅ one row, one anchor | ✗ whole-blob rewrite, anchor lost |
| FK integrity + uniqueness (no dup variant per source row) | ✅ constraints | ✗ none |
| Matches repo precedent | ✅ `*_identifiers` tables | JSONB reserved for opaque verbatim blobs (`event_place_address`) |

People are **not** a dated-name precedent — PM's `PersonName` has no validity
window (only `name_type`/`is_canonical`/`locale`); People store a scalar
`name_full` + the N:1 `person_identifiers` child table. So the precedent People
*do* set is the identifier **child-table shape**, which this follows.

### Model

```python
class OrganizationName(Base, TimestampMixin):
    """One name variant for an Organization, with an optional PM validity window.

    Mirrors PM's OrgName (power-map#239). Organization.name remains the resolved
    "current" scalar; this table is the historical/association surface.
    """
    __tablename__ = "organization_names"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_organization_names_natural_key"),
        {"schema": SCHEMA},
    )
    id: ULID  PK
    organization_id: ULID  FK organizations.id  CASCADE  index
    source: str(64)        # "powermap" for mirrored rows
    source_id: str(128)    # PM OrgName id (== pm_org_name_id for mirrored rows)
    name: str(512)
    name_type: str(32)     # legal | common | former | ...
    is_canonical: bool
    effective_start: date | None
    effective_end: date | None
    pm_org_name_id: ULID | None  index   # per-name PM anchor
```

No `(organization_id, name)` unique constraint — the same name can recur across
disjoint windows. `(source, source_id)` is the idempotency key for the mirror.

### Sync helper

Add `sync_org_names(session, *, organization_id, pm_names)` modeled on
`sync_entity_events`: insert new (by `pm_org_name_id` anchor), update in place,
prune locally-anchored rows PM no longer reports. Touches only
`organization_names` — never the parent `Organization` — so it cannot trigger a
spurious LWW write-back. `map_pm_org_name` flattens an `OrgName` dict to column
values, parsing `effective_start`/`effective_end` ISO dates.

The org descriptor's `fetch_record` already returns the full `OrgDetail` (it then
attaches `/events`); `OrgDetail.names` is already in that payload, so **no extra
PM round-trip** — `upsert_from_pm` reads `record.get("names")` and calls
`sync_org_names`, right after the existing `sync_entity_events` block.

### Read contract ("which name is current at date D")

- **Live/current read** — unchanged: `Organization.name` (PM-resolved).
- **As-of-date** — query `organization_names` for the row whose
  `[effective_start, effective_end)` contains D (NULL start = `-inf`, NULL end =
  open). A thin `name_as_of(org_id, d)` query helper in `queries.py` when the
  first consumer lands; not built speculatively here.
- **Former-name association** — `normalize_name(name)` lookup across the child
  rows for the org cohort (the historical-WSL use case).

## Tradeoffs / alternatives

- **JSONB column on `Organization`** — rejected: this is a join/match surface, not
  opaque provenance (see table above). Loses per-name PM anchor + indexing.
- **Reuse `OrganizationIdentifier`** — rejected: identifiers are external-system
  keys (scheme/value), not human names with windows; overloading muddies both.
- **Drop `Organization.name` scalar, read from child table** — rejected: the
  scalar is the hot-path current-name cache; keeping it makes this purely additive
  and zero-risk to live reads.
- **Build the as-of/former-name query helpers now** — deferred: write them with
  their first consumer (the historical-WSL ingest), not speculatively.

## Steps (TDD: red → green per step)

1. **Test first (red).** Descriptor test: an `OrgDetail` with a multi-entry
   `names[]` (one current, one `former` with a closed window) → assert
   `organization_names` mirrors both, `Organization.name` still adopts the resolved
   scalar, re-running the upsert is idempotent (anchor match, no dup), and a name
   PM drops is pruned. Sibling test doubles get the `names` field.
2. **`OrganizationName` model** (identity.py) — modeled on `OrganizationIdentifier`.
3. **`map_pm_org_name` + `sync_org_names`** (new `descriptors/org_names.py`,
   modeled on `events.py`).
4. **Wire `upsert_from_pm`** (org descriptor): after `sync_entity_events`, call
   `sync_org_names(session, organization_id=row.id, pm_names=record.get("names") or [])`.
   `Organization.name` scalar handling unchanged.
5. **Alembic migration**: create `canonical.organization_names` (FK + indexes +
   natural-key uq). No `grants.sql` change — same `canonical` schema, default
   privileges already grant the app role DML.
6. **Green**: full suite + `ruff`. Migration applied via `usa-wa-migrate` on deploy.
7. **Docs**: AGENTS.md layer map (identity.py line), update the
   `reference_pm_org_identity_contract` memory ("dated names mirrored into
   `organization_names`; `Organization.name` = resolved current scalar").

## Decisions (resolved 2026-06-26)

1. **`name_type` vocab — verbatim, no CHECK.** PM is system-of-record; a CHECK
   risks 422-style drift on a new PM slug. (Unlike `EntityEvent.visibility`, whose
   enum is closed in PM, org `name_type` is open.)
2. **Write side (#46) emits to PM only.** The producer does not write
   `organization_names` locally; PM resolves canonical and the read mirror here
   brings it back — same hands-off stance as today's `names`/`org_acronyms`
   evidence.
3. **Acronyms → own follow-up issue.** Former acronyms are the same
   historical-association need (`org_acronyms` is a *separate* PM list from
   `names`); a sibling `organization_acronyms` table is tracked in
   [usa-wa#47](https://github.com/CannObserv/usa-wa/issues/47), out of scope here.
