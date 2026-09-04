"""Keyset pagination for the ``/api/v1`` list routes (#184).

Every list route in this package pages, and pages the same way. ``persons`` and
``assignments`` are the two tables that grow without bound, but a route that is
small today is the one that quietly stops being small, so the scheme is applied
uniformly rather than where it currently hurts.

**Keyset, not limit/offset.** Both are one line of SQL; they differ under
concurrent writes. ``OFFSET n`` re-runs the whole ordering and skips *n* rows, so
a row inserted before the reader's position shifts every later page by one — the
reader silently skips a row (or sees one twice), and the cost of the skip grows
with the offset. A keyset predicate (``WHERE key > :cursor``) resumes from a
value rather than a position: stable under writes, and index-seekable at any
depth. The primary keys here are ULIDs, which sort lexicographically by creation
time, so the key is already the natural order for every canonical table.

**No total count.** ``COUNT(*)`` over a growing table is the query that gets
slow, and a capped or stale total is a worse contract than none. A consumer that
needs "is there more" reads ``next_cursor``.

**The cursor is opaque and route-scoped.** Most routes key on the row's own
identifier — a registry ULID, a structural ``role_key``, a ``job_slug``. Since
#313 ``/assignments`` keys on the five columns that *are* a span's identity, so
its cursor is those values encoded (:func:`encode_cursor`) rather than one
string. Each route documents its own ordering; a consumer must only ever echo a
``next_cursor`` back, never construct one.
"""

import base64
import binascii
from collections.abc import Callable, Sequence
from typing import Annotated

from fastapi import HTTPException, Query, status
from pydantic import BaseModel, Field
from ulid import ULID as _ULID

DEFAULT_LIMIT = 50
"""Rows per page when the caller does not say. Small enough to be cheap for an
interactive consumer, large enough that a roster fits in a couple of requests."""

MAX_LIMIT = 200
"""Hard cap. A caller asking for more gets a 422 rather than a silently truncated
page — a clamp would make ``len(items) < limit`` ambiguous with exhaustion."""

CURSOR_MAX_LENGTH = 512
"""Longest accepted cursor. The ULID routes need 26 and ``/health/jobs`` keys on
``job_slug`` (``String(128)``), but ``/assignments`` encodes five columns —
source, member id, kind, discriminator, start biennium — which base64 inflates
past 128. Sized for that rather than for the shortest case."""

_CURSOR_SEPARATOR = "\x1f"
"""ASCII unit separator: the one delimiter no key value can contain. A span's
member id carries colons (the roster family's ``<fold>:<year>``), which is
exactly the assumption a punctuation delimiter would break."""

LimitQuery = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_LIMIT,
        description=f"Rows per page (max {MAX_LIMIT}).",
    ),
]

CursorQuery = Annotated[
    str | None,
    Query(
        max_length=CURSOR_MAX_LENGTH,
        description=(
            "Opaque resume token from a previous response's `next_cursor`. "
            "Route-scoped — echo it back, never construct one."
        ),
    ),
]


class Page[T](BaseModel):
    """One page of a list route.

    ``next_cursor`` is ``None`` exactly when the page is the last one. It is the
    *only* signal of exhaustion: a short page also means exhausted, but a full page
    with no cursor does too, so consumers must branch on the cursor rather than on
    ``len(items)``.
    """

    items: list[T] = Field(default_factory=list)
    limit: int = Field(description="The page size actually applied.")
    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` to fetch the next page. `null` means no more rows.",
    )


def take_page[R](
    rows: Sequence[R], *, limit: int, key_of: Callable[[R], str]
) -> tuple[list[R], str | None]:
    """Split a ``limit + 1`` fetch into the page and its follow-on cursor.

    Over-fetching by exactly one row is what lets ``next_cursor`` be truthful
    without a second query: the extra row proves more exist, and is then discarded.
    Its absence is the only evidence of exhaustion — which is why a full page with
    no overflow row correctly yields ``None`` rather than a cursor that would
    return an empty page.
    """
    if len(rows) > limit:
        page = list(rows[:limit])
        return page, key_of(page[-1])
    return list(rows), None


def parse_ulid_cursor(cursor: str | None) -> _ULID | None:
    """Validate a ULID-keyed cursor at the request boundary.

    Without this the malformed value reaches ``ULID.from_str`` inside the query
    and surfaces as a 500. A cursor is caller-supplied input, so a bad one is a
    422. Notably this rejects the UUID-hex form: a consumer that round-tripped an
    id through a ``::text`` cast gets told, rather than getting an empty page.
    """
    if cursor is None:
        return None
    try:
        return _ULID.from_str(cursor)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="cursor must be a 26-character ULID",
        ) from exc


def encode_cursor(values: Sequence[str]) -> str:
    """Several key columns → one opaque cursor.

    Base64url over a unit-separated join. Opaque is the contract (see the module
    docstring), and encoding is what keeps it true: a cursor that looked like a
    readable tuple would invite consumers to build one, and the day a key column
    gains a part every hand-built cursor breaks.
    """
    joined = _CURSOR_SEPARATOR.join(values)
    return base64.urlsafe_b64encode(joined.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, *, arity: int) -> tuple[str, ...] | None:
    """A cursor back into its key columns, or ``None`` when there is no cursor.

    ``arity`` is checked, not assumed: a cursor from a different route decodes
    cleanly but means something else, and using it would resume the scan at an
    arbitrary point rather than failing. A 422 says so; a wrong page would not.
    """
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = base64.urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="cursor is not a token this API issued",
        ) from exc
    parts = tuple(values.split(_CURSOR_SEPARATOR))
    if len(parts) != arity:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"cursor carries {len(parts)} key parts; this route keys on {arity}",
        )
    return parts
