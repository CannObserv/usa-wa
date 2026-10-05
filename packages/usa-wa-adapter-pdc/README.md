# usa-wa-adapter-pdc

Layer 3 adapter for the **WA Public Disclosure Commission (PDC)**.

Archives the seated-winner cohorts of the PDC `Campaign Finance Summary` Socrata dataset
(`3h9x-7bvm`) on data.wa.gov into the #304 raw store (`raw_harvest`, nightly). Begun in
issue [#69](https://github.com/CannObserv/usa-wa/issues/69) as the House **Position** source; the
Position has been the SOS ballot's since #101.

PDC is read as an **identifier source, not a Person source**: the #302 pipeline matches each
winner (within LD and seating biennium, by surname tokens) to the WSL member key, and the
registrar files the `wa_pdc:<id>` key on that member's registry entity. The Postgres adapter, harvest and identifier emitter were deleted in #412 PR F.

## Transport

REST/JSON over the Socrata Open Data API (SODA) via `httpx` — distinct from the WSL zeep SOAP client,
but mirrors its `WireFetch` (raw bytes + parsed) contract, so the raw store hashes the pristine bytes.

An **optional** `USA_WA_PDC_APP_TOKEN` (sent as `X-App-Token`) raises Socrata's per-IP rate limit; not
required for correctness at our nightly volume (app tokens are rate-limiting only, not
auth — the dataset is public).

## Re-recording cassettes

Tests replay `vcrpy` cassettes in `record_mode='none'` (live PDC is never silently contacted).
Re-record deliberately by deleting the target cassette and running the recording helper against live
PDC (see `tests/conftest.py`).
