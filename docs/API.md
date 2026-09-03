# HTTP API

The `usa-wa-api` deployment's routes. Two surfaces with different contracts:

- **Unversioned probes** — `/health`, `/ready`, `/health/sync`, `/sync/redrive`. Deployment
  contracts consumed by systemd, the proxy and the operator's shell. They predate the versioned
  surface and keep their paths; versioning them would break those consumers to buy a consistency
  nobody asked for.
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
| GET | `/health/sync` | PM-sync outbox backlog — terminal piles plus overdue PENDING work. Retires at #313 with the outbox. |
| GET | `/health/datasets` | Publication health (#311): catalog age + per-dataset latest version/rows/age; `published: false` before the first publish. The pipeline-era successor to `/health/sync`. |
| GET | `/health/serving` | Serving-projection health (#313): per-dataset loaded rows vs. the catalog's published rows, with `current` per dataset. Distinct from `/health/datasets` — a healthy catalog with a stale serving load is the silent case, because every `/api/v1` answer is still a 200. `loaded: false` before the first load. |
| GET | `/datasets/{path:path}` | Published dataset files (#311): `catalog.json` + `<name>/<version>/data.csv\|datapackage.json` off `USA_WA_DATASETS_ROOT`. Traversal-guarded; 404 for anything unpublished. |
| POST | `/sync/redrive` | **Mutating.** Re-drives dead-lettered outbox entries. `X-Operator-Token` gated. |

### `/api/v1` — operations

| Method | Path | Response model | What |
|---|---|---|---|
| GET | `/api/v1/health/jobs` | `Page[JobHealth]` | Latest run per job slug from the run ledger (#178). |
| GET | `/api/v1/sources` | `Page[SourceOut]` | Every configured feed. |
| GET | `/api/v1/sources/{slug}` | `SourceOut` | One feed by slug. |
| GET | `/api/v1/sources/{slug}/coverage` | `SourceCoverageOut` | What the feed covers, per dimension (#180). |
| GET | `/api/v1/provenance/{entity_type}/{entity_id}` | `Page[CitationOut]` | Citation chain for one canonical row, newest first. |

### `/api/v1` — canonical

| Method | Path | Response model | What |
|---|---|---|---|
| GET | `/api/v1/persons` | `Page[PersonSummary]` | People. Filters: `source`, `name_contains`, `include_hidden`. |
| GET | `/api/v1/persons/{person_id}` | `PersonDetail` | One person plus their external-identifier graph. |
| GET | `/api/v1/organizations` | `Page[OrganizationOut]` | Filters: `org_type`, `jurisdiction_id`, `active`, `include_hidden`. |
| GET | `/api/v1/organizations/{organization_id}` | `OrganizationOut` | One organization. |
| GET | `/api/v1/roles` | `Page[RoleOut]` | Filters: `organization_id`, `role_type`, `jurisdiction_id`, `include_hidden`. |
| GET | `/api/v1/roles/{role_id}` | `RoleOut` | One role. |
| GET | `/api/v1/assignments` | `Page[AssignmentSummary]` | **Tenure spans.** Filters: `person_id`, `role_id`, `is_active`, `span_kind`, `as_of`, `include_hidden`. |
| GET | `/api/v1/assignments/{assignment_id}` | `AssignmentDetail` | One span with its citation chain. |

## Contracts

### Read-only

Every `/api/v1` route is a `GET`. This is not a convention, it is a deployment fact: the API runs
as the **app** role and the provenance tables carry `REVOKE UPDATE` (#54), so a mutating route
here fails at the database, in production, not in review. `test_v1_contract.py` asserts the
method set.

### There is no `/spans`

A tenure span **is** an `Assignment` ([ONTOLOGY.md](ONTOLOGY.md) § 2), so `/assignments` is the
span route. Its `source_id` is the 4-part span key
`{member_id}:{kind}:{discriminator}:{start_biennium}`, which the API parses into `span_kind`,
`span_discriminator` and `span_start_biennium` — all `null` on a row whose `source_id` is any
other shape. The `span_kind` filter refuses to match those rows rather than reading position 2 of
a key that has no position 2.

### Pagination

Keyset, on the row's ULID primary key, ascending — except `/health/jobs` (keyed on `job_slug`)
and `/provenance/…` (descending, newest attestation first). Each route's docstring states its own
order.

```
GET /api/v1/persons?limit=50
  → {"items": [...], "limit": 50, "next_cursor": "01J9ZQ..."}
GET /api/v1/persons?limit=50&cursor=01J9ZQ...
```

| Parameter | Default | Max | Notes |
|---|---|---|---|
| `limit` | 50 | **200** | Above the cap is a 422, not a clamp — clamping would make a short page ambiguous with exhaustion. |
| `cursor` | — | — | Opaque and route-scoped. Echo a `next_cursor` back; never construct one. |

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

### Identifiers are ULIDs, in base32

Every id in a request or response is the 26-character Crockford base32 form
(`01J9ZQ7X8K3M4N5P6Q7R8S9T0V`). The PKs are stored as PostgreSQL `uuid`, so a `::text` cast — or
any path through the `uuid.UUID` representation — yields the 36-character hyphenated hex form,
which is not an id this system's consumers can use (Power Map's API 404s on it). Passing hex where
a ULID is expected is a **422**, deliberately: a 404 would read as "no such row" and send the
caller hunting for a data problem that does not exist.

### Liveness

List routes hide archived and deleted rows, applying `queries.live_only` once per lifecycle model
the query joins through — so an archived Organization hides its roles *and* the tenures held under
them. `include_hidden=true` is the explicit audit escape hatch.

Detail routes never filter: a caller who names an id gets that row with its `archived_at` /
`deleted_at` visible, because "archived" is an answer and a 404 is not.

`Organization.active` is a **third** axis and not a liveness filter — a dissolved committee is
inactive, not archived, and stays in every read. It is exposed as a field and as a filter.

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
- **Write routes of any kind.** `/sync/redrive` remains the only mutating endpoint.
- **Free-text search** beyond `persons?name_contains=`. A real search surface wants an index and a
  ranking contract, neither of which exists yet.
- **Retiring the read-only probe CLIs** (`probe_committee_extent`, `probe_member_identity`,
  `validate_committees`, `committee_lineage_suggest`). #184 names it as a follow-on; the
  orchestration plan puts it explicitly out of scope.
- **`organization_names` / `organization_acronyms` on the org detail route.** Both are dated-variant
  child tables whose windowing semantics deserve their own shape rather than an unbounded embed.
