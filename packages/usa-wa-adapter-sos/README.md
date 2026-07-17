# usa-wa-adapter-sos

WA Secretary of State (`votewa` / `eledataweb.votewa.gov`) Layer-3 adapter.

Archives the general-election candidate-filing export (CSV) and supplies the House
`Position 1/2` qualifier that PDC's `Campaign Finance Summary` dataset did not record
before the 2018 election (see #98 / #100). PDC stays the *winner* authority; this adapter
contributes only the position qualifier, joined on `(LD, surname, party)`.

Broader candidate enrichment (contact/candidacy facts) is tracked in #99.
