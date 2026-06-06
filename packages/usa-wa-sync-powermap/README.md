# usa-wa-sync-powermap

usa-wa's deployment binding for the portable `clearinghouse-sync-powermap` engine.

Contains the concrete `EntityDescriptor`s that wire usa-wa's local tables
(`clearinghouse_core.jurisdictions`, `canonical.{persons,organizations,roles,assignments}`)
to Power Map, plus the long-running sidecar daemon + systemd unit.

Entity model (5 descriptors): jurisdictions, persons, organizations, roles,
assignments. Entity events are a person/org sub-resource (not a descriptor). See
`docs/specs/2026-06-02-power-map-sync-sidecar-design.md`.
