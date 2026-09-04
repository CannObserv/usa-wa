# HTTP API

The `usa-wa-api` deployment's routes. Two surfaces with different contracts:

- **Unversioned probes** — `/health`, `/ready`, `/health/datasets`, `/health/serving`,
  `/datasets/{path}`. Deployment contracts consumed by systemd, the proxy and the operator's
  shell. They predate the versioned surface and keep their paths; versioning them would break
  those consumers to buy a consistency nobody asked for.
- **`/api/v1`** — the read-only product surface (#184). Everything here is published through
  OpenAPI the moment it ships, so it is versioned from its first route.

Live OpenAPI document: `GET /openapi.json`; interactive docs at `/docs`.

This page is pinned against the running app by
`packages/usa-wa-api/tests/test_v1_contract.py` — a route added, removed or renamed fails the
suite until the table below matches.

## Route inventory

### Unversioned

| Method | Path | What |
|---|---|---|
| GET | `/health` | Liveness. No external calls; returns the build id. |
| GET | `/ready` | Readiness. `SELECT 1` against the database; 503 on failure. |
| GET | `/health/datasets` | Publication health (#311): catalog age + per-dataset latest version/rows/age; `published: false` before the first publish. The pipeline-era successor to `/health/sync`. |
| GET | `/health/serving` | Serving-projection health (#313): per-dataset **loaded version** vs. the catalog's published version, plus rows and `unaddressable_rows`. Currency is a version comparison, not a row count — an unchanged count is the normal case, so counts cannot tell yesterday's snapshot from today's. `current: null` when the catalog does not carry the dataset. Distinct from `/health/datasets`: a healthy catalog with a stale load is the silent case, because every `/api/v1` answer is still a 200. `loaded: false` before the schema is built; a broken database raises rather than reporting `false` — `/ready` is the database-liveness probe. |
| GET | `/datasets/{path:path}` | Published dataset files (#311): `catalog.json` + `<name>/<version>/data.csv\|datapackage.json` off `USA_WA_DATASETS_ROOT`. Traversal-guarded; 404 for anything unpublished. |

### `/api/v1` — operations

| Method | Path | Response model | What |
|---|---|---|---|
| GET | `/api/v1/health/jobs` | `Page[JobHealth]` | Latest run per job slug from the run ledger (#178). |
| GET | `/api/v1/sources` | `Page[SourceOut]` | Every configured feed. |
| GET | `/api/v1/sources/{slug}` | `SourceOut` | One feed by slug. |
| GET | `/api/v1/sources/{slug}/coverage` | `SourceCoverageOut` | What the feed covers, per dimension (#180). |
| GET | `/api/v1/provenance/{entity_type}/{entity_id}` | `Page[CitationOut]` | Citation chain for one published entity, in `(source, resource_id)` order. `entity_type` ∈ `person` \| `organization` \| `role` \| `assignment`; `entity_id` is a registry ULID except for an assignment, whose id is its span key. |

### `/api/v1` — products (served from the `serving` schema since #313)

| Method | Path | Response model | What |
|---|---|---|---|
| GET | `/api/v1/persons` | `Page[PersonSummary]` | People. Filters: `source` (key namespace), `name_contains`. |
| GET | `/api/v1/persons/{person_id}` | `PersonDetail` | One person plus every natural key the registry binds to them. |
| GET | `/api/v1/organizations` | `Page[OrganizationOut]` | Filters: `org_type`, `agency`. |
| GET | `/api/v1/organizations/{organization_id}` | `OrganizationOut` | One organization. |
| GET | `/api/v1/roles` | `Page[RoleOut]` | Filters: `organization_id`, `role_type`, `district`. Ordered by `role_key`. |
| GET | `/api/v1/roles/{role_id}` | `RoleOut` | One role, by its registry ULID. |
| GET | `/api/v1/assignments` | `Page[AssignmentSummary]` | **Tenure spans.** Filters: `person_id`, `role_id`, `role_key`, `is_active`, `span_kind`, `as_of`. |
| GET | `/api/v1/assignments/{assignment_id}` | `AssignmentDetail` | One span with its citation chain. `assignment_id` is the 4-part span key. |

## Contracts

### Read-only

Every `/api/v1` route is a `GET`. This is not a convention, it is a deployment fact: the API runs
as the **app** role and the provenance tables carry `REVOKE UPDATE` (#54), so a mutating route
here fails at the database, in production, not in review.

Since #313 that is true of the **whole app**, not just the `/api/v1` prefix: `POST /sync/redrive`
was the last mutating route and retired with the sync surface, so the deployment registers no
non-GET operation anywhere. That is what lets Power Map revoke usa-wa's write scopes against an
API that provably cannot write rather than one that promises not to.
`test_v1_contract.py` asserts both — the prefixed set and the whole route table.

Re-driving dead-lettered outbox work is now on-box only:
`python -m usa_wa_api.cli.redrive` ([COMMANDS-SYNC.md](COMMANDS-SYNC.md)), with the same scoping
and dry-run semantics. Shell access was always a stronger trust boundary than the single shared
`X-Operator-Token` header the route carried, and that header — and
`USA_WA_OPERATOR_TOKEN` — are gone with it.

### There is no `/spans`

A tenure span **is** an `Assignment` ([ONTOLOGY.md](ONTOLOGY.md) § 2), so `/assignments` is the
span route. Since #313 the span key's parts are **real columns** — `source`, `member_id`,
`span_kind`, `span_discriminator`, `span_start_biennium` — and `assignment_id` is assembled from
them rather than parsed out of a string. That is the inverse of the old model, and it is what
retires #335: `span_kind` filters a column, so the roster family (whose member ids carry their own
colon) stops being invisible to it.

`assignment_id` is `{member_id}:{kind}:{discriminator}:{start_biennium}`, split from the **right**
when it comes back in (#259). `source` is not part of it: the two families key in disjoint
identity spaces, and a span key matching in both is a 500, not a coin flip.

### Pagination

Keyset, ascending, on whatever the row's own identity is: a registry ULID for
`persons`/`organizations`, the structural `role_key` for `roles`, `job_slug` for `/health/jobs`,
and the several columns that together are the key for `assignments` and `/provenance/…`. Each
route's docstring states its own order.

Where the key has a checkable shape the cursor is that value and it is validated as one — a
26-character ULID, so a truncated or `::text`-cast token is a 422 rather than a page starting in
the wrong place. Where it does not (`roles`, and the multi-column routes) the cursor is
**encoded**, which rejects every token this API did not issue that we tried: a raw key, a cursor
from another route, junk, an oversized string. Neither is proof against a well-formed *wrong*
token — a base64 cursor truncated by one character re-pads and decodes to a shorter key, which
shows up as a repeated row rather than a missing one. Only a signed cursor would close that, and a
read-only API over immutable published datasets does not warrant the key management.

```
GET /api/v1/persons?limit=50
  → {"items": [...], "limit": 50, "next_cursor": "01J9ZQ..."}
GET /api/v1/persons?limit=50&cursor=01J9ZQ...
```

| Parameter | Default | Max | Notes |
|---|---|---|---|
| `limit` | 50 | **200** | Above the cap is a 422, not a clamp — clamping would make a short page ambiguous with exhaustion. |
| `cursor` | — | — | Opaque and route-scoped. Echo a `next_cursor` back; never construct one. A cursor from another route decodes cleanly but means something else, so its key arity is checked and a mismatch is a 422. |

`next_cursor` is `null` exactly when the page is the last. It is the *only* exhaustion signal:
branch on it, not on `len(items)`.

Not limit/offset. `OFFSET n` re-runs the ordering and skips *n* rows, so a row inserted before the
reader's position shifts every later page and the reader silently skips one; the cost also grows
with depth. A keyset predicate resumes from a value, is stable under concurrent writes, and is
index-seekable at any depth. No total count — `COUNT(*)` over a growing table is exactly the query
that gets slow.

`/api/v1/sources/{slug}/coverage` is the one unpaginated collection. Its rows are a full reconcile
of a hand-written `CoverageClaim` declaration — one per `(dimension, range_start)` — so the set is
bounded by the audit, not by data volume.

### Identifiers

**Entity ids are ULIDs, in base32.** Persons, organizations and roles are addressed by
`entity_id`, the 26-character Crockford base32 form (`01J9ZQ7X8K3M4N5P6Q7R8S9T0V`). The registry
stores them as PostgreSQL `uuid`, so a `::text` cast — or any path through the `uuid.UUID`
representation — yields the 36-character hyphenated hex form, which is not an id this system's
consumers can use (Power Map's API 404s on it).

**One deliberate exception: an assignment.** A span has no row identity — a span *is* its key —
so it is addressed by `assignment_id`, the 4-part
`{member_id}:{kind}:{discriminator}:{start_biennium}`. `/provenance/{entity_type}/{entity_id}`
therefore accepts both shapes, since it answers for every kind.

Where a ULID *is* expected, passing hex is a **422**, deliberately: a 404 would read as "no such
row" and send the caller hunting for a data problem that does not exist. That holds for path
parameters (`/persons/{id}`), for the ULID-valued query filters (`roles?organization_id=`,
`assignments?person_id=`, `assignments?role_id=`), and for the cursors of the routes keyed on a
ULID — where the alternative is worse than a 404, because an unvalidated cursor resumes the scan
at whatever position it sorts to and the caller silently skips rows.

### Liveness, and why there is none left

The lifecycle tombstones are gone with the tables that carried them. A row the pipeline no longer
asserts is simply **absent** — retraction-as-absence, the #302 publication contract — and a person
the registry merged away is reachable through the crosswalk's `merged_into` rather than through an
archived row. So `archived_at`, `deleted_at`, `include_hidden` and `Organization.active` are all
gone: there is nothing to hide, and therefore no escape hatch to offer.

Detail routes still answer for a row a list route would not surface. A caller holding an id
usually holds it *because* the row went quiet.

### Serving-tier migration (#313)

`/api/v1` now serves the published datasets, loaded into the `serving` schema, rather than the
canonical Postgres tables. Same paths, same methods; the payloads changed. What a consumer
migrates to:

| Gone | Why | Instead |
|---|---|---|
| `id` on every product row | The identity is a *registry entity*, not a canonical row | `entity_id` — the same ULID: the registry seed preserved them, so ids did not move |
| `source` / `source_id` scalars on persons and orgs | Multi-source by construction; one pair could never say more than one thing | `person_crosswalk` / `org_crosswalk`, embedded on the person detail route as `identifiers` |
| `PersonIdentifier` rows | Identity is the registry's now, so an external id is a key bound to an entity | `PersonCrosswalkOut` — `natural_key`, `key_namespace`, `key_value`, `registered_by`, `merged_into` |
| `pm_person_id` / `pm_organization_id` / `pm_role_id` / `pm_assignment_id` | The PM sync retires with the outbox | Power Map's own API; the anchors it holds are unmoved |
| `created_at` / `updated_at` | The dataset **version** is the clock — a row clock said when a row was written, not when the fact was true | `/health/datasets` and `/health/serving` for the version and its age |
| `archived_at` / `deleted_at` / `include_hidden` | See *Liveness* above | Absence, and `merged_into` |
| `Assignment.id` (a ULID) | A span has no row identity; a span **is** its key | `assignment_id` — the 4-part span key |
| `Assignment.holder_name_raw` | An unresolved holder is an unregistered one now, not a name with no id | `entity_id: null` |
| `Organization.jurisdiction_id` / `active`, `Role.jurisdiction_id` | Not in the published datasets | `agency` on an org; `district` on a role |
| `CitationOut.id` / `confidence` / `asserted_at` / `field_path` | A stateless join asserts nothing a re-derivation would not re-assert, and nothing emits field-level citations | `sha256` — which names the bytes exactly |
| `personidentifier` as a `/provenance` type | A key is not a thing that gets separately attested | The person's own chain |

Two filters changed meaning rather than disappearing. `persons?source=` now asks *which key
namespace knows this person* instead of *which source created this row* — a person reachable by
both a WSL and a PDC key answers to both, which the old scalar could not express. And
`assignments?role_id=` resolves through `roles.role_key`, so `role_key` is offered beside it for a
caller who already holds the structural name.

### `source_coverage` when nothing has been recorded

`GET /api/v1/sources/{slug}/coverage` distinguishes three cases that a naive implementation
collapses into one empty response:

| Case | Response |
|---|---|
| No such feed | **404** |
| Feed exists, nobody has audited it | **200**, `coverage_recorded: false`, `items: []` |
| Feed exists, audited, and known not to serve a range | **200**, `coverage_recorded: true`, an `absent` span in `items` and in `known_gaps` |

The middle row is the common case today: the #180 migration is additive and rows seed from
`get_or_create_source`, so the table is empty in production until the next harvest run. Returning
404 or a bare `[]` there would restore exactly the silence #180 exists to remove.

`status` is reported verbatim per span — `verified` | `assumed` | `absent` — and the `absent`
subset is repeated as `known_gaps`. The duplication is deliberate: `absent` is the load-bearing
value (a gap the system *knows about*), and a consumer that renders only `items` still shows it.

### `/health/jobs` when nothing has run

An empty page. #178 shipped with one adopter (the integrity sweep) and the adoption sweep across
the remaining CLIs is #179b, so most slugs have no row yet. A slug that has never run does not
appear — which is itself the finding, and the reason the ledger records slugs that ran rather than
deriving them from a registry that would claim runs that never happened.

Four states, not three: `outcome` is `ok` | `degraded` | `failed` once a run closes, and `null`
while it is open. A row that *stays* open is a job that never reported back — killed, OOM, hung —
and `in_flight: true` names it rather than leaving a consumer to infer it from two nulls.

## Not in v1

Deliberate omissions, each because shipping it would have meant guessing at a contract:

- **Bills, votes, statutes, lobbying.** Those tables are the declared-not-implemented tier (#182):
  no source populates them, so a route over them would publish an empty resource that looks like a
  data outage.
- **Write routes of any kind.** There are none at all since #313 — see *Read-only* above.
- **Free-text search** beyond `persons?name_contains=`. A real search surface wants an index and a
  ranking contract, neither of which exists yet.
- **Retiring the read-only probe CLIs** (`probe_committee_extent`, `probe_member_identity`,
  `validate_committees`, `committee_lineage_suggest`). #184 names it as a follow-on; the
  orchestration plan puts it explicitly out of scope.
- **`organization_names` / `organization_acronyms` on the org detail route.** Both are dated-variant
  child tables whose windowing semantics deserve their own shape rather than an unbounded embed.
