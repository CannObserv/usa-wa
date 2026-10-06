# Commands — the roster PDF source

Split out of [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md); the index lives in
[COMMANDS.md](COMMANDS.md). The Legislature's own roster PDF (#225, epic #219): the raw-store
harvest (#421) and its monthly edition re-check (#237). The #226 succession backfill and the #228
pre-1991 build wrote the canonical tier and were deleted in #412 PR F; the pipeline now stages the
roster and derives its spans nightly ([PIPELINE.md](PIPELINE.md)). Module reference:
[MODULES-LEGISLATURE-ROSTER.md](MODULES-LEGISLATURE-ROSTER.md).

## Roster PDF — the archival member source (#225, epic #219)

The Legislature's own *Members of the Legislature 1889-2025* roster. **Not a sweep and not a
refresh**: the source publishes one document per revision (~biennially; 18 editions since 1962),
so the harvest archives exactly one resource and re-running is a cache hit.

```bash
# Phase A — archive one edition into the raw store the #302 pipeline stages from (#421)
uv run python -m usa_wa_adapter_legislature.roster_pdf.raw_harvest --revision 2025-06-05
```

It writes `raw/usa_wa_legislature_roster/` (`--root` or `USA_WA_RAW_ROOT` to override).
**Load the env first** (`export $(cat /etc/usa-wa/.env .env | xargs)`, which
sets `USA_WA_RAW_ROOT` to the prod store) **or run from the primary checkout**: the default root is
`raw/` under the cwd, so a worktree run lands the edition where the pipeline never looks and still
exits 0. The `roster_raw_harvest_complete` line names the absolute store it wrote. Roster staging parses the newest `legroster:` there, so the next nightly
publishes the edition. `--force` re-fetches past the 90-day freshness window; `--dry-run` fetches
and verifies the stamp but writes nothing; `--pause-seconds` sets the
`leg.wa.gov` courtesy limiter for the run (unset leaves `USA_WA_LEG_MIN_REQUEST_INTERVAL`, default
1.0s, in force — #236). Exit `0` clean · `1` failed · `2` config · **`4` degraded** — the document
could not be located (the CMS media key rotated *and* the href could not be re-discovered, so an
operator must re-point the source), a newer edition is published, or the document's `Revision
Date` could not be read (`unreadable=true`). An unreadable stamp archives nothing: it is the only
guard against a new edition landing under an old `--revision`.

Re-checked **monthly** by `usa-wa-roster-pdf-recheck.timer` (#237, 1st 09:00 UTC) — never in
the daily raw harvest. Closed history does not drift, and the edition lags the current biennium by
design, so it is never authority there. The pipeline parses **offline** from the raw store: revise
the parser and the next build re-parses without re-fetching 5.7MB.

The timer runs this same harvest `--dry-run --force`: one GET (~69MB/yr), the stamp verified
against `DEFAULT_REVISION` in `roster_pdf/edition.py`, nothing written. `--force` is
load-bearing — the 90-day freshness window would otherwise make the check a cache hit that never
fetches. When its `OnFailure=` email arrives, the summary line says which exit 4:

- **`mismatch=…`** — a new edition is published. Archive it with the `--revision` the message
  names, then bump `DEFAULT_REVISION` on `main`: the check compares against the code, so it
  alerts every month until both land. Audit the new edition before building on it (the
  `coverage.py` claim is closed at the old ceiling, and the parser has only seen this layout).
  The next nightly publishes it: staging reads the newest edition in the raw store.
- **`unavailable=true`** — the media key rotated and the href could not be re-discovered from
  the index page; re-point `DEFAULT_ROSTER_URL` in `roster_pdf/transport.py`.
- **`unreadable=true`** — the document's `Revision Date` no longer parses, most likely a new
  edition with a changed front-matter layout. The check cannot see editions until this is fixed:
  read the stamp off the PDF by hand, fix `extract_revision_date` in `roster_pdf/extraction.py`
  for the new layout, then treat it as `mismatch=…` if the edition is new.

The check compares the **stamp**, not the bytes: a re-upload that keeps the `Revision Date`
stays green. Exit `1` is an outage (a non-404 status, a timeout); the next month's run retries.
Re-check by hand with `sudo systemctl start usa-wa-roster-pdf-recheck.service`.
