# usa-wa-adapter-legislature

WA State Legislature SOAP adapter. Layer 3 of the clearinghouse architecture — maps `wslwebservices.leg.wa.gov` SOAP services to the canonical `clearinghouse-domain-legislative` entities.

Owns the `usa_wa_legislature.*` Postgres schema for raw + parsed source data. Subclasses `clearinghouse_core.BaseAdapter`. Transport: `zeep` (lazy WSDL load, per-service client cache).

## Tests

Default tier replays vcrpy cassettes from `tests/cassettes/` (`record_mode='none'`). No live WSL is contacted during `uv run pytest`.

## Re-recording a cassette

When a WSL service changes shape, drop the affected cassette and re-record. One-shot recipe (no pytest plugin involved):

```bash
# 1) Delete the stale cassette.
rm packages/usa-wa-adapter-legislature/tests/cassettes/<name>.yaml

# 2) Run a short Python script that wraps the desired transport call in a
#    vcrpy use_cassette context with record_mode='new_episodes' and points
#    at the cassette path. (See git history for the GetActiveCommittees
#    recording session.) The cassette will materialize after the call returns.

# 3) Commit the cassette alongside the test changes that depend on it.
```

Per-call cassettes (one operation per file) are easier to refresh and less brittle than mixing operations.

Path: `tests/conftest.py` configures the shared VCR instance (`wsl_vcr` fixture) — match keys are `method/scheme/host/port/path` (body matching off, since zeep's SOAP envelope namespace prefixes shuffle across runs).
