"""Tests for the FastAPI lifespan — startup DB fingerprint wiring.

httpx's ``ASGITransport`` does not run lifespan events, so the ``client``
fixture never exercises this path. These drive the lifespan context manager
directly with the DB layer mocked, so no real connection (prod or test) is made.
"""

from __future__ import annotations

from usa_wa_api.api import main


class _FakeSession:
    def __init__(self, *, execute_error: Exception | None = None) -> None:
        self._execute_error = execute_error

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> object:
        if self._execute_error is not None:
            raise self._execute_error
        raise AssertionError("execute should be mocked away in this test")


async def test_lifespan_fingerprints_the_db_connection(monkeypatch):
    """Startup opens a session and logs the fingerprint with context='api'."""
    calls: list[tuple[object, str]] = []

    async def fake_fingerprint(session, *, context: str) -> None:
        calls.append((session, context))

    monkeypatch.setattr(main, "get_session_factory", lambda: _FakeSession)
    monkeypatch.setattr(main, "log_connection_fingerprint", fake_fingerprint)

    async with main.lifespan(main.app):
        pass

    assert [context for _, context in calls] == ["api"]


async def test_lifespan_survives_a_fingerprint_query_failure(monkeypatch):
    """A DB hiccup during the best-effort fingerprint SELECT must not block boot.

    Uses the real ``log_connection_fingerprint`` (which swallows errors) against
    a session whose ``execute`` raises — the lifespan must still complete.
    """

    def failing_factory() -> _FakeSession:
        return _FakeSession(execute_error=RuntimeError("connection reset"))

    monkeypatch.setattr(main, "get_session_factory", lambda: failing_factory)

    async with main.lifespan(main.app):
        pass  # no exception == boot proceeded despite the fingerprint failure
