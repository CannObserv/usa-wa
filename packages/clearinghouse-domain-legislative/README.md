# clearinghouse-domain-legislative

Legislative-government domain primitives for the CannObserv clearinghouse.

Provides:

- the biennium term calendar (`terms`)
- the tenure-span engine (`tenure_spans`, `span_kinds`, `seat_clipping`) — pure, no DB
- the operator-succession overlay (`operator_overlay`) and the two attestation models it and
  the lineage dataset read: `OperatorEvent` and `CommitteeSuccessionEvent`, both in the
  `registry.*` schema

The canonical entity models (persons, organizations, roles, assignments, bills, votes,
statutes, sessions) were deleted with the Postgres canonical tier in #412 PR F.

Reusable across state legislatures (WA, OR, …) and federal legislatures (US Congress). Municipal-government concepts (city councils, ordinances) belong in a future `clearinghouse-domain-municipal` package.
