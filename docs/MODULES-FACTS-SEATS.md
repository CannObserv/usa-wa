# Modules — usa-wa-facts-seats (Layer 3b)

The **composition layer** the four-layer model lacked (#189, AR-14). An *application*, in
[ARCHITECTURE.md](ARCHITECTURE.md)'s sense, derives a fact from one or more archives. Every
application in this deployment used to live inside a target-keyed **adapter** package, so
composing across targets meant an adapter importing a peer adapter — which is how
`usa-wa-adapter-legislature` became a shared kernel by accident.

What is left is pure: the House Position seat logic that joins the WSL sponsor roster to the
SOS ballot, and the PDC↔WSL roster matching it reuses. No DB, no CLI. Its one consumer is the
pipeline's conformed House (`usa_wa_pipeline.conformed.house`), which runs it inside the
nightly build — [PIPELINE-CONFORMED.md](PIPELINE-CONFORMED.md). The Postgres drivers — the
House and PDC span builders and refreshes, the two corroboration units, the identifier
emitter and the one-shot migrations — were deleted in #412 PR F.

```
packages/
  usa-wa-facts-seats/                 — Layer 3b: the WA legislative-seat fact family
    src/usa_wa_facts_seats/
      house/          — the House Position seat (#100/#101/#103/#118/#123). WSL owns *who sits*
                        (sponsor roster: LD + party), SOS owns *which position* (ballot
                        Position 1/2), PDC matching supplies the chamber-mover exclusion.
        projector.py  —   pure `build_house_seat_observations`: roster x ballot -> positioned
                          `chamber-house` Observations keyed `ld-{n}-position-{p}`. No resolvable
                          position → nothing (`missing_position`: a post-1965 unknown is a data
                          gap, not a position-less seat) — unless the #103 within-LD elimination
                          resolves it (2 sitting members, 1 ballot-claimed seat, 1 unmatched
                          member → the remaining position; tracked in `inferred_keys`) or a #118
                          back-chain seed does (`seeded_keys`; ballot always wins over a seed)
        positions.py  —   pure `merge_positions` (#123): a biennium's position map, the even
                          seating cohort's full candidacy set ∪ the odd special's winners only
        backchain.py  —   pure `backchain_house_observations` (#118): carries a ballot-class
                          member's Position back one biennium at a time through continuous
                          same-LD tenure, reaching 2003-04→2007-08 below the SOS floor; breaks
                          at `REDISTRICTING_ERA_START_BIENNIA`, an LD move or tenure gap, and
                          `MAX_BACKCHAIN_HOPS_DEFAULT` (4)
      pdc/            — PDC↔WSL roster matching (#79)
        matching.py       —   `HouseRosterEntry`, `build_house_roster` (WSL `GetSponsors` rows →
                              `{LD: [entry]}`, with the **#105 (a) mover exclusion**: a House row
                              whose `Id` also appears in a named Senate row of the same wire is
                              dropped, so the #103 elimination can seat the appointed replacement)
                              and `house_mover_ids` (#145 — that mover set, which the conformed
                              House passes to the overlay as `movers_by_biennium`). The PDC
                              winner matchers and the `observations.py` projectors that used them
                              were deleted in #471, after #412 PR F removed their last caller
```

## Layering rules

May import Layer 1, Layer 2, `usa-wa-common` and any `usa-wa-adapter-*`. May **not** import an
adapter's `transport` — a fact composes archived rows, never a live wire — and may not be
imported by an adapter. Enforced by the `import-linter` contracts in the root `pyproject.toml`,
**with no exceptions**; `scripts/tests/test_import_contracts.py` asserts the `ignore_imports`
list stays empty. The pipeline sits under the same transport rule.

## Why one package, not three

The issue sketches `usa-wa-facts-house-position`, `-senate-seat` and `-committee-membership`.
The House Position seat and the PDC matching are **one fact family**: they share the roster
builder (`pdc/matching.py`), the projector's row types and the seat vocabulary. Splitting them
would immediately require a shared module between the halves — which is exactly the accident
this issue exists to fix, reproduced one level down.

**Committee membership is absent**, deliberately. It composes only WSL sources
(`usa_wa_adapter_legislature.membership.projector`), so it crosses no adapter boundary and
imports no peer adapter.
