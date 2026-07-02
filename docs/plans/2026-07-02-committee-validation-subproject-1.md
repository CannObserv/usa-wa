---
title: Committee data validation + provenance cleanup — sub-project 1 implementation
date: 2026-07-02
status: draft
---

# Committee data validation + provenance cleanup — sub-project 1

Spec: [docs/specs/2026-07-02-committee-validation-design.md](../specs/2026-07-02-committee-validation-design.md)

## Problem

Standing + Joint/Other committees now roundtrip to Power Map (PM), but we've never
verified the current 58-org cohort reflects PM's live curated state (names,
acronyms, merges) — and we're about to push far more history through the same
pipeline. The provenance corpus also carries 6 payload-less pre-#54 fetch_events
(NULL `content_hash`), and the integrity sweep has never run (cursor NULL). We
need a rerunnable local↔PM validator, a clean corpus, and a read-only probe of how
much history exists — all before backfill (sub-project 3).

## Approach

Three deliverables, each TDD. (1) A read-only `validate_committees` CLI in the
`reconcile_*` family: for every `canonical.organizations` row with a
`pm_organization_id`, fetch PM's `OrgDetail` via `PowerMapClient.get_entity`
(`read_path="/api/v1/orgs"`, which embeds `names[]`/`acronyms[]`) and classify each
org into a discrepancy bucket, distinguishing `reconciled` (local adopted PM's
curated value) from `divergent` (mirror lag/break); exit `0`/`1`/`2`. Reads run
**sequentially** (~58 calls, naturally staggered — no concurrent flooding), each
`get_entity` wrapped in a bounded exponential backoff via
`clearinghouse_sync_powermap.retry.backoff` honoring `RetryableClientError` (the
client maps 429/5xx to it but does **not** retry itself — retry is the caller's
job). (2) An owner-role cleanup: because `Citation.fetch_event_id` is `ondelete="RESTRICT"`,
re-point the 6 targets' citations to a surviving baselined `committees:2025-26`
fetch_event, then delete the 6; follow with a `--full` integrity pass and a timer
check. (3) A write-free `probe_committee_extent` CLI walking bienniums backward
from current, stopping after N consecutive empty responses, tallying rows + wire
bytes per biennium — no runner, no archival, no DB writes.

## Tradeoffs / alternatives

- **Reuse `OrganizationDescriptor.fetch_record` for PM reads** — rejected: it also
  fetches `/events` and is write-path shaped; the validator only needs
  `client.get_entity(read_path, pm_id)`. Call it directly, keep the tool read-only
  and dependency-light.
- **Delete the 6 fetch_events with a raw SQL `DELETE` (no re-point)** — rejected:
  the `RESTRICT` FK makes it error, and even if it didn't, dropping citations
  silently loses provenance. Re-point first.
- **Probe through the AdapterRunner (archive as we go)** — rejected: the spec's
  "probe then decide" wants extent *before* committing to archive; runner pulls
  would write half-explored payloads into provenance. Backfill (sub-project 3) does
  archival properly.
- **Alembic migration for the cleanup vs. a one-off owner-role script** — the
  deletion is data, not schema, and is a one-time corpus fix, so a standalone
  owner-role script (run like the other on-box CLIs, under `DATABASE_URL_OWNER`)
  is the better fit; but it must be idempotent and test-covered. (Open question 1.)

## Steps

1. **Validation CLI — tests first.** Add
   `packages/usa-wa-sync-powermap/tests/test_validate_committees.py` with a
   `FakePowerMapClient` returning crafted `OrgDetail` responses — one fixture per
   bucket: `unlinked`, `missing-in-pm`, `merged`, `name-drift` (reconciled),
   `name-drift` (divergent), `acronym-drift`, `names-window-drift`,
   `acronyms-drift`, `parent-drift`, and clean. Assert bucket classification, the
   reconciled/divergent split, exit codes (`0`/`1`/`2`), and the empty-cohort abort.
2. **Validation CLI — implementation.** Add
   `packages/usa-wa-sync-powermap/src/usa_wa_sync_powermap/validate_committees.py`
   (module entrypoint `python -m ...`). Reuse `SidecarSettings` for creds and the
   app-role session factory. Read local orgs (with `organization_names` /
   `organization_acronyms` / parent), fetch each PM `OrgDetail` **sequentially**,
   each `get_entity` wrapped in a bounded `retry.backoff` loop on
   `RetryableClientError` (respects 429/5xx, no flooding), classify, print a
   per-class summary + detail table, `--json` flag, exit on `divergent`. Green the
   step-1 tests (add a fixture where the fake client raises `RetryableClientError`
   once then succeeds, asserting the backoff-retry path).
3. **Provenance cleanup — tests first.** Add a test (owner-role / integration-marked
   as needed) that seeds 6 NULL-hash payload-less fetch_events for
   `committees:2025-26` — each with a `Citation` — plus a surviving baselined
   fetch_event, runs the cleanup, and asserts: the 6 citations re-point to the
   survivor, the 6 fetch_events are gone, baselined fetch_events + their RawPayloads
   are untouched, and a second run is a no-op (idempotent).
4. **Provenance cleanup — implementation.** Add
   `packages/.../scripts` or a `clearinghouse_core`/adapter module (Open question 1)
   that, under the owner role, selects the NULL-hash `committees:2025-26`
   fetch_events, re-points their citations to the newest baselined fetch_event for
   the same `resource_id`, then deletes them. Green the step-3 test.
5. **Probe CLI — tests first.** Add
   `packages/usa-wa-adapter-legislature/tests/test_probe_committee_extent.py` with a
   fake WSL transport returning committees/meetings for a few bienniums then empties;
   assert the backward walk order, the N-consecutive-empty stop, the per-biennium
   tallies, and that nothing is written to the DB / no RawPayload is created.
6. **Probe CLI — implementation.** Add
   `packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/probe_committee_extent.py`
   calling `WSLClient` transport directly (not the runner), tallying row counts +
   wire bytes per biennium, emitting a table + totals, `--json`, `--max-empty N`
   (default per Open question 2). Green the step-5 tests.
7. **Full suite + lint.** `uv run pytest` and `uv run ruff check .` green. Update
   `docs/COMMANDS.md` and `AGENTS.md` layout notes with the two new CLIs + the
   cleanup script.
8. **Operational run (against production, in order).** (a) Run `validate_committees`
   against prod; confirm the 58-org cohort is clean (or triage `divergent`). (b) Run
   the cleanup script under the owner role; confirm 6 removed, citations re-pointed.
   (c) `python -m clearinghouse_core.integrity --full`; confirm zero mismatches.
   (d) Verify `usa-wa-integrity-sweep.timer` is enabled and firing
   (`systemctl status`/`list-timers`); fix if not. (e) Run `probe_committee_extent`;
   capture the volume table for the sub-project 3 spec.

## Open questions / risks

- **Q1 — Home + form of the cleanup. RESOLVED:** standalone idempotent Python
  script run under `DATABASE_URL_OWNER` (consistent with the other on-box CLIs), not
  an Alembic data migration.
- **Q2 — `--max-empty` default for the probe. RESOLVED:** 2 consecutive empty
  bienniums = earliest boundary.
- **Rate limiting — RESOLVED:** the PM client surfaces 429/5xx as
  `RetryableClientError` but does not retry itself. The validator calls
  sequentially (naturally staggered) and wraps each read in a bounded
  `retry.backoff` loop honoring `RetryableClientError`, so we respect 429s rather
  than flood. Covered by a step-1 backoff-path test.
- **Risk — PM read volume / auth in tests.** All PM interaction in tests is via a
  fake client; the only live PM reads are the step-8a operational run (58
  `get_entity` calls, sequential — trivial).
- **Risk — operational deletion is irreversible.** The 6 rows carry no payload and
  are superseded, but the re-point + delete runs against prod under the owner role.
  Step 3's idempotency + FK-integrity test is the guard; run once, verify counts.
