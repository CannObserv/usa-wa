"""Keyset pagination for ``/api/v1`` (#184).

``persons`` and ``assignments`` grow without bound, so every list route pages.
The scheme is **keyset on the row's primary key**, not limit/offset: the PKs are
ULIDs (lexicographically ordered by creation time), so a cursor is a stable
resume point that neither skips nor repeats a row when the table is written
between two pages — the failure limit/offset has by construction.

No total count. A ``COUNT(*)`` over a growing table is exactly the query that
gets slow, and a stale or capped total is a worse contract than none.
"""

import pytest
from fastapi import HTTPException
from ulid import ULID

from usa_wa_api.api.v1.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Page,
    parse_ulid_cursor,
    take_page,
)


class _Row:
    def __init__(self, value: str) -> None:
        self.id = value


class TestTakePage:
    def test_short_page_has_no_next_cursor(self):
        rows = [_Row("a"), _Row("b")]
        page, cursor = take_page(rows, limit=5, key_of=lambda r: r.id)
        assert [r.id for r in page] == ["a", "b"]
        assert cursor is None

    def test_exactly_limit_rows_has_no_next_cursor(self):
        """The query fetches ``limit + 1``; only the overflow row proves more exist."""
        rows = [_Row("a"), _Row("b")]
        page, cursor = take_page(rows, limit=2, key_of=lambda r: r.id)
        assert len(page) == 2
        assert cursor is None

    def test_overflow_row_is_dropped_and_becomes_the_cursor_boundary(self):
        rows = [_Row("a"), _Row("b"), _Row("c")]
        page, cursor = take_page(rows, limit=2, key_of=lambda r: r.id)
        assert [r.id for r in page] == ["a", "b"]
        assert cursor == "b"


class TestParseULIDCursor:
    def test_none_passes_through(self):
        assert parse_ulid_cursor(None) is None

    def test_valid_cursor_parses(self):
        ulid = ULID()
        assert str(parse_ulid_cursor(str(ulid))) == str(ulid)

    def test_malformed_cursor_is_a_422_not_a_500(self):
        with pytest.raises(HTTPException) as exc:
            parse_ulid_cursor("not-a-ulid")
        assert exc.value.status_code == 422

    def test_uuid_hex_cursor_is_rejected(self):
        """A consumer that round-tripped an id through ``::text`` must fail loudly."""
        with pytest.raises(HTTPException) as exc:
            parse_ulid_cursor(str(ULID().to_uuid()))
        assert exc.value.status_code == 422


class TestPageEnvelope:
    def test_defaults_to_an_empty_item_list(self):
        page: Page[str] = Page(limit=DEFAULT_LIMIT)
        assert page.items == []
        assert page.next_cursor is None

    def test_cap_is_documented_and_above_the_default(self):
        assert DEFAULT_LIMIT == 50
        assert MAX_LIMIT == 200
