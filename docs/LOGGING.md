# Logging — the JSON record, and why uvicorn needs a `--log-config`

The rules an agent needs on most tasks stay in [`AGENTS.md`](../AGENTS.md) § Conventions:
`get_logger(__name__)`, `configure_logging()` at entry points only, and the four reserved
keys. This document is the reasoning behind them — needed when touching logging config,
the uvicorn invocation, or the record shape.

## Why `configure_logging()` alone is not enough under uvicorn

**Under uvicorn, `configure_logging()` is not enough** (#155 / gregoryfoster/skills#81): uvicorn ships `uvicorn`/`uvicorn.access`/`uvicorn.error` with `propagate=False` and their own plain-text handlers, which the root-only `configure_logging()` never reaches — journald then interleaves plain-text access lines with JSON app records. Every uvicorn invocation (the `usa-wa.service` `ExecStart` **and** the dev-server commands) therefore passes `--log-config packages/usa-wa-api/src/usa_wa_api/log_config.json`, a dictConfig routing all three through the shared `build_json_formatter` (`"()"` factory — no duplicated fmt string) with `ColorMessageFilter` on each logger to drop uvicorn's ANSI-duplicate `color_message` extra at the record source. Pinned by `packages/usa-wa-api/tests/test_log_config.py` (file validity, formatter single-sourcing, filter placement, and that the unit + docs still pass the flag).

## The record shape

JSON records carry `{timestamp, level, logger, message}` (#133; structlog's default key set). The `timestamp` uses the `+00:00` offset form, a deliberate deviation from the `Z`-suffix date convention below — the structlog migration (gregoryfoster/skills#68) owns the final format. `level`/`logger`/`timestamp`/`message` are reserved: never pass them in `extra={}`.
