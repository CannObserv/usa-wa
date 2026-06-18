---
title: WSL SOAP adapter — P1a first cut (legislature/chamber/session anchors + committees)
date: 2026-06-18
status: draft
---

# WSL SOAP adapter — P1a first cut

## Problem

`usa-wa-adapter-legislature` is a 39-line stub. The sidecar workstream is complete and waiting for canonical-side producer data ([usa-wa#14](https://github.com/CannObserv/usa-wa/issues/14) and [#20](https://github.com/CannObserv/usa-wa/issues/20) explicitly gate on this). The schema is ready, PM is live, the runner contract is exercised by other adapters — only the WA Legislature SOAP transport itself is missing. P1a is the smallest cut that exercises the contract end-to-end with meaningful data.

## Approach

Build the adapter in two halves bridged by a runner integration. **Synthesis half:** `synthesis.py` (pure functions) + `bootstrap.py` (idempotent DB seeds) materialize the WA Legislature Org, House + Senate chamber Orgs, the 2025-26 biennium-classified session, and the 2025 + 2026 Regular sessions. **SOAP half:** `transport.py` (a thin `zeep` wrapper, lazy-init per service) + `normalize/committees.py` + `adapter.py` (BaseAdapter subclass) fetch `CommitteeService.GetActiveCommittees("2025-26")` once and emit ~50 committee Organization rows. A `refresh.py` CLI orchestrates: load env → resolve `usa-wa` jurisdiction + `usa_wa_legislature` source → call `bootstrap_synthetic_anchors` → construct `AdapterRunner` → invoke `runner.refresh()`. Tests use vcr.py cassettes for the SOAP path so the default tier stays offline. New alembic migration on top of `f5f1bd9f84ae` adds three columns: `legislative_sessions.parent_legislative_session_id` (self-FK), `organizations.acronym`, `organizations.phone`. Docstring-only vocab adds for `org_type` (`legislature`) and `classification` (`biennium`).

## Tradeoffs / alternatives

- **Uniform pipeline (synthesis-as-SOAP-resource).** Rejected — synthesis riding the BaseAdapter contract masquerades synthetic data as fetched and complicates provenance. Brainstorm comparison: Approach 1 vs. 2.
- **Migration-seeded anchors (no adapter-side seed code).** Rejected — inflexible across bienniums; every roll-forward would need a new migration. Brainstorm Approach 3.
- **Hand-written XML fixtures instead of vcr.py cassettes.** Rejected — brittle when WSL schema shifts; cassettes round-trip the real wire shape including SOAP envelope quirks.
- **Add a 1:N `organization_acronyms` table instead of an `acronym` column.** Rejected — premature; WSL exposes one acronym per committee per biennium, and the v1.4 IA's `organization_identifiers` UQ would have collided across bienniums anyway.
- **Skip the `parent_legislative_session_id` self-FK; rely on `biennium_label` text only.** Rejected during the brainstorm — bills carry across regular sessions within a biennium (load-bearing for scope), and the parent FK is the right hook for that.

## Common gates (every code-touching step)

Same convention as the Jurisdictional IA plan: every code-touching step must close clean against `uv run pytest` (full suite green at the 80% coverage floor), `uv run ruff check .`, and `uv run ruff format --check .`. Step-specific verifiable-when notes add per-step criteria on top of these.

## Steps

1. **Add dependencies + schema migration + model updates.** `zeep` and `vcrpy` added to `packages/usa-wa-adapter-legislature/pyproject.toml`; `uv lock`. New alembic migration `2026_06_18_wsl_adapter_p1a_columns.py` on top of `f5f1bd9f84ae` adds three columns:
   - `canonical.legislative_sessions.parent_legislative_session_id` ULID nullable + `ix_canonical_legislative_sessions_parent_id` index + self-FK (`ondelete="RESTRICT"`)
   - `canonical.organizations.acronym` varchar(64) nullable
   - `canonical.organizations.phone` varchar(64) nullable

   `sessions.py` + `identity.py` model classes updated to declare the new columns; `org_type` docstring gains `legislature`; `classification` docstring gains `biennium`. **Verifiable when:** common gates pass; `uv run alembic upgrade head` succeeds on `TEST_DATABASE_URL`; `uv run alembic check` reports no drift; `git diff` shows only the three new columns + docstring vocab adds.

2. **Synthesis module + bootstrap function (TDD).** `synthesis.py`: pure functions `legislature_org(jurisdiction_id) -> dict`, `chamber_orgs(legislature_id) -> list[dict]`, `biennium_session(legislature_id, biennium) -> dict`, `regular_sessions(biennium_session_id, legislature_id, biennium) -> list[dict]`. `bootstrap.py`: `bootstrap_synthetic_anchors(session, biennium, jurisdiction_id) -> BootstrapAnchors` (a dataclass exposing legislature/house/senate/biennium/regular IDs) — runs the synthesis functions, performs idempotent upserts via `INSERT … ON CONFLICT (source, source_id) DO NOTHING` per row, returns the anchor IDs. **Verifiable when:** common gates pass; new `tests/test_synthesis.py` covers all four pure functions (biennium-string parsing, slug formatting, classification values, parent FK plumbing); new `tests/test_bootstrap.py` (uses the workspace `usa_wa` fixture) asserts the 6 expected rows after one call, idempotency on re-run, and FK integrity (regular sessions → biennium session → legislature Org).

3. **Transport layer + first live cassette recording (TDD).** `transport.py`: `WSLClient(service: str)` lazy-inits a `zeep.Client` against `https://wslwebservices.leg.wa.gov/{service}.asmx?wsdl`; caches the Client per service across the process; exposes a typed `get_active_committees(biennium: str) -> list[dict]` method that calls `CommitteeService.GetActiveCommittees` and serializes results to plain Python dicts (zeep's `serialize_object`). Cassette infrastructure: `tests/conftest.py` configures `vcrpy` with `record_mode="once"`, `cassette_library_dir="tests/cassettes/"`, body-matching, decoded responses. **First cassette pass:** run with `--vcr-record=new_episodes` to capture `CommitteeService.GetActiveCommittees.2025-26.yaml` against live WSL; inspect for actual field names (`Agency` vs `AgencyName`, etc.); commit cassette. **Verifiable when:** common gates pass; `tests/test_transport_cassettes.py` (default tier; cassette replay) asserts ~50 committees come back with expected structure; cassette committed under `tests/cassettes/`; recording protocol documented in package README.

4. **Committee normalizer (TDD).** `normalize/committees.py`: `normalize_committees(payload: FetchedPayload, anchors: BootstrapAnchors, jurisdiction_id: ULID) -> NormalizedBatch` — parses the SOAP-derived dict list from the payload body, maps each committee to a canonical `Organization` row with `source_id=Id` (string), `name=LongName`, `short_name=Name`, `acronym=Acronym` (uppercase), `phone=PhoneNumber.strip()` if present, `org_type="committee"`, `parent_organization_id` resolved from `Agency` text via the anchors. Missing `LongName` → log warning + skip row; unknown `Agency` → log warning + `parent=null`. **Verifiable when:** common gates pass; new `tests/test_normalize_committees.py` exercises House / Senate / Joint cases + missing-field + unknown-agency + Unicode-name paths against the recorded cassette (or hand-trimmed fixtures derived from it).

5. **Adapter class + runner integration (TDD).** `adapter.py`: `WALegislatureAdapter(BaseAdapter)` constructed with `(anchors, biennium)`. `discover(since)` yields one `ResourceRef(resource_id=f"committees:{biennium}", url=...)`. `fetch_one(resource_id)` parses the biennium, calls `WSLClient("CommitteeService").get_active_committees(biennium)`, returns a `FetchedPayload`. `normalize(payload)` delegates to `normalize/committees.py`. **Verifiable when:** common gates pass; new `tests/test_adapter_with_runner.py` constructs `AdapterRunner` + `WALegislatureAdapter` with the cassette-backed transport, calls `runner.refresh()`, asserts: 1 `FetchEvent` + 1 `RawPayload` + ~50 `Organization` rows + ~50 `Citation` rows written; re-running short-circuits on cache hit (no second SOAP call).

6. **CLI entrypoint (TDD via integration test).** `refresh.py`: loads `DATABASE_URL` from env, computes biennium from current date (override via `USA_WA_BIENNIUM`), opens async session, resolves `usa-wa` jurisdiction by slug + lazy-creates `usa_wa_legislature` source row, calls `bootstrap_synthetic_anchors`, constructs adapter + runner, invokes `runner.refresh()`, prints `RunSummary`, exits with appropriate code. **Verifiable when:** common gates pass; new `tests/test_refresh_e2e.py` marked `@pytest.mark.integration` invokes `python -m usa_wa_adapter_legislature.refresh` (via `subprocess` or in-process equivalent) against `TEST_DATABASE_URL` post-`alembic upgrade head` + live WSL; asserts 1 + 2 + 1 + 2 + ~50 = 56 rows present across `organizations` + `legislative_sessions` with the FK chain valid; `uv run pytest -m integration` passes.

7. **File the sidecar follow-up issue.** Open a usa-wa GitHub issue captioned "sidecar: emit org_acronyms + contact_methods for organizations with new acronym/phone columns" — the Organization descriptor's `to_observation` extension flagged in the spec § Vocabulary additions item 5. Body lists the small change (one method update + tests), cross-links to this plan and the WSL transformation spec, notes it does not block P1a but does block end-to-end PM delivery of committee acronyms/contact methods. **Verifiable when:** issue is filed and linked from the plan completion comment.

## Open questions / risks

- **Cassette field-name divergence.** The spec asserts WSL `Committee` field names based on documentation patterns (`Agency`, `Acronym`, `LongName`, `Name`, `PhoneNumber`). The first cassette pass (step 3) will pin actual names. If WSL returns `AgencyName` or different casing, the normalizer in step 4 codes against the recorded shape — small inline adjustment, no plan pivot.
- **WSL availability during step 3 cassette recording.** WSL services are generally up, but recording is the only step that requires live network. If WSL is down at recording time, defer the recording until it returns; the rest of the plan can advance in parallel using hand-written fixtures temporarily.
- **`USA_WA_BIENNIUM` env override semantics.** Computing the current biennium from `date.today()` works through 2026; the rollover to 2027-28 happens at the calendar boundary. The override exists for testing and for early-year edge cases when the new biennium hasn't formally started but adapters want to ingest it. Document in `refresh.py` docstring.
- **Cassette commit size.** A single cassette is typically 50–200 KB; well within repo norms. If WSL response shape balloons unexpectedly (e.g., includes inline member lists), revisit cassette filtering before committing.
- **Step 7 issue body.** Sidecar follow-up is a small enough change (one method + tests) that it might land alongside this plan rather than as a separate follow-up. Decide after step 6 lands: if the descriptor extension is trivial, fold it in; if it requires its own brainstorm/spec cut, keep it as a separate issue.

## Revisions during execution

Captured per the writing-plans skill (Phase 4 small-revision policy). None change scope; all are mechanics adjustments uncovered during implementation.

- **Step 1 — vcrpy version pin.** Bumped from `>=6.0,<7` to `>=7.0` (8.x resolved). The 6.x line errors against `urllib3 2.7` (`AttributeError: 'VCRHTTPResponse' object has no attribute 'version_string'`). Workspace `dev` group constraint updated.
- **Step 3 — WSL field-shape pin.** WSDL inspection confirmed `Phone` (not `PhoneNumber` as the spec text guessed), and `GetActiveCommittees` takes **no biennium parameter** — it implicitly returns the currently-active set. Transport method signature dropped the `biennium` argument; the biennium remains adapter-side metadata. Cassette recorded: 34 active committees (within the spec's ~50 estimate).
- **Step 5 — AdapterRunner natural-key + RETURNING-equivalent SELECT.** Two latent runner issues surfaced when integrating against canonical `Organization`:
  1. The runner hardcoded `NATURAL_KEY = ("jurisdiction_id", "source", "source_id")` predating the 2026-06-09 decoupling. Several canonical tables now carry UQ on `(source, source_id)` only. Added a `natural_key` constructor parameter (default unchanged for backward compatibility); WSL adapter constructs the runner with `natural_key=("source", "source_id")`.
  2. `_extract_id_after_upsert` returned the in-memory `entity.id` (typically `None` for ORM-assigned defaults that fire only at flush), causing `citations.entity_id` NOT NULL violations. `_upsert` now executes a follow-up `SELECT id WHERE <natural-key cols>` and assigns the persisted id back onto the entity so Citations bind to the row actually written (whether INSERT or UPDATE). All six existing AdapterRunner tests still pass.
