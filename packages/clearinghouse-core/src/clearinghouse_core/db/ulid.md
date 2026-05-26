# ADR: ULID storage representation

**Date:** 2026-05-26 · **Status:** decided · **Scope:** every PK and FK in the usa-wa schema family.

## Decision

Store ULIDs in PostgreSQL's native `UUID` column type. Python application code reads and writes `ulid.ULID` objects via a SQLAlchemy `TypeDecorator` (`clearinghouse_core.db.ulid.ULID`) that converts to/from `uuid.UUID` at the bind/result boundary. ULIDs and UUIDs are both 128-bit; the conversion is lossless via `ULID.to_uuid()` / `ULID.from_uuid(...)`.

## Rationale

`UUID` is 16 bytes on disk vs `text(26)`'s ~27 bytes (with PostgreSQL overhead). For million-row tables and frequent FK joins, the 40% size reduction translates into meaningfully tighter B-tree indexes and more pages in shared buffers. The ULID time prefix is preserved as the leading bytes of the UUID, so B-tree ordering remains time-sequential — the property that motivated picking ULIDs over UUIDv4 in the first place ([[feedback-db-ulid]]).

`UUID` also wins on psql ergonomics: rows display as `01918d27-2b53-7b8a-9c4d-0123456789ab` rather than a raw hex blob. Not as direct as Crockford-base32 ULIDs (`01HZ...`) but still trivially copy-pasteable. asyncpg has first-class UUID support — no JSON-style encoding overhead, no custom codec.

Rejected `text(26)` because the storage and index overhead compound across the canonical schema (every PK + every FK on every table), and the debug-ergonomics win — being able to type a literal ULID in a psql `WHERE` clause — is small once `psql \gset` or jq-piped queries take over. Rejected `BYTEA` because it's the same 16-byte footprint as `UUID` but with no native rendering and no asyncpg type affinity. Rejected the native `UUID` *with* a custom column type that renders Crockford-base32 in psql because the integration cost (server-side `pg_proc` or client-side format hook) is not justified by the readability delta.

## Implementation pointer

`packages/clearinghouse-core/src/clearinghouse_core/db/ulid.py` (lands in P0 step 4). Wraps `sqlalchemy.dialects.postgresql.UUID(as_uuid=True)` with bind/result conversion to `python-ulid`'s `ULID` type. All canonical tables import this and use it for `id` PKs and every `*_id` FK.
