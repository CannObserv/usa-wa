# usa-wa-facts-seats

Layer 3b (#189, AR-14). The **composition layer** the four-layer model lacked.

An *application*, in `docs/ARCHITECTURE.md`'s sense, derives a fact from one or more
archives. Before this package, every application in this deployment lived inside a
target-keyed **adapter** package — so composing across targets meant an adapter importing a
peer adapter, and `usa-wa-adapter-legislature` became a shared kernel by accident.

This package holds the pure WA legislative-**seat** logic the #302 pipeline's conformed House
composes (no DB, no CLI):

| Module | Fact |
|---|---|
| `house/` | the House Position seat — WSL owns *who sits* (sponsor roster: LD + party), SOS owns *which position* (ballot Position 1/2): the WSL House roster and its chamber-mover exclusion, the projector, the odd-year position merge and the #118 back-chain |

The Postgres builders, refreshes and corroboration units were deleted in #412 PR F; the PDC
winner matchers, left without a caller, in #471.

**Layering rules.** May import Layer 1, Layer 2, `usa-wa-common` and any `usa-wa-adapter-*`.
May **not** import an adapter's `transport` — a fact composes archived rows, never a live
wire — and may not be imported by an adapter. Enforced by the import-linter contracts
in the root `pyproject.toml`.

**Why one package and not three.** The issue sketches `-house-position`, `-senate-seat` and
`-committee-membership`. The House Position seat and the PDC matching were one fact family:
they shared the roster builder (now `house/roster.py`), the projector's row types and the seat
vocabulary; since #471 only the House Position seat remains. Committee membership is absent because it composes only WSL sources: it crosses
no adapter boundary.

Module reference: [`docs/MODULES-FACTS-SEATS.md`](../../docs/MODULES-FACTS-SEATS.md).
