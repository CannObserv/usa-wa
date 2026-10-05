# clearinghouse-core

Jurisdiction-agnostic framework primitives for the CannObserv clearinghouse.

Provides:

- `run_job` — the shared job harness every operational CLI runs on, plus the `job_runs` ledger
- The raw-tier file store (`rawstore`) and its integrity sweep (`raw_integrity`)
- The identity registry (`registry`) the dataset pipeline resolves entities through
- `Jurisdiction`, `Source` and the `source_coverage` claims
- `ULID` SQLAlchemy column type
- Shared SQLAlchemy declarative `Base`, session factory, engine helpers
- Logging + config primitives

The adapter runner and the Postgres provenance models were deleted in #412 PR F.

No government-domain concepts live here. Those belong in `clearinghouse-domain-legislative` (and future `clearinghouse-domain-municipal`).
