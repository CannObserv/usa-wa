# usa-wa-adapter-sos

WA Secretary of State Layer-3 adapter: two sources, one target.

- **filings** (`usa_wa_sos`, `eledataweb.votewa.gov`) — the general-election candidate-filing
  export (CSV), 2008–2018; SOS retired it to Power BI for 2020+. Candidacy metadata only (#99).
- **results** (`usa_wa_sos_results`, `results.vote.wa.gov`) — the legislative results export,
  2008→present: the House `Position 1/2` qualifier (#101) plus vote counts, and the odd-year
  special winners.

`raw_harvest` archives both into the #304 raw store nightly; the #302 pipeline stages them and
seats the House Position from the results feed through `results.normalize`. The Postgres
adapters, harvests and archive refresh were deleted in #412 PR F. Module reference:
[`docs/MODULES-SOS.md`](../../docs/MODULES-SOS.md).
