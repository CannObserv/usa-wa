# usa-wa-adapter-legislature

WA State Legislature adapter. Layer 3 of the clearinghouse architecture — two sources on one target: the `wslwebservices.leg.wa.gov` SOAP services and the Legislature's *Members of the Legislature 1889-2025* roster PDF.

Archives both into the #304 raw store (`raw_harvest`, `roster_pdf.raw_harvest`) and supplies the pure parsers, resource ids and span projectors the #302 pipeline stages and builds through; also the operator-attestation CLIs (`operators.cli`, `committees.succession_cli`). Transport: `zeep` (lazy WSDL load, per-service client cache). The Postgres adapter, normalizers, refresh and builders were deleted in #412 PR F. Module reference: [`docs/MODULES-LEGISLATURE.md`](../../docs/MODULES-LEGISLATURE.md).

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

### Refresh cadence (decision, #24)

Do **not** put cassette re-recording on a calendar. WA committees reorganize on the order of once per biennium, so a monthly re-record is churn. Policy:

- **Opportunistically** — re-record the affected cassette whenever you touch `CommitteeService` (e.g. when P1b adds `GetActiveCommitteeMembers`), so the committee snapshot tracks alongside the new work.
- **Mandatory at each new biennium** — capture the new biennium's `committee_service_get_active_committees_<biennium>.yaml` in early odd years (next: 2027-28 in early 2027). This also tests the cross-biennium `Id`/`Acronym` stability hypothesis (see the transformation spec's Open Questions).

Production data is fetched independently of the cassettes: the nightly `usa-wa-pipeline` unit runs `python -m usa_wa_adapter_legislature.raw_harvest` against live WSL.
