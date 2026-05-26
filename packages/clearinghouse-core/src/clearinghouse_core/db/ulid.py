"""SQLAlchemy column type for ULIDs.

Stores values as PostgreSQL native ``uuid`` (16 bytes); converts to/from
``ulid.ULID`` at the bind/result boundary. See `db/ulid.md` for the rationale.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import TypeDecorator
from ulid import ULID as _ULID


class ULID(TypeDecorator):
    """SQLAlchemy type: Python ``ulid.ULID`` ⇄ Postgres ``uuid``.

    Application code reads and writes :class:`ulid.ULID` instances. The bytes
    on disk are identical to the underlying UUID, preserving ULID's time-prefix
    ordering for B-tree indexes.
    """

    impl = PG_UUID(as_uuid=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> UUID | None:
        if value is None:
            return None
        if isinstance(value, _ULID):
            return value.to_uuid()
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            return _ULID.from_str(value).to_uuid()
        if isinstance(value, bytes):
            return _ULID.from_bytes(value).to_uuid()
        raise TypeError(f"Cannot adapt {type(value).__name__} to ULID column")

    def process_result_value(self, value: Any, dialect: Any) -> _ULID | None:
        if value is None:
            return None
        if isinstance(value, UUID):
            return _ULID.from_uuid(value)
        raise TypeError(f"Unexpected DB value type for ULID column: {type(value).__name__}")
