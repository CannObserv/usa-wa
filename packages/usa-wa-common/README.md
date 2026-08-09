# usa-wa-common

Layer 2b (#189, AR-14). Washington-State legislative **vocabulary**, source-free.

Everything here is a fact about Washington's legislature rather than about any one
publisher of data about it: when general elections happen and which biennium they
seat, how a House seat is keyed, how a name is folded for matching, how a party
string canonicalizes, and the ballot row shapes a positioned seat is built from.

Before this package existed, each of these lived in whichever adapter first needed
it — the election calendar in the PDC SODA adapter, seat keying in the PDC
normalizer, party canonicalization in the WSL SOAP normalizer — so an adapter
importing a peer adapter was the *only* way to reuse them. That is the accident
AR-14 named: `usa-wa-adapter-legislature` became a shared kernel because it was
the package that existed first.

**Rules.** This package may import Layer 1 (`clearinghouse-core`) and Layer 2
(`clearinghouse-domain-legislative`). It may **not** import a `usa-wa-adapter-*`,
a `usa-wa-facts-*` or a deployment package, and it speaks no wire. Enforced by the
import-linter contract in the root `pyproject.toml`.

| Module | Owns |
|---|---|
| `elections.py` | the WA general-election calendar: which years decide a biennium's House and Senate, and which biennium a cycle seats |
| `seats.py` | WA legislative seat keying: LD parsing, `Position 1/2` canonicalization, seat-Role `source_id`s, House span discriminators |
| `names.py` | name folding and the token-set surname match both the PDC and SOS matchers use |
| `parties.py` | party canonicalization — the WSL `Party` encodings and the SOS ballot `(Prefers X Party)` form |
| `ballot.py` | the source-agnostic ballot interfaces (`HousePosition`, `SenateWinner`, `position_for`) and the `HousePositionCohortProvider` Protocol a fact package depends on instead of a concrete SOS source |
