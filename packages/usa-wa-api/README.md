# usa-wa-api

WA deployment of the CannObserv clearinghouse. Layer 4 — the FastAPI surface: the published
datasets (`/datasets`), the read-only `/api/v1` products off the `serving` schema, the ops
routes (job ledger, sources, coverage, provenance), and the nightly backup + restore
(`backup/`). Route inventory and contracts: [`docs/API.md`](../../docs/API.md).

Hosted under the `usa-wa.service` systemd unit on port 8000.

`uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload --log-config packages/usa-wa-api/src/usa_wa_api/log_config.json` for the dev server.
