# usa-wa-facts-seats

Layer 3b (#189, AR-14). The **composition layer** the four-layer model lacked.

An *application*, in `docs/ARCHITECTURE.md`'s sense, derives a canonical fact from one or more
archives. Before this package, every application in this deployment lived inside a
target-keyed **adapter** package — so composing across targets meant an adapter importing a
peer adapter, and `usa-wa-adapter-legislature` became a shared kernel by accident.

This package holds the WA legislative-**seat** fact family:

| Module | Fact |
|---|---|
| `house/` | the House Position seat — WSL owns *who sits* (sponsor roster: LD + party), SOS owns *which position* (ballot Position 1/2), PDC supplies the chamber-mover exclusion |
| `house_corroboration.py` | House seat coverage/corroboration audit |
| `senate_corroboration.py` | Senate seat ballot attestation (#106 A′) |
| `pdc/` | PDC winner spans, the chamber-move matcher, and the `person_wa_pdc` cross-link |

**Layering rules.** May import Layer 1, Layer 2, `usa-wa-common` and any `usa-wa-adapter-*`.
May **not** import an adapter's `transport` — a fact depends on cohort *interfaces*, never on
a live wire — and may not be imported by an adapter. Enforced by the import-linter contracts
in the root `pyproject.toml`.

**Why one package and not three.** The issue sketches `-house-position`, `-senate-seat` and
`-committee-membership`. House Position, Senate corroboration and the PDC span builders are
one fact family: they share the roster builder (`pdc/matching.py`), the projector's row types
and the seat vocabulary. Splitting them would immediately require a shared module between the
halves — which is precisely the accident this issue exists to fix. Committee membership is
absent because it composes only WSL sources: it crosses no adapter boundary, so where it lives
is `#183`'s question about intra-package shape, not this one's.
