# usa-wa-api

WA deployment of the CannObserv clearinghouse. Layer 4 — the FastAPI + MCP + REST surface that wires WA-specific adapters (legislature, PDC, RCW) to the shared query layer.

Hosted under the `usa-wa.service` systemd unit on port 8000.

`uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload` for the dev server.
