# usa-wa-adapter-pdc

Layer 3 adapter for the **WA Public Disclosure Commission (PDC)**.

Initial cut (issue [#69](https://github.com/CannObserv/usa-wa/issues/69)): source a WA House member's
**Position** (1 / 2) — which WSL does not expose — from the PDC `Campaign Finance Summary` Socrata
dataset (`3r6b`… → resource `3h9x-7bvm`) on data.wa.gov, and emit the House `state_representative` seat
Assignment that P1b ([#27](https://github.com/CannObserv/usa-wa/issues/27)) deferred.

PDC is read as a **Position resolver, not a Person source**: winners are matched (within LD, by
normalized last name + party) to the WSL-`Id`-keyed `Person` already in `canonical.persons`, then
cross-linked with a `person_wa_pdc` identifier. See
[`docs/plans/2026-07-07-pdc-adapter-house-positions.md`](../../docs/plans/2026-07-07-pdc-adapter-house-positions.md).

## Transport

REST/JSON over the Socrata Open Data API (SODA) via `httpx` — distinct from the WSL zeep SOAP client,
but mirrors the `WireFetch` (raw bytes + parsed) contract so the runner's #54 hashing is identical.

An **optional** `USA_WA_PDC_APP_TOKEN` (sent as `X-App-Token`) raises Socrata's per-IP rate limit; not
required for correctness at our once-daily single-GET volume (app tokens are rate-limiting only, not
auth — the dataset is public).

## Re-recording cassettes

Tests replay `vcrpy` cassettes in `record_mode='none'` (live PDC is never silently contacted).
Re-record deliberately by deleting the target cassette and running the recording helper against live
PDC (see `tests/conftest.py`).
