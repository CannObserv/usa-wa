"""Base ``EntityDescriptor._anchor_match`` — anchor lookup tolerant of a duplicate.

The one-local-row-per-PM-anchor invariant is enforced at the DB layer (partial
unique index, usa-wa#86). This helper is the read-side defense in depth: a
pre-index duplicate must NOT raise ``MultipleResultsFound`` (which would poison
the whole reconcile/feed apply path); it logs and returns a deterministic winner.
"""

from typing import Any

from ulid import ULID

from clearinghouse_sync_powermap.testing import FakeEntity


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Records the statement and returns preset rows — the anchor query never hits a DB."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _FakeResult:
        self.statements.append(statement)
        return _FakeResult(self._rows)


async def test_anchor_match_returns_none_without_id(fake_descriptor) -> None:
    assert await fake_descriptor._anchor_match(_FakeSession([]), {}) is None


async def test_anchor_match_returns_none_when_no_row(fake_descriptor) -> None:
    assert await fake_descriptor._anchor_match(_FakeSession([]), {"id": str(ULID())}) is None


async def test_anchor_match_returns_single_row(fake_descriptor) -> None:
    row = FakeEntity(source="wsl", source_id="A", name="a")
    result = await fake_descriptor._anchor_match(_FakeSession([row]), {"id": str(ULID())})
    assert result is row


async def test_anchor_match_tolerates_duplicate(fake_descriptor, caplog) -> None:
    """Two rows sharing an anchor → return the first (query orders newest-first),
    log ``anchor_invariant_violation`` with both source_ids, and do NOT raise."""
    winner = FakeEntity(source="wsl", source_id="NEW", name="new")
    loser = FakeEntity(source="wsl", source_id="OLD", name="old")
    session = _FakeSession([winner, loser])  # descriptor query orders newest-first

    with caplog.at_level("WARNING"):
        result = await fake_descriptor._anchor_match(session, {"id": str(ULID())})

    assert result is winner
    violation = next(r for r in caplog.records if r.msg == "anchor_invariant_violation")
    assert violation.anchor_column == "pm_fake_id"
    assert set(violation.source_ids) == {"NEW", "OLD"}
