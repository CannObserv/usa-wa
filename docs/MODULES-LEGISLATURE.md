# Modules — WA Legislature adapter

Layer 3, `packages/usa-wa-adapter-legislature/`: the WSL SOAP source mapping —
transport, the raw harvest, resource ids, the pure parsers and projectors the
pipeline stages through, and the write-free probes. The tenure-span and
operator-succession half of the same package is in
[MODULES-LEGISLATURE-SPANS.md](MODULES-LEGISLATURE-SPANS.md); the package's
**second source**, the roster PDF, is in
[MODULES-LEGISLATURE-ROSTER.md](MODULES-LEGISLATURE-ROSTER.md).

The Postgres write path — adapter, normalizers, daily refresh, cohort providers,
Phase-A harvests, span builders and one-shot migrations — was deleted in #412 PR F.

## Package layout (#183)

The Washington State Legislature is **one target publishing one `Source`**
(`usa_wa_legislature`), so `docs/ARCHITECTURE.md`'s `<source_a>/ <source_b>/`
split has nothing to divide here — the package top level *is* the source. What
it does have is four distinct **archives** under that one Source, each with its
own SOAP service and resource-id scheme. Those are the subpackages, plus the
operator surface:

| Directory | Archive key | SOAP service |
|---|---|---|
| `sponsors/` | `sponsors:<biennium>` | `SponsorService.GetSponsors` |
| `committees/` | `committees-roster:<biennium>` | `CommitteeService.GetCommittees` |
| `membership/` | `committee-members-hist:<biennium>:<id>:…` | `CommitteeService.GetCommitteeMembers` |
| `meetings/` | `committee-meetings:<begin>:<end>` | `CommitteeMeetingService` |
| `operators/` | *(no wire)* — the `usa_wa_operator` attestation Source | — |

Phase A is `raw_harvest.py` (archive the wire into the #304 raw store); Phase B
is the #302 pipeline, whose staging models re-parse those wires through
`parsing.py` and whose conformed models call this package's pure projectors.
What stays at package top level is what serves every archive: `transport.py`
(one wire), `ratelimit.py`, `resources.py`, `parsing.py`, `raw_harvest.py`,
`provisioning.py` + `coverage.py` (one `Source`, its claims), `role_keys.py` and
`member_rows.py`.

**Two sources, one target (#225).** Since the roster-PDF source landed, this package holds
*two* self-contained archives, per the multi-source pattern in
[ARCHITECTURE.md](ARCHITECTURE.md): the WSL SOAP wire at package top level, and
`roster_pdf/` — the Legislature's own published *Members of the Legislature 1889-2025*
roster, with its own `Source`, slug, archive key, transport, parser and raw harvest. The SOS
filings+results pair is the precedent. They differ in kind, not just in endpoint: SOAP is a
daily API, the roster is a **frozen document revised about twice a decade**, so it is
harvested by hand, re-checked monthly by a dry-run timer (#237), never joins the nightly
harvests, and is never authoritative for the current biennium (it lags it by design).

```
  usa-wa-adapter-legislature/         — Layer 3: WA Legislature SOAP source mapping
    src/usa_wa_adapter_legislature/
      transport.py    — WSLClient: per-service zeep wrapper with lazy WSDL load; SOAP calls via asyncio.to_thread. `fetch_active_committees` + `fetch_committee_meetings` + `fetch_committees` + `fetch_sponsors` + `fetch_historical_committee_members` (`GetCommitteeMembers(biennium, agency, Name)`, #82 — the **only** archived roster op; a committee absent that biennium, or a sub-1999-00 biennium, raises a benign Fault swallowed to an *empty* WireFetch, matched on the two specific messages so unrelated faults still propagate) return WireFetch (parsed records + pristine SOAP wire for archival, #54) — the raw harvest's pulls. Non-archival parsed-dict siblings `get_committees` / `get_sponsors` / `get_active_committee_members` serve the write-free probes (the last only for the identity probe, which needs a second *endpoint* to cross-check `Id` stability). `parse_committee_meetings` / `parse_committees` / `parse_sponsors` / `parse_historical_committee_members` re-deserialize an *archived* wire offline through the same operation binding (no data re-pull); each guarded by a transport cassette round-trip test. **Central WSL rate limiter (#77)**: a global min-interval gate every SOAP operation POST passes through (`_CapturingTransport.post` → `_WSL_LIMITER`), so no caller can burst the single WSL host. An instance of the shared `ratelimit.RateLimiter`; env-tunable `USA_WA_WSL_MIN_REQUEST_INTERVAL` (default 0.5s, set 0 to disable); `configure_wsl_rate_limit()` sets it in-process — the test suite zeroes it via an autouse fixture; the harvests whose `--pause-seconds` it served were deleted in #412 PR F
      ratelimit.py    — **the package's courtesy limiter** (#77, hoisted from `transport.py` by #236): `RateLimiter`, a thread-safe min-interval gate (reserves the next slot under a lock, sleeps outside it, so concurrent `to_thread` callers are spaced without one holding the lock while sleeping) + `env_float(name, default)` (unset/malformed → default, never an import-time crash). One implementation, one instance per host: `_WSL_LIMITER` (`wslwebservices.leg.wa.gov`) and the roster PDF's `_LEG_LIMITER` (`leg.wa.gov`). `acquire()` blocks — an async caller dispatches it via `asyncio.to_thread`. The SOS package keeps an async sibling (`usa_wa_adapter_sos.ratelimit`); a shared Layer-1 home for both is deferred, not rejected (#236)
      resources.py    — the WSL archive's resource ids (#412): the `committees:` / `committees-roster:` / `sponsors:` / `committee-members-hist:` prefixes and the members-hist id builder + parser. Pure; shared by `raw_harvest` and the pipeline's WSL staging
      parsing.py      — offline archived-wire parse seam (#306): module-level sync wrappers (`parse_committees`/`parse_sponsors`/`parse_committee_members`/`parse_committee_meetings`) over the per-service clients' offline binding replay, so `usa_wa_pipeline` staging depends on *parsing* without importing `transport` (the layer contract); empty wire (archived benign fault) → `[]`; one WSDL GET per service, amortized by module singletons
      raw_harvest.py  — **Phase A** (#304, `python -m usa_wa_adapter_legislature.raw_harvest`, slug `wsl-raw-harvest`, run by `pipeline-nightly.sh`): the biennium committee roster + active committees + full-biennium meeting window + sponsors, plus the per-committee `GetCommitteeMembers` fan-out, written as pristine wires to `raw/usa_wa_legislature/` under the archive's resource ids. Fan-out enumerates committees from the roster wire fetched in the SAME run, parsed offline through the SOAP binding — no DB anywhere; every call passes the central WSL rate limiter (#77); per-resource failures contained (`err` entries; a dead roster kills only the fan-out); an uncontained one (a `wire=None` transport-contract break) raises `JobFailure` with the counters reached (#331). `USA_WA_BIENNIUM` overrides the biennium
      provisioning.py — get-or-create the `usa_wa_legislature` SOAP `Source` + seed its claims on both paths. Called nightly by `usa_wa_pipeline.coverage_seed`, the only caller left
      coverage.py     — **the WSL source's declared coverage claims** (#180): `sponsor_roster` 1991-92→open **verified** (probed 2026-07-08, 1989-90 faults) + `committee_membership` 1999-00→open **assumed** (the `~1999-00` in #82 was never probed, and the transport swallows the sub-floor Fault to an empty roster, so a wrong bound fails *silently* — worth a probe to promote). One source, two dimensions, different floors. `WSL_SOURCE_SLUG` is the slug's one definition — every reader imports it, and `scripts/tests/test_source_slug_literals.py` fails on a retyped copy and pins the two dbt SQL/YAML sites that cannot import it (#245); `sponsors.probe_identity.DEFAULT_HISTORY_FLOOR` derives from `SPONSOR_ROSTER_COVERAGE`
      role_keys.py    — the party and committee `Member` Role `source_id`s (#412). Pure; the pipeline's conformed `roles` model mints them. The Senate seat key lives in `usa_wa_common.seats`
      member_rows.py  — `is_person` (#412): the name-blanked-stub screen every reader of the Member wire applies — the sponsor and committee projectors, roster hygiene and the identity probe
      probe_availability.py — write-free availability probe (#135 item 4, `python -m usa_wa_adapter_legislature.probe_availability --biennium B`, slug `wsl-availability-probe`, unit `usa-wa-wsl-availability-probe.timer` daily through 2027-02-28): `GetSponsors` by chamber, `GetCommittees` (`committees=null` while it **faults** — what WSL does for an unpublished biennium, unlike the past-floor "valid biennium" fault), and each committee's `GetCommitteeMembers`, appended to a JSONL log. **Exit 4 when a count changed state** — faulting, empty, has rows (the news, #237's convention; growth between published counts is logged, not mailed, CR 3); a first all-empty look is a baseline. `--until` closes the window (asks nothing after), `--dry-run` measures without logging. `needs_db=False`: no ledger row
      data/           — `initial_jurisdictions.json` (read by the 2026-06-03 jurisdictional-IA migration) (the frozen #39 Joint/`Other` committee seed lost its loader in #412 PR F and was deleted in #471)
      meetings/windows.py — biennium → (begin, end) window + `committee-meetings:<begin>:<end>` resource-id keying (#39); once-per-window cache key for docket frugality. Shared by `raw_harvest`, `committees.probe_extent` and the pipeline's WSL staging
      membership/projector.py — committee-roster → `Observation` projection (#82), pure; the pipeline's conformed spans build the committee family from it. See [MODULES-LEGISLATURE-SPANS.md](MODULES-LEGISLATURE-SPANS.md)
      sponsors/projector.py — sponsor-roster → party + Senate-seat `Observation`s (#78), pure; the pipeline's conformed sponsor family. Hygiene beside it (`roster_hygiene.py`, `artifacts.py`): [MODULES-LEGISLATURE-SPANS.md](MODULES-LEGISLATURE-SPANS.md)
      sponsors/probe_identity.py — write-free discovery CLI (P1b sub-project, #27 step 0, slug `wsl-member-identity-probe`): answers "is the WSL member `Id` a stable person key?" Talks to `WSLClient` **directly** (nothing archived); matches members **by name** (`LastName`,`FirstName` — deliberately not `Id`) across two axes and tallies `Id` agreement: cross-endpoint (`SponsorService.GetSponsors` vs `CommitteeService.GetActiveCommitteeMembers`) and cross-biennium (`GetSponsors(current)` vs `GetSponsors(prior)`). **Finding (2026-07-06): `Id` is stable across endpoint, biennium, AND chamber change (94/94 + 125/125, 0 divergences) → the person key is `GetSponsors.Id` (`usa_wa_legislature:<Id>` in the registry), no name-match fallback.** `GetSponsors` returns **one row per (member, chamber-tenure)**: a member appears once per tenure under a stable `Id`, so a mid-biennium House→Senate mover has two *named* rows (Alvarado `34024`, V. Hunt `35410`) and a boundary mover / departed member carries a **name-blanked stub** (`"Representative "`/`"Senator "`, null name/district/party — Orwall/Slatter House stubs, departed Hawkins/Hunt/Rivers), which `is_person` filters
      committees/probe_extent.py — write-free discovery CLI (#64, slug `wsl-committee-extent-probe`): walks bienniums backward from current calling `GetCommittees` + `GetCommitteeMeetings`, tallying committee/meeting counts + meeting wire bytes, stopping after N consecutive empty bienniums (`--max-empty`, default 2; bounded by `--max-bienniums`). Talks to `WSLClient` **directly** — nothing archived; answers "how much history exists". Also `probe_floor` — a **committee-only** backward walk (GetCommittees only, no slow meeting pulls) to the earliest biennium with data
      committees/succession_cli.py / committees/succession_store.py — the committee-lineage attestation CLI + store (usa-wa#124): [MODULES-LEGISLATURE-SPANS.md](MODULES-LEGISLATURE-SPANS.md)
      operators/      — the operator-succession CLI, store and raw-store archive (#107): [MODULES-LEGISLATURE-SPANS.md](MODULES-LEGISLATURE-SPANS.md)
      roster_pdf/     — **SOURCE 2** (#225, epic #219): the Legislature's own *Members of the Legislature 1889-2025* roster PDF. Its own transport/parser/raw harvest plus the pure pre-1991 identity and projection chain — see [MODULES-LEGISLATURE-ROSTER.md](MODULES-LEGISLATURE-ROSTER.md)
```
