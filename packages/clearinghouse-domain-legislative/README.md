# clearinghouse-domain-legislative

Legislative-government domain entities for the CannObserv clearinghouse.

Provides SQLAlchemy models for:

- Bill, BillSponsorship, BillAction, BillVersion
- Legislator, Committee, Hearing
- StatuteCode, StatuteTitle, StatuteChapter, StatuteSection, BillStatuteChange
- Filer, LobbyingActivity, LobbyingPosition, Contribution

All entities carry `jurisdiction_id` and live in the `canonical.*` schema.

Reusable across state legislatures (WA, OR, …) and federal legislatures (US Congress). Municipal-government concepts (city councils, ordinances) belong in a future `clearinghouse-domain-municipal` package.
