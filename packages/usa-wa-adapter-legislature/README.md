# usa-wa-adapter-legislature

WA State Legislature SOAP adapter. Layer 3 of the clearinghouse architecture — maps `wslwebservices.leg.wa.gov` SOAP services to the canonical `clearinghouse-domain-legislative` entities.

Owns the `usa_wa_legislature.*` Postgres schema for raw + parsed source data.

Subclasses `clearinghouse_core.BaseAdapter`. SOAP transport (likely `zeep`) lands during P1a.
