# Jurisdictional Information Architecture — research exploration

- **Date:** 2026-05-30
- **Status:** research note (external exploration → input for the JaaS sub-spec brainstorm)
- **Source:** chat exploration with an external AI assistant; transcript cleaned and structured
- **Output target:** input to `docs/specs/2026-05-30-jurisdictional-information-architecture-design.md` (forthcoming JaaS sub-spec)

## Framing

I'm building an information architecture for **Jurisdictions** — geographically-bounded areas where particular political, regulatory, and policy guidelines apply. Examples: nations, states, counties, cities, districts, census regions, judicial areas, enforcement partitions, federal regional partitions.

Most adhere to strict hierarchies (parent/child), but not all. The core question driving this exploration: where does strict hierarchy break down, and what shape should the IA take to handle the messy cases (overlap, sovereignty, special-purpose overlays)?

---

## 1. The "Multiple Parents" problem (poly-hierarchy)

The most common break in strict hierarchy occurs when a lower-level jurisdiction belongs to more than one higher-level jurisdiction of the same type.

- **Cities in multiple counties:** In the U.S., many cities (Dallas, Aurora, Portland) span multiple county lines. An IA that assumes one parent_id loses data integrity for taxing authorities or law-enforcement reporting.
- **Zip codes:** Frequently used as jurisdictions, but they don't respect city or even state boundaries in rare cases. They're "service routes" for the USPS, not true geographic containers.

## 2. Non-nested "special-purpose" jurisdictions

Many jurisdictions are "functional overlays" — same physical space as cities/counties, but they don't answer to them or nest neatly inside them.

- **School districts:** A single district might encompass three small towns plus a sliver of unincorporated county. A "sibling" to the city in power; geographically a "mosaic" piece that ignores other borders.
- **Water/utility districts:** Often defined by topography (watersheds), not political history.
- **Census tracts/blocks:** Designed for data, not governance. Often nest within counties but frequently bisect neighborhoods or informal districts.

## 3. Sovereign and concurrent jurisdictions

Where hierarchy becomes "flat" or "parallel" rather than vertical.

- **Tribal lands:** In the U.S., Native American reservations are "Domestic Dependent Nations." They exist within a state's geographic footprint but often bypass state authority to deal directly with the federal government.
- **Federal enclaves:** Military bases or National Parks. A crime in Yosemite is often under federal jurisdiction, even though it's physically inside Mariposa County, California. The county may have zero administrative authority there.

## 4. Administrative vs. geographic hierarchy

Distinguish between *spatial containment* (where it is) and *reporting authority* (who is the boss).

| Jurisdiction type | Spatial relationship | Administrative relationship |
|---|---|---|
| Municipal police | Strictly inside city | Reports to city council/mayor |
| County sheriff | Spans city + unincorporated | Independent constitutional officer |
| Federal regions | Spans multiple states | Reports to federal agency |

---

## IA design recommendations

A strict parent/child tree will eventually fail to represent reality. Two architectural patterns instead:

### A. The "Component" or "Tags" model

Instead of `parent_id`, use a many-to-many junction table.

- Entity: `Jurisdiction_A`
- Relationships: Has a "Spatial Overlap" with `County_X` (60%) and `County_Y` (40%).
- Attribute: `is_subdivision_of: State_Z`.

### B. The Spatial-First model (GIS)

Rather than defining hierarchy through logic, define it through geospatial coordinates.

1. Store the boundary of the jurisdiction as a Polygon (GeoJSON/WKT).
2. Use a spatial query (e.g., `ST_Intersects` or `ST_Within`) to determine relationships dynamically.
3. Ask the system: "Which regulatory bodies have any authority over this specific GPS point?" → list of overlapping entities.

---

## Relationship Schema (Component/Tags model elaborated)

Junction table shape: `[Subject_ID] | [Relationship_Type] | [Object_ID] | [Metadata]`.

### Primary Spatial relationships

Describe "container" logic but allow for messy overlaps.

- `IS_FULLY_CONTAINED_BY` — standard hierarchies (a typical neighborhood inside a city). Classic parent/child.
- `PARTIALLY_OVERLAPS` — crucial for cities that span two counties. Metadata stores percentage of area (e.g., Dallas `PARTIALLY_OVERLAPS` Collin County by 18%).
- `IS_COTERMINOUS_WITH` — two different jurisdiction types share the exact same boundary (San Francisco is both a City and a County). Prevents incorrect nesting.

### Primary Governance relationships

Describe "authority" logic, which often bypasses spatial nesting.

- `HAS_REGULATORY_AUTHORITY_OVER` — specialized districts (school districts, water boards, EPA regions). Not "parents" of the city, but they have mandate over the same territory.
- `EXERCISES_CONCURRENT_JURISDICTION` — law enforcement. A state trooper and a county sheriff both have authority on the same stretch of highway. Neither "owns" the other.
- `HAS_EXTRATERRITORIAL_JURISDICTION_OVER` — common in urban planning where a city regulates zoning in a 1-mile "buffer" outside its official limits.

### Functional and Peer relationships

Describe how jurisdictions talk to each other without a power hierarchy.

- `MEMBER_OF` — regional collectives (Council of Governments, specialized Task Force).
- `REPORTS_TO` — functional link (e.g., local health department reporting data to a federal regional partition).
- `CONTRACTS_SERVICES_FROM` — common in "contract cities" that don't have their own police but pay the county sheriff to patrol.

### Example: Aurora, Illinois (sits in four counties)

| Subject (ID) | Relationship Type | Object (ID) | Metadata (Weight/Basis) |
|---|---|---|---|
| Aurora | `PARTIALLY_OVERLAPS` | Kane County | 51% |
| Aurora | `PARTIALLY_OVERLAPS` | DuPage County | 40% |
| Aurora | `PARTIALLY_OVERLAPS` | Will/Kendall | 9% |
| Aurora | `IS_FULLY_CONTAINED_BY` | Illinois | 100% |
| Aurora | `CONTRACTS_SERVICES_FROM` | Metra Rail | Transit Auth |
| Aurora | `SUBJECT_TO` | Fox Valley Water | Watershed District |

### Why the "Link" model beats the "Tree"

1. **Multiple parents** — five "parent" counties for one "child" city without breaking the database logic.
2. **Sovereignty** — represent a Tribal Nation as `GEOGRAPHICALLY_WITHIN` a state but `NOT_SUBJECT_TO` state law, simply by excluding the regulatory link.
3. **Discovery** — query the table from both directions ("What entities have authority over this city?" / "What cities does this Water District touch?").

### Suggested metadata columns

- `effective_date` / `expiration_date` — essential for historical IA (boundaries change).
- `legal_basis` — URL or reference to the statute or charter that created the link.
- `spatial_extent` — GeoJSON snippet or reference to a GIS polygon ID for visualizing the specific area of overlap.

---

## Temporal Dimension (history + draft maps)

Electoral districts and redistricting introduce a temporal dimension that transforms the IA from a static map into a "living" 4D model. A jurisdiction is no longer just a place; it's a *versioned entity* tied to a specific cycle (e.g., the 2020 Census Cycle).

Three specific layers extend the Component/Tags model: **temporal validity**, **lineage**, and **scenarios**.

### 1. The Temporal Layer — valid time vs. transaction time

Bitemporal Modeling:

- **Valid Time** — when the district actually governs ("Effective Jan 1, 2022, to Dec 31, 2032").
- **Transaction/Draft Time** — when the map was created or proposed ("Draft Map A, proposed on Oct 15, 2021").

### 2. Primary Relationships for Redistricting & Lineage

Add these "temporal edges" to the junction table:

- `SUPERSEDES` / `SUCCEEDED_BY` — links 2010 "Congressional District 4" to the 2020 version. Even if the ID is the same, geometry and constituent makeup have changed.
- `EVOLVED_FROM` — useful when a single district is split into two, or two are merged. Tracks the flow of voters or policy obligations.
- `IS_PROPOSAL_FOR` — links a draft map (scenario) to the active jurisdiction it's intended to replace.
- `COVERS_CENSUS_POPULATION` — explicitly links a jurisdiction version to a specific Census dataset ("2020 Decennial Census"). The "legal" population of a district is often fixed in time.

### 3. "Future Potential" — Scenarios

Draft redistricting maps are "branches" in the IA. Don't mix them with active data — add a `Scenario_ID` or `Status` attribute to Jurisdiction entities.

| Jurisdiction_ID | Name | Status | Scenario_ID | Valid_From |
|---|---|---|---|---|
| CD-04-2022 | District 4 | ACTIVE | NULL | 2022-01-01 |
| CD-04-PROP-A | District 4 (Draft) | PROPOSAL | MAP_SCENARIO_2032_X | 2032-01-01 |
| CD-04-PROP-B | District 4 (Draft) | PROPOSAL | MAP_SCENARIO_2032_Y | 2032-01-01 |

Why this matters: "Who is my representative?" → query `Status = 'ACTIVE'`. "How does the new draft affect our service area?" → query `Scenario_ID = 'MAP_SCENARIO_2032_X'`.

### 4. The "Nested-ish" problem in Electoral IA

Electoral districts rarely nest perfectly:

1. Federal districts (Congressional) must contain roughly equal populations.
2. State legislative districts (House/Senate) often have different boundaries than federal ones.
3. Precincts/wards are the smallest "Lego bricks," but redistricting often "cracks" or "packs" them.

Instead of trying to find a parent for a state Senate district, use the *intersection relationship*:

- `State_Senate_12 INTERSECTS County_A` (contains 40,000 residents).
- `State_Senate_12 INTERSECTS County_B` (contains 10,000 residents).

### 5. The "Cycle" container

Group versioned jurisdictions into Cycles.

- Cycle Entity: `2020_Redistricting_Cycle`
- Children: all valid districts for that decade.
- Proposed Scenarios: linked to that cycle.

This allows "hot-swapping" entire sets of jurisdictions. If a court strikes down a map and orders a remedial map, create a new Cycle or Scenario, update the Status, and the entire IA updates without manually re-linking thousands of parent/child relationships.

---

## Aggregation + Classification

The IA needs to bridge spatial truth and relational logic to support both:

- **Classification** — taking a point (address/site) and returning every jurisdictional "tag" that applies.
- **Aggregation** — summing data across jurisdictions (population, voter turnout, etc.).

### 1. Classification — "Where am I?"

A Resolution Engine that filters by time and status:

1. **Spatial Indexing** — store jurisdiction boundaries as spatial objects. On address input, perform a "point-in-polygon" query.
2. **Temporal Filtering** — query must include a `Valid_At` parameter. "Classify this coordinate based on the Active boundaries for the 2024 Election Cycle."
3. **Output Set** — not one parent, but a collection of attributes:
   - Federal_District: WA-07
   - State_Senate: 43
   - City: Seattle
   - Special_Tax_Zone: Downtown_BIZ

### 2. Aggregation — "Weights and Measures"

Harder in a non-hierarchical system because of overlap. If you sum a county's population by adding its cities, but one city spans two counties, you double-count or miss people.

#### The "Atomic Unit" Pattern

Aggregate accurately by using a "ground truth" layer — the smallest possible geographic building blocks that *do* nest strictly. In the U.S., these are often Census Blocks.

- Step 1: Map all jurisdictions to their constituent Atomic Units.
- Step 2: Store the Relationship Weight in the junction table.

| Jurisdiction (Child) | Atomic Unit (Parent) | Weight (%) | Basis |
|---|---|---|---|
| City of Aurora | Census Block A | 100% | Area/Pop |
| City of Aurora | Census Block B | 45% | Area/Pop |

#### Areal Interpolation

When you need to aggregate data ("How many registered voters in this draft district?"):

```
∑(Value of Atomic Unit × Weight of Overlap)
```

This aggregates "up" to any arbitrary boundary, even if it cuts through existing neighborhoods.

### 3. "What-If" Scenarios

Run the same aggregation query across two different `Scenario_ID`s to see the delta — vital for policy impact statements.

- Baseline: "Total population in current federal districts."
- Scenario A: "Total population in proposed Map 2032_v1."
- Delta: "Net change in minority-majority representation between Baseline and Scenario A."

### 4. Relational Schema Summary

| Table | Purpose | Key Columns |
|---|---|---|
| `Jurisdictions` | The "Who/What" | ID, Name, Type, Cycle_ID |
| `Geographies` | The "Where" (Spatial) | Jurisdiction_ID, Geometry, Valid_Start, Valid_End |
| `Junction_Weights` | The "How Much" (Math) | Source_ID, Target_ID, Weight_Value, Status (Active/Draft) |

### The "Sliver" Problem

Aggregating overlapping maps creates "slivers" — tiny geometric fragments (sometimes inches wide) from mismatched GPS data between a city boundary and a new legislative line.

**Rule:** include a *Tolerable Error Threshold*. If a spatial overlap is less than ~0.05% of the total area, the IA should flag for manual review or "snap" the weight to 0% to prevent "ghost data" in aggregations.

---

## "Jurisdiction-as-a-Service" (JaaS) — recommended architecture

Given the complexity of temporal versions, overlapping polygons, and the need for both graph-like lineage and heavy math, a monolithic application will eventually buckle. A dedicated service is the right move.

### Recommended Tech Stack

| Component | Technology | Role |
|---|---|---|
| API Layer | FastAPI | High-performance async handling of spatial queries; Pydantic validation for complex JSON. |
| Spatial Engine | PostGIS | The "source of truth." GeoJSON storage, `ST_Intersects` (classification), `ST_Area` (aggregation). |
| Graph Logic | Postgres + CTEs | Recursive CTEs allow graph-traversal on lineage without a separate Neo4j instance. |
| Frontend | HTMX + Leaflet/MapLibre | "Draft Map" use case — reactive map updates without a heavy SPA framework. |

### The Spatial Layer (PostGIS)

Store boundaries as `GEOMETRY` or `GEOGRAPHY` types. PostGIS handles GeoJSON input/output natively.

- For aggregation: `ST_Intersection(geom1, geom2)` creates the "slivers" for area-weighting.

### The Relational/Graph Layer (Bitemporal)

Every jurisdiction record has four timestamp columns:

1. `valid_start` / `valid_end` — when the district is legally active in the real world.
2. `system_start` / `system_end` — when the record was added/edited (auditing and "undoing" drafts).

### API Endpoints

- **`/resolve`** (Classification) — Input: coordinates + timestamp. Output: flat list of every jurisdiction (city, school district, census block, federal district) that "touches" that point at that time.
- **`/aggregate`** (Aggregation) — Input: jurisdiction ID + metric ("voter_turnout"). Output: weighted sum based on the underlying Atomic Units.
- **`/lineage/{id}`** (Lineage) — Input: a district ID. Output: graph of "parent" districts (historical) and "child" districts (future/drafts).

### Graph in SQL (no Neo4j required for shallow graphs)

Closure Table or Adjacency List in Postgres:

```sql
-- Finding all historical predecessors of a District
WITH RECURSIVE district_lineage AS (
    SELECT source_id, target_id, rel_type
    FROM jurisdictional_links
    WHERE target_id = 'District_2024_A'
  UNION
    SELECT jl.source_id, jl.target_id, jl.rel_type
    FROM jurisdictional_links jl
    INNER JOIN district_lineage dl ON dl.source_id = jl.target_id
)
SELECT * FROM district_lineage;
```

### Coordinate Drifts

GeoJSON from different sources (City Open Data vs. Census Bureau) often doesn't line up perfectly. Recommendation: implement a "Snap-to-Grid" or topology layer in the API — on draft map ingest, the API auto-cleans edges against ground truth (state/county boundary) so aggregations always sum to 100%.

### JaaS Workflow Summary

1. **Ingest** — GeoJSON comes in (Proposed Map B).
2. **Validate** — FastAPI checks for self-intersections or gaps.
3. **Calculate** — PostGIS computes weights against Census Blocks.
4. **Store** — record saved with `status='DRAFT'` and a `Scenario_ID`.
5. **Serve** — application calls `/compare?scenario=B` to see how the draft changes the policy landscape.

---

## GeoAlchemy2 (Python ↔ PostGIS bridge)

GeoAlchemy2 extends SQLAlchemy with spatial types and functions.

### Key Components

- **Spatial Data Types** — `Geometry`, `Geography`, `Raster`. For jurisdictions: `Geometry(POLYGON)` or `Geometry(MULTIPOLYGON)`.
- **Spatial Functions** — full PostGIS library (300+ functions) directly in Python. Write `func.ST_Contains(Jurisdiction.geom, point)` instead of raw SQL.
- **Spatial Operators** — standard Python operators for spatial logic. `&` represents "bounding box" overlap.

### Spatial Joins for Aggregation

To solve aggregation across overlapping boundaries:

```python
# Conceptual GeoAlchemy2 Query
session.query(Jurisdiction).filter(
    Jurisdiction.geom.ST_Intersects(user_point),
    Jurisdiction.valid_start <= election_day,
    Jurisdiction.valid_end >= election_day
).first()
```

### Spatial Indexing (GIST)

Auto-managed. Without GIST, "Classification" would sequentially check every jurisdiction. With GIST, R-Tree finds the result in logarithmic time.

### Integration with FastAPI + Pydantic

| Step | Data Format | Responsibility |
|---|---|---|
| Database | WKB (Binary) | PostGIS |
| ORM Layer | Geometry Object | GeoAlchemy2 |
| Logic Layer | Shape Object | Shapely |
| API Layer | GeoJSON (JSON) | FastAPI / Pydantic |

### SRID Management

Every geometry has a Spatial Reference System (SRID). Usually SRID 4326 (WGS 84 — GPS coordinates). GeoAlchemy2 forces explicit SRID, preventing alignment errors where maps and addresses don't line up.

---

## GeoJSON Serialization — DB-side vs. App-side

The "Geometry Serialization Bottleneck": PostGIS stores WKB (optimized for spatial math, useless for browsers). GeoJSON is text-based, optimized for web mapping. *Where* the CPU work of translation happens matters.

### Approach 1: Database-Side (`ST_AsGeoJSON`)

```sql
SELECT jsonb_build_object(
    'type', 'Feature',
    'geometry', ST_AsGeoJSON(geom)::jsonb,
    'properties', jsonb_build_object('name', name, 'id', id)
) FROM jurisdictions;
```

**Pros:** blistering speed (PostGIS is C); `ST_Simplify` / `ST_Quantize` reduce vertices in the DB; thin API layer.
**Cons:** DB load on string-munging; opaque logic; harder Pydantic validation.

### Approach 2: Application-Side (GeoAlchemy2 + Pydantic)

SQLAlchemy fetches WKB → Shapely object → Pydantic GeoJSON response.

**Pros:** Pythonic; Pydantic validation; high flexibility; scale FastAPI horizontally.
**Cons:** the "Python Tax" — serializing 1,000 polygons in pure Python takes seconds vs. milliseconds in PostGIS. Memory overhead.

### Hybrid Recommendation

1. **For Classification/Single Lookups:** Approach 2 (Python). Overhead negligible; Pydantic flexibility worth it.
2. **For Aggregation/Map Views:** Approach 1 (PostGIS). Loading 500 draft districts onto a map — use `ST_AsGeoJSON` to keep the UI snappy.

GeoAlchemy2 handles standard ORM tasks; `session.execute(text(...))` for high-performance GeoJSON routes.

---

## HTMX Frontend Rendering — Leaflet vs. inline SVG

The "Map Impedance Mismatch": HTMX is hypermedia-first (HTML over the wire); mapping libraries are JSON-API-first (GeoJSON consumed by a stateful JS engine).

### Approach 1: HTMX + Leaflet (Hybrid JaaS)

Base map and Leaflet engine are static. HTMX requests GeoJSON data or layer-switching HTML that Leaflet consumes.

**Pros:** performance with complexity (canvas rendering for thousands of polygons); standard features (tiles, pinch-zoom, panning) free; stateful interactivity (zoom into neighborhood + toggle "2032 Proposed Districts" without losing position).
**Cons:** broken locality of behavior (need "glue" script for HTMX → Leaflet); state desync (server tracks jurisdiction versions; client tracks visible layers).

### Approach 2: HTMX + Inline SVG (Pure Hypermedia)

Server generates `<svg>` and `<path>` tags directly (via `geojson2svg` or PostGIS `ST_AsSVG`). HTMX swaps these into a div.

**Pros:** zero client-side logic; perfect styling (standard CSS hover/transition/fill); accessibility (DOM elements → screen readers + aria-label).
**Cons:** DOM bloat (every vertex becomes a point in the DOM; 500 districts = 100,000+ DOM nodes); namespace trap (HTMX treats everything as `text/html`; SVG requires `image/svg+xml` namespace); hard navigation (zoom/pan needs `svg-pan-zoom`, defeating the "pure HTMX" purpose).

### Recommendation: Leaflet with HTMX Triggers

For viewing + comparing redistricting maps: Leaflet, controlled by HTMX events.

1. **Trigger:** `hx-get="/api/districts?cycle=2032"` on a dropdown.
2. **Response:** server returns small HTML fragment OR special `HX-Trigger: "new-map-data"` header.
3. **Glue:** small Alpine.js or vanilla JS listener catches the event, tells Leaflet to fetch new GeoJSON.

Python remains master of Information Architecture + Temporal Logic; Leaflet handles Spatial Presentation.

---

## Use Case: Strictly viewing and comparing (not editing)

The IA shifts from transactional to **comparative analysis** focus. In redistricting, the most valuable insights aren't "where the line is" but "where the line moved" and how that shifted the underlying data.

### Three Essential Comparison Modes

#### Mode A: Symmetric Difference (Delta Map)

Show only the areas that changed.

- Math: PostGIS `ST_SymDifference(geom_a, geom_b)`.
- Visualization: areas added in one color (green), removed in another (red), unchanged transparent.

#### Mode B: Split-Screen (Synced Leaflet)

Two map containers side-by-side, zoom + pan locked. See "context" of old map alongside "future" of new map.

#### Mode C: Ghost Overlay

Map A is the solid base; Map B is a high-contrast dashed outline.

### Backend: The "Delta Engine"

```sql
-- SQL to find the "Shifted" population between two drafts
SELECT
    ST_AsGeoJSON(ST_SymDifference(a.geom, b.geom)) as delta_geom,
    (ST_Area(ST_Intersection(a.geom, b.geom)) / ST_Area(a.geom)) * 100 as percent_overlap
FROM jurisdictions a, jurisdictions b
WHERE a.id = :id_old AND b.id = :id_new;
```

Pro-tip: pre-calculate "Sliver"/"Delta" polygons during ingestion of a draft map. Storing deltas in a `Comparison_Cache` table makes the frontend vastly more responsive.

### Frontend: HTMX + Alpine.js "Glue"

```html
<select hx-get="/api/comparison-layer"
        hx-target="#map-logic"
        hx-swap="innerHTML"
        name="scenario_id">
    <option value="draft_v1">2032 Proposal A</option>
    <option value="draft_v2">2032 Proposal B</option>
</select>

<div id="map-logic" style="display:none;"></div>
```

The HTMX response (partial):

```html
<script>
    // This runs when HTMX swaps the fragment
    window.dispatchEvent(new CustomEvent('update-map', {
        detail: { url: '/api/geojson/delta/123/456' }
    }));
</script>
```

### Comparing Hidden Data (Aggregation)

| Metric | Current (2020) | Proposed (2032) | Delta |
|---|---|---|---|
| Total Pop | 710,000 | 715,000 | +5,000 |
| VAP (Voting Age) | 540,000 | 542,000 | +2,000 |
| Compactness | 0.82 (Reock) | 0.74 (Reock) | -0.08 |

Implementation: HTMX triggers two requests — one to `/api/geojson/...` (updates Leaflet); one to `/api/stats/compare?old=A&new=B` (returns an HTML `<table>` partial for the sidebar).

### Edge Case: Non-Continuous Comparison

A single old district may split into three new ones. Comparison API should accept **arrays** of IDs:

```
GET /compare?baseline=[ID1]&scenarios=[ID2, ID3, ID4]
```

This visualizes how a single rural district was "cracked" or "packed" into several urban ones.

---

## Open questions for the JaaS sub-spec

The exploration above establishes a strong direction. Questions still to resolve in the design phase:

1. **Service placement.** Is the JaaS a new sibling alongside Power Map, or does Power Map's scope absorb it? The user's lean from this exploration is "dedicated service."
2. **Authority model.** Who owns ingestion of authoritative boundaries (Census Bureau, state SOS, local GIS portals)?
3. **Consumer integration pattern.** How does usa-wa consume? Same producer/archival sidecar as Power Map identity? Or synchronous lookup via `/resolve`?
4. **Naming.** usa-wa uses `usa-wa` slugs; Power Map uses `wa`. Does JaaS adopt a third convention, or align with one of the existing two?
5. **Scope phasing for MVP.** Full bitemporal + scenarios + areal interpolation is a large surface. What's the minimum slice that lets usa-wa replace `Role.district: text(32)` with proper FK references?
6. **Integration with Roles + Assignments.** Should `Role` have a `jurisdiction_id` FK to the district-as-Jurisdiction, or should `Assignment` carry it, or both?
7. **PostGIS / no-PostGIS.** Does MVP require full spatial geometry, or can the relational/graph layer ship first with geometry deferred?
