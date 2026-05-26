# clearinghouse-core

Jurisdiction-agnostic framework primitives for the CannObserv clearinghouse.

Provides:

- `BaseAdapter` (ABC) + `AdapterRunner` — the source-ingestion contract
- Provenance models: `Jurisdiction`, `Source`, `FetchEvent`, `RawPayload`, `Citation`
- `ULID` SQLAlchemy column type
- Shared SQLAlchemy declarative `Base`, session factory, engine helpers
- Logging + config primitives

No government-domain concepts live here. Those belong in `clearinghouse-domain-legislative` (and future `clearinghouse-domain-municipal`).
