# Modules — usa-wa-facts-seats (Layer 3b)

The **composition layer** the four-layer model lacked (#189, AR-14). An *application*, in
[ARCHITECTURE.md](ARCHITECTURE.md)'s sense, derives a canonical fact from one or more archives.
Every application in this deployment used to live inside a target-keyed **adapter** package, so
composing across targets meant an adapter importing a peer adapter — which is how
`usa-wa-adapter-legislature` became a shared kernel by accident.

Detailed behaviour of each module is unchanged and documented where it was before the move:
the House Position builder chain in [MODULES-SOS.md](MODULES-SOS.md), the PDC span/identifier
chain in [MODULES-PDC.md](MODULES-PDC.md). This file records the **shape**.

All eight CLIs here run on the #179b job harness with their exit codes unchanged. Two keep
`commit=False` and their own transaction, because their commit is **not** conditional on
success: `senate_corroboration` field-cites the elected senators and *then* exits 1 on a
missing winner (a `failed` result under a committing harness would roll those citations back
behind an unchanged exit code), and both refreshes committed unconditionally through an
explicit `session.begin()` and had no `--dry-run` to honour. `house/migrate.py` and
`pdc/migrate_pdc_spans.py` declare `role="owner"` instead of reading `DATABASE_URL_OWNER`.

```
packages/
  usa-wa-facts-seats/                 — Layer 3b: the WA legislative-seat fact family
    src/usa_wa_facts_seats/
      house/          — the House Position seat (#100/#101/#103/#118/#123). WSL owns *who sits*
                        (sponsor roster: LD + party), SOS owns *which position* (ballot
                        Position 1/2), PDC supplies the chamber-mover exclusion.
        projector.py  —   pure: roster x ballot -> positioned Observations (+ the #103 within-LD
                          elimination and #118 back-chain seeds)
        build.py      —   Phase B driver: read the cohorts offline, merge into TenureSpans, emit
        emit.py       —   bind spans to the Layer-2 generic emitter, cite every biennium
        backchain.py  —   #118 carry-back of a Position from a later ballot anchor
        refresh.py    —   the daily REBUILD driver (systemd: usa-wa-sos-refresh.service).
                          Archive-refresh moved to the adapter at #201 — see below
        migrate.py    —   the one-shot #101 re-source migration
      house_corroboration.py   — House seat coverage/corroboration audit (systemd unit)
      senate_corroboration.py  — Senate ballot attestation, #106 A' (systemd unit)
      pdc/            — PDC-derived seat facts
        matching.py       —   was `usa_wa_adapter_pdc.normalize.pdc_matching`: the House roster
                              builder + `house_mover_ids` (#105 (a) chamber-mover exclusion).
                              Composes a WSL roster with PDC evidence — never was a normalizer
        observations.py   —   was `…normalize.pdc_observations`: PDC cohorts -> Observations
        identifiers.py    —   was `…normalize.pdc_span_emit`: the `person_wa_pdc` child-identifier
                              upsert (identifier-only since #101)
        build_pdc_spans.py / migrate_pdc_spans.py / refresh.py — the PDC Phase-B driver, its
                              one-shot migration, and the daily REBUILD (systemd:
                              usa-wa-pdc-refresh.service)
```

## The archive/rebuild split (#201)

Both `refresh.py` modules used to do **two** jobs in one process: run the source's Phase-A
harvest (which needs a live client) and then rebuild the fact from the resulting archive. Only
the second half is a fact, and the first half is why this package held the only two
`import-linter` exceptions in the tree — a *false* provenance comment in `pyproject.toml`
pointed at a follow-on note in this very file that had never been written. Both are gone.

| Half | Owner | Module | Unit |
|---|---|---|---|
| Archive (Phase A) | adapter | `usa_wa_adapter_sos.results.archive_refresh` | `usa-wa-sos-archive-refresh.service` |
| Rebuild (Phase B) | fact | `usa_wa_facts_seats.house.refresh` | `usa-wa-sos-refresh.service` |
| Archive (Phase A) | adapter | `usa_wa_adapter_pdc.archive_refresh` | `usa-wa-pdc-archive-refresh.service` |
| Rebuild (Phase B) | fact | `usa_wa_facts_seats.pdc.refresh` | `usa-wa-pdc-refresh.service` |

The timers are unchanged and still fire the **rebuild** units, each of which `Wants=` + `After=`
its archive unit — weak on purpose, so a source outage alerts on the archive unit while the
rebuild still re-derives the fact from the last good archive. Two jobs means two `job_runs` rows
and two `/health/jobs` slugs, so one half's staleness can no longer hide behind the other's.
Flag semantics (`--force` is the archive half's; `USA_WA_BIENNIUM` governs both):
[COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md) § Archive vs rebuild. **Two new unit files —
`sudo cp` + `daemon-reload` at merge** (see below).

## Entry-point renames (deployment-affecting)

Four systemd units changed their `ExecStart` module path. **`sudo cp deploy/<unit>
/etc/systemd/system/` + `daemon-reload` is required at merge**, per
[DEPLOYMENT.md](DEPLOYMENT.md) — a unit-file reload alone re-reads the stale root-owned copy.

| Was | Now | Unit |
|---|---|---|
| `usa_wa_adapter_sos.house.refresh` | `usa_wa_facts_seats.house.refresh` | `usa-wa-sos-refresh.service` |
| `usa_wa_adapter_pdc.refresh` | `usa_wa_facts_seats.pdc.refresh` | `usa-wa-pdc-refresh.service` |
| `usa_wa_adapter_sos.house_corroboration` | `usa_wa_facts_seats.house_corroboration` | `usa-wa-house-corroboration.service` |
| `usa_wa_adapter_sos.senate_corroboration` | `usa_wa_facts_seats.senate_corroboration` | `usa-wa-senate-corroboration.service` |

Non-timer CLIs renamed the same way: `usa_wa_facts_seats.house.build` / `.migrate` /
`.backchain`, `usa_wa_facts_seats.pdc.build_pdc_spans` / `.migrate_pdc_spans`. Every occurrence
in [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md) and [COMMANDS.md](COMMANDS.md) was updated with
the move; `scripts/tests/test_docs_timer_drift.py` holds the units and the docs in agreement.

## Layering rules

May import Layer 1, Layer 2, `usa-wa-common` and any `usa-wa-adapter-*`. May **not** import an
adapter's `transport` — a fact depends on cohort *interfaces*, never on a live wire — and may
not be imported by an adapter. Enforced by the `import-linter` contracts in the root
`pyproject.toml` — **with no exceptions since #201** (see § The archive/rebuild split);
`scripts/tests/test_import_contracts.py` asserts the `ignore_imports` list stays empty and that
the contract rejects a real fact→transport import.

## Why one package, not three

The issue sketches `usa-wa-facts-house-position`, `-senate-seat` and `-committee-membership`.

House Position, Senate corroboration and the PDC span builders are **one fact family**: they
share the roster builder (`pdc/matching.py`), the projector's row types and the seat
vocabulary. Splitting them into three packages would immediately require a shared module
between the halves — which is exactly the accident this issue exists to fix, reproduced one
level down.

**Committee membership is absent**, deliberately. It composes only WSL sources
(`membership.build`, `membership.projector`,
`membership.emit`), so it crosses no adapter boundary and imports no peer adapter. Moving
it here would be motion without a layering payoff; where it sits inside
`usa-wa-adapter-legislature` is `#183`'s question about intra-package shape, not this one's.

## Known follow-on: the two refresh drivers still import a transport

`house/refresh.py` and `pdc/refresh.py` each run the source's Phase-A harvest *and* rebuild
the fact in one process. Only the second half is a fact, so these are the package's two
exceptions to the *Facts depend on cohort interfaces, never on a transport* contract —
enumerated by name in the root `pyproject.toml`, never wildcarded, so any **new** violation
still fails. Every builder module satisfies the rule unaided.

The split (each adapter owns "refresh my archive", the fact owns "rebuild from the archive")
changes two systemd entry points and their `--force`/`--biennium` semantics, so it is tracked
separately as **#201** rather than bundled into #189.
